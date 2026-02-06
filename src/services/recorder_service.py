from __future__ import annotations

import queue
import re
import threading
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np
import sounddevice as sd
try:
    import pyaudiowpatch as pyaudio
except Exception:  # pragma: no cover - non-windows
    pyaudio = None

from audio.wasapi_loopback import get_loopback_for_output

PART_SECONDS = 2 * 60 * 60  # 2 heures


def _safe_name(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r'[<>:"/\\|?*\n\r\t]', "_", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _date_folder_name(dt: datetime) -> str:
    return dt.strftime("%d-%m-%Y")


def _time_prefix(dt: datetime) -> str:
    return dt.strftime("%Hh%Mm%Ss")


@dataclass
class TrackSpec:
    label: str
    wav_paths: List[Path]


class _WavRotatingWriter:
    def __init__(
        self,
        base_dir: Path,
        time_prefix: str,
        label: str,
        samplerate: int,
        channels: int,
        on_part_closed=None,
    ):
        self.base_dir = Path(base_dir)
        self.time_prefix = str(time_prefix)
        self.label = _safe_name(label)
        self.samplerate = int(samplerate)
        self.channels = int(channels)
        self._on_part_closed = on_part_closed

        self.part_index = 1
        self.frames_written = 0

        self.wav: Optional[wave.Wave_write] = None
        self.paths: List[Path] = []
        self._current_path: Optional[Path] = None
        self._emitted_paths = set()

        self._open_new_part()

    def _emit_closed(self, path: Optional[Path]):
        if not path:
            return
        if path in self._emitted_paths:
            return
        self._emitted_paths.add(path)
        if callable(self._on_part_closed):
            try:
                self._on_part_closed(path)
            except Exception:
                pass

    def _open_new_part(self):
        if self.wav is not None:
            try:
                self.wav.close()
            finally:
                self._emit_closed(self._current_path)

        part = f"Partie{self.part_index:02d}"
        filename = f"{self.time_prefix} - {self.label} - {part}.wav"
        path = self.base_dir / filename
        self.paths.append(path)
        self._current_path = path

        wf = wave.open(str(path), "wb")
        wf.setnchannels(self.channels)
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(self.samplerate)

        self.wav = wf
        self.frames_written = 0
        self.part_index += 1

    def write_frames_i16_bytes(self, data_bytes: bytes, frames: int):
        if self.wav is None:
            return

        frames = int(frames)
        if frames <= 0:
            return

        if self.frames_written + frames >= PART_SECONDS * self.samplerate:
            self._open_new_part()

        self.wav.writeframes(data_bytes)
        self.frames_written += frames

    def close(self):
        if self.wav is not None:
            try:
                self.wav.close()
            finally:
                self._emit_closed(self._current_path)
        self.wav = None


class RecorderService:
    """
    Enregistre 2 pistes:
    - Participants: loopback de la sortie Windows (WASAPI) en PCM16 via PyAudioWPatch
    - Micro: entrée micro via sounddevice (float32) convertie en PCM16

    En plus, tu peux brancher une queue live (participants ou micro) pour envoyer l'audio vers une transcription live.
    """

    def __init__(
        self,
        participants_output_device_id: int,
        mic_device_id: int,
        output_root: Optional[Path] = None,
        participants_label: str = "Audio des participants",
        my_audio_label: str = "Mon audio",
    ):
        self.participants_output_device_id = int(participants_output_device_id)
        self.mic_device_id = int(mic_device_id)

        # Dossier par défaut
        self.output_root = Path(output_root) if output_root else (Path.home() / "Documents" / "MeetingTranslatorNetwork")

        self.participants_label = participants_label
        self.my_audio_label = my_audio_label

        self._running = False

        self._q_part = queue.Queue()
        self._q_mic = queue.Queue()

        # LIVE: on met (bytes_pcm16, frames) ou None
        self._q_live_part = None
        self._q_live_mic = None

        self.participants_rate: Optional[int] = None
        self.participants_channels: Optional[int] = None
        self.mic_rate: Optional[int] = None
        self.mic_channels: Optional[int] = None

        self._writer_part: Optional[_WavRotatingWriter] = None
        self._writer_mic: Optional[_WavRotatingWriter] = None

        self._t_part: Optional[threading.Thread] = None
        self._t_mic: Optional[threading.Thread] = None

        self._pa: Optional[pyaudio.PyAudio] = None
        self._pa_stream = None
        self._sd_stream: Optional[sd.InputStream] = None

        self.session_dir: Optional[Path] = None

        self.participants_track = TrackSpec(label=self.participants_label, wav_paths=[])
        self.my_track = TrackSpec(label=self.my_audio_label, wav_paths=[])

        # Optional callbacks notified when a WAV part is closed.
        self._part_closed_callbacks = []

    def add_part_closed_callback(self, cb):
        if callable(cb):
            self._part_closed_callbacks.append(cb)

    def clear_part_closed_callbacks(self):
        self._part_closed_callbacks = []

    def _emit_part_closed(self, label: str, path: Path):
        for cb in list(self._part_closed_callbacks):
            try:
                cb(label, path)
            except Exception:
                pass

    def set_live_participants_queue(self, q):
        self._q_live_part = q

    def set_live_mic_queue(self, q):
        self._q_live_mic = q

    def start(self):
        if self._running:
            return

        if pyaudio is None:
            raise RuntimeError(
                "Capture participants (WASAPI loopback) indisponible sur cette plateforme. "
                "Utilise la version Windows pour l'enregistrement multi-pistes."
            )

        now = datetime.now()
        date_dir = _date_folder_name(now)
        time_prefix = _time_prefix(now)

        # 1 dossier = 1 session
        self.session_dir = self.output_root / date_dir / time_prefix
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # -------- Participants (loopback) --------
        lb = get_loopback_for_output(self.participants_output_device_id)
        if not lb:
            raise RuntimeError("Loopback introuvable pour la sortie sélectionnée (participants).")

        part_rate = int(lb["defaultSampleRate"])
        part_channels = int(lb.get("maxInputChannels", 2) or 2)

        self.participants_rate = part_rate
        self.participants_channels = part_channels

        self._writer_part = _WavRotatingWriter(
            base_dir=self.session_dir,
            time_prefix=time_prefix,
            label=self.participants_label,
            samplerate=part_rate,
            channels=part_channels,
            on_part_closed=lambda p: self._emit_part_closed(self.participants_label, p),
        )

        self._t_part = threading.Thread(target=self._writer_loop_participants, daemon=True)
        self._t_part.start()

        self._pa = pyaudio.PyAudio()

        def cb_part(in_data, frame_count, time_info, status):
            try:
                if in_data:
                    frames_i = int(frame_count)
                    self._q_part.put((in_data, frames_i))

                    if self._q_live_part is not None:
                        try:
                            self._q_live_part.put_nowait((in_data, frames_i))
                        except queue.Full:
                            pass
            except Exception:
                pass
            return (in_data, pyaudio.paContinue)

        self._pa_stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=part_channels,
            rate=part_rate,
            input=True,
            input_device_index=int(lb["index"]),
            frames_per_buffer=1024,
            stream_callback=cb_part,
        )
        self._pa_stream.start_stream()

        # -------- Micro (sounddevice) --------
        mic_dev = sd.query_devices(self.mic_device_id, "input")
        mic_rate = int(mic_dev["default_samplerate"])
        mic_channels = 1
        self.mic_rate = mic_rate
        self.mic_channels = mic_channels

        self._writer_mic = _WavRotatingWriter(
            base_dir=self.session_dir,
            time_prefix=time_prefix,
            label=self.my_audio_label,
            samplerate=mic_rate,
            channels=mic_channels,
            on_part_closed=lambda p: self._emit_part_closed(self.my_audio_label, p),
        )

        self._t_mic = threading.Thread(target=self._writer_loop_mic, daemon=True)
        self._t_mic.start()

        def cb_mic(indata, frames, time_info, status):
            try:
                mono = indata[:, 0] if getattr(indata, "ndim", 1) > 1 else indata
                i16 = (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16)
                self._q_mic.put((i16.tobytes(), int(frames)))
                if self._q_live_mic is not None:
                    try:
                        self._q_live_mic.put_nowait((i16.tobytes(), int(frames)))
                    except queue.Full:
                        pass
            except Exception:
                pass

        self._sd_stream = sd.InputStream(
            device=self.mic_device_id,
            channels=mic_channels,
            samplerate=mic_rate,
            blocksize=1024,
            latency="high",
            dtype="float32",
            callback=cb_mic,
        )
        self._sd_stream.start()

        self._running = True

    def stop(self):
        """
        Stop "durci" pour éviter les freezes drivers (WASAPI / sounddevice / pyaudio).
        + IMPORTANT: ne jamais bloquer sur la live queue (maxsize).
        """
        import time

        if not self._running:
            return

        t0 = time.perf_counter()
        print("[Recorder.stop] BEGIN")

        self._running = False

        def _call_with_timeout(fn, timeout_sec: float, label: str):
            done = {"err": None}

            def _run():
                try:
                    fn()
                except Exception as e:
                    done["err"] = repr(e)

            th = threading.Thread(target=_run, daemon=True)
            th.start()
            th.join(timeout=timeout_sec)

            if th.is_alive():
                print(f"[Recorder.stop] TIMEOUT on {label} after {timeout_sec}s (continue)")
                return False

            if done["err"] is not None:
                print(f"[Recorder.stop] ERROR on {label}: {done['err']}")
            else:
                print(f"[Recorder.stop] OK {label}")
            return True

        # ---- Stop sounddevice mic ----
        if self._sd_stream is not None:
            s = self._sd_stream
            print("[Recorder.stop] mic: stop/close start")
            if hasattr(s, "abort"):
                _call_with_timeout(lambda: s.abort(), 2.0, "sd_stream.abort")
            else:
                _call_with_timeout(lambda: s.stop(), 2.0, "sd_stream.stop")
            _call_with_timeout(lambda: s.close(), 2.0, "sd_stream.close")
            self._sd_stream = None
            print("[Recorder.stop] mic: stop/close done")
        else:
            print("[Recorder.stop] mic: no stream")

        # ---- Stop pyaudio loopback ----
        if self._pa_stream is not None:
            ps = self._pa_stream
            print("[Recorder.stop] loopback: stop/close start")

            def _stop_stream():
                try:
                    if hasattr(ps, "is_active"):
                        if ps.is_active():
                            ps.stop_stream()
                        else:
                            ps.stop_stream()
                    else:
                        ps.stop_stream()
                except Exception:
                    pass

            _call_with_timeout(_stop_stream, 2.0, "pa_stream.stop_stream")
            _call_with_timeout(lambda: ps.close(), 2.0, "pa_stream.close")
            self._pa_stream = None
            print("[Recorder.stop] loopback: stop/close done")
        else:
            print("[Recorder.stop] loopback: no stream")

        # ---- Terminate pyaudio ----
        if self._pa is not None:
            pa = self._pa
            print("[Recorder.stop] pyaudio: terminate start")
            _call_with_timeout(lambda: pa.terminate(), 2.0, "pyaudio.terminate")
            self._pa = None
            print("[Recorder.stop] pyaudio: terminate done")
        else:
            print("[Recorder.stop] pyaudio: no instance")

        # ---- Stop writer threads ----
        print("[Recorder.stop] writers: send sentinel")
        try:
            self._q_part.put(None)
        except Exception:
            pass
        try:
            self._q_mic.put(None)
        except Exception:
            pass

        # IMPORTANT: live queue can be full -> NEVER BLOCK
        if self._q_live_part is not None:
            try:
                self._q_live_part.put_nowait(None)
                print("[Recorder.stop] live: sentinel queued (nowait)")
            except queue.Full:
                # live thread already stopped; sentinel not mandatory
                print("[Recorder.stop] live: queue full -> sentinel skipped")
        if self._q_live_mic is not None:
            try:
                self._q_live_mic.put_nowait(None)
                print("[Recorder.stop] live mic: sentinel queued (nowait)")
            except queue.Full:
                print("[Recorder.stop] live mic: queue full -> sentinel skipped")

        print("[Recorder.stop] writers: join threads")
        try:
            if self._t_part is not None:
                self._t_part.join(timeout=5)
        except Exception:
            pass
        try:
            if self._t_mic is not None:
                self._t_mic.join(timeout=5)
        except Exception:
            pass

        # ---- Close writers and expose file paths ----
        print("[Recorder.stop] writers: close files")
        try:
            if self._writer_part is not None:
                self._writer_part.close()
                self.participants_track.wav_paths = list(self._writer_part.paths)
        except Exception as e:
            print(f"[Recorder.stop] writer_part close error: {e!r}")

        try:
            if self._writer_mic is not None:
                self._writer_mic.close()
                self.my_track.wav_paths = list(self._writer_mic.paths)
        except Exception as e:
            print(f"[Recorder.stop] writer_mic close error: {e!r}")

        dt = time.perf_counter() - t0
        print(f"[Recorder.stop] END ({dt:.3f}s)")

    def _writer_loop_participants(self):
        while True:
            item = self._q_part.get()
            if item is None:
                break
            data_bytes, frames = item
            if self._writer_part is not None:
                self._writer_part.write_frames_i16_bytes(data_bytes, int(frames))

    def _writer_loop_mic(self):
        while True:
            item = self._q_mic.get()
            if item is None:
                break
            data_bytes, frames = item
            if self._writer_mic is not None:
                self._writer_mic.write_frames_i16_bytes(data_bytes, int(frames))
