from __future__ import annotations

import re
import wave
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import numpy as np
import sounddevice as sd
import pyaudiowpatch as pyaudio

from audio.wasapi_loopback import get_loopback_for_output

PART_SECONDS = 2 * 60 * 60  # 2 heures


def _safe_name(s: str) -> str:
    s = s.strip()
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
    def __init__(self, base_dir: Path, time_prefix: str, label: str, samplerate: int, channels: int):
        self.base_dir = base_dir
        self.time_prefix = time_prefix
        self.label = _safe_name(label)
        self.samplerate = int(samplerate)
        self.channels = int(channels)
        self.part_index = 1
        self.frames_written = 0
        self.wav: Optional[wave.Wave_write] = None
        self.paths: List[Path] = []
        self._open_new_part()

    def _open_new_part(self):
        if self.wav is not None:
            self.wav.close()

        part = f"Partie{self.part_index:02d}"
        filename = f"{self.time_prefix} - {self.label} - {part}.wav"
        path = self.base_dir / filename
        self.paths.append(path)

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
        if self.frames_written + frames >= PART_SECONDS * self.samplerate:
            self._open_new_part()
        self.wav.writeframes(data_bytes)
        self.frames_written += frames

    def close(self):
        if self.wav is not None:
            self.wav.close()
            self.wav = None


class RecorderService:
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

        # ✅ Nouveau dossier par défaut
        self.output_root = output_root or (Path.home() / "Documents" / "MeetingTranslatorNetwork")

        self.participants_label = participants_label
        self.my_audio_label = my_audio_label

        self._running = False
        self._q_part = queue.Queue()
        self._q_mic = queue.Queue()

        # LIVE (participants)
        self._q_live_part = None

        self.participants_rate: Optional[int] = None
        self.participants_channels: Optional[int] = None

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

    # IMPORTANT: signature simple (évite les soucis de crochets/quotes)
    def set_live_participants_queue(self, q):
        self._q_live_part = q

    def start(self):
        if self._running:
            return

        now = datetime.now()
        date_dir = _date_folder_name(now)
        time_prefix = _time_prefix(now)

        # 1 dossier = 1 session
        self.session_dir = self.output_root / date_dir / time_prefix
        self.session_dir.mkdir(parents=True, exist_ok=True)

        lb = get_loopback_for_output(self.participants_output_device_id)
        if not lb:
            raise RuntimeError("Loopback introuvable pour la sortie sélectionnée (participants).")

        part_rate = int(lb["defaultSampleRate"])
        part_channels = int(lb.get("maxInputChannels", 2)) or 2

        self.participants_rate = part_rate
        self.participants_channels = part_channels

        self._writer_part = _WavRotatingWriter(
            base_dir=self.session_dir,
            time_prefix=time_prefix,
            label=self.participants_label,
            samplerate=part_rate,
            channels=part_channels,
        )

        self._t_part = threading.Thread(target=self._writer_loop_participants, daemon=True)
        self._t_part.start()

        self._pa = pyaudio.PyAudio()

        def cb_part(in_data, frame_count, time_info, status):
            try:
                if in_data:
                    self._q_part.put((in_data, int(frame_count)))
                    if self._q_live_part is not None:
                        try:
                            self._q_live_part.put_nowait((in_data, int(frame_count)))
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

        mic_dev = sd.query_devices(self.mic_device_id, "input")
        mic_rate = int(mic_dev["default_samplerate"])
        mic_channels = 1

        self._writer_mic = _WavRotatingWriter(
            base_dir=self.session_dir,
            time_prefix=time_prefix,
            label=self.my_audio_label,
            samplerate=mic_rate,
            channels=mic_channels,
        )

        self._t_mic = threading.Thread(target=self._writer_loop_mic, daemon=True)
        self._t_mic.start()

        def cb_mic(indata, frames, time_info, status):
            try:
                mono = indata[:, 0] if indata.ndim > 1 else indata
                i16 = (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16)
                self._q_mic.put((i16.tobytes(), int(frames)))
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
        if not self._running:
            return

        self._running = False

        if self._sd_stream is not None:
            try:
                self._sd_stream.stop()
                self._sd_stream.close()
            except Exception:
                pass
            self._sd_stream = None

        if self._pa_stream is not None:
            try:
                self._pa_stream.stop_stream()
                self._pa_stream.close()
            except Exception:
                pass
            self._pa_stream = None

        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

        self._q_part.put(None)
        self._q_mic.put(None)

        if self._q_live_part is not None:
            try:
                self._q_live_part.put(None)
            except Exception:
                pass

        if self._t_part is not None:
            self._t_part.join(timeout=5)
        if self._t_mic is not None:
            self._t_mic.join(timeout=5)

        if self._writer_part is not None:
            self._writer_part.close()
            self.participants_track.wav_paths = list(self._writer_part.paths)

        if self._writer_mic is not None:
            self._writer_mic.close()
            self.my_track.wav_paths = list(self._writer_mic.paths)

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
