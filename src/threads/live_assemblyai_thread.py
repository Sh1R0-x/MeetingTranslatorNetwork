from __future__ import annotations

from collections import OrderedDict
import queue
import re
import threading
import time
import traceback
import json
import io
import wave

from PyQt6.QtCore import QThread, pyqtSignal

from common import log_line
from config.secure_store import getsecret
from services.recorder_service import RecorderService


class LiveAssemblyAIThread(QThread):
    live_line = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, cfg: dict, recorder: RecorderService, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.recorder = recorder

        self._stop_flag = False
        self._ratecv_state = None
        self._debug = bool(self.cfg.get("debug", False))

        self.input_samplerate = int(self.recorder.participants_rate or 48000)
        self.input_channels = int(self.recorder.participants_channels or 2)

        self.target_sr = int(self.cfg.get("live_target_sr", 16000) or 16000)
        self.format_turns = bool(self.cfg.get("live_format_turns", True))
        self.api_host = (self.cfg.get("assemblyai_api_host") or "streaming.assemblyai.com").strip()
        self.min_chunk_ms = int(self.cfg.get("assemblyai_min_chunk_ms", 100) or 100)
        self.max_chunk_ms = int(self.cfg.get("assemblyai_max_chunk_ms", 500) or 500)
        # AssemblyAI expects audio chunks between 50 and 1000ms.
        if self.min_chunk_ms < 50:
            self.min_chunk_ms = 50
        if self.min_chunk_ms > 1000:
            self.min_chunk_ms = 1000
        if self.max_chunk_ms < self.min_chunk_ms:
            self.max_chunk_ms = self.min_chunk_ms
        if self.max_chunk_ms > 1000:
            self.max_chunk_ms = 1000
        self.enable_translation = bool(self.cfg.get("live_enable_translation", False))
        self.translate_model = str(self.cfg.get("live_translate_model") or "gpt-4o-mini")
        self.source_language = str(self.cfg.get("live_source_language") or "AUTO").lower()
        self.enable_speaker_labels = bool(self.cfg.get("live_speaker_labels", False))
        self.enable_delayed_speaker = bool(self.cfg.get("live_speaker_labels_delayed", False))
        self.speaker_window_s = float(self.cfg.get("live_speaker_window_s", 30) or 30)
        self.speaker_interval_s = float(self.cfg.get("live_speaker_interval_s", 15) or 15)
        if self.source_language == "fr":
            self.enable_translation = False
        self.translate_max_pending = int(self.cfg.get("live_translate_max_pending", 3) or 3)
        # NOTE: Don't debounce emissions here (it can drop short phrases).
        # Quality/latency is controlled via AssemblyAI end-of-turn detection params below.
        # Defaults chosen for a "good compromise" (stable sentences with ~1-2s typical delay).
        self.end_of_turn_confidence_threshold = float(self.cfg.get("assemblyai_end_of_turn_confidence_threshold", 0.60) or 0.60)
        self.min_end_of_turn_silence_when_confident = int(self.cfg.get("assemblyai_min_end_of_turn_silence_when_confident_ms", 750) or 750)
        self.max_turn_silence = int(self.cfg.get("assemblyai_max_turn_silence_ms", 2500) or 2500)

        # Clamp to safe ranges
        if self.end_of_turn_confidence_threshold < 0.0:
            self.end_of_turn_confidence_threshold = 0.0
        if self.end_of_turn_confidence_threshold > 1.0:
            self.end_of_turn_confidence_threshold = 1.0
        if self.min_end_of_turn_silence_when_confident < 0:
            self.min_end_of_turn_silence_when_confident = 0
        if self.max_turn_silence < 0:
            self.max_turn_silence = 0

        # Deduplicate within a single AssemblyAI turn (don't hide valid repeats across turns).
        self._last_emitted_turn_order: int | None = None
        self._last_emitted = ""
        self._last_emitted_norm = ""
        self._translate_q: "queue.Queue[object]" = queue.Queue()
        self._translate_thread: threading.Thread | None = None
        self._openai_client = None
        self._audio_seconds = 0.0
        self._dia_buffer = bytearray()
        self._dia_thread: threading.Thread | None = None
        self._dia_lock = threading.Lock()

        # Track what we already emitted for a given AssemblyAI turn_order (prevents duplicates).
        self._turn_text_norm: "OrderedDict[int, str]" = OrderedDict()

        # Sentence buffering: build full sentences (across turns) before emitting to the UI.
        self._sentence_buf = ""
        self._emit_seq = 0

    def stop(self):
        self._stop_flag = True
        # Flush any remaining buffered text as a last message (best-effort).
        try:
            if self._sentence_buf.strip():
                self._emit_seq += 1
                self._emit_text(self._emit_seq, self._sentence_buf.strip(), speaker=None)
                self._sentence_buf = ""
        except Exception:
            pass
        try:
            self.recorder.live_queue.put_nowait(None)
        except Exception:
            pass
        try:
            self._translate_q.put_nowait(None)
        except Exception:
            pass
        try:
            if self._dia_thread and self._dia_thread.is_alive():
                self._dia_thread.join(timeout=1.0)
        except Exception:
            pass

    def _emit_text(self, msg_id: int | None, text: str, speaker: str | None = None):
        t = (text or "").strip()
        if not t:
            return
        norm = self._norm_text(t)

        # Only dedupe if it's the same msg_id (don't hide valid repeats across messages).
        if msg_id is not None and msg_id == self._last_emitted_turn_order:
            if t == self._last_emitted:
                return
            # allow longer updates even if normalized text matches
            if norm and norm == self._last_emitted_norm:
                if len(t) <= len(self._last_emitted):
                    return

        self._last_emitted_turn_order = msg_id
        self._last_emitted = t
        self._last_emitted_norm = norm

        turn_prefix = f"[T:{msg_id}] " if msg_id is not None else ""
        prefix = f"[SPK:{speaker}] " if speaker else ""
        if self.source_language == "fr":
            self.live_line.emit(f"{turn_prefix}{prefix}[FR] {t}")
            return
        if self.enable_translation:
            # If translation backlog is high, emit EN immediately to keep live responsive.
            try:
                if self.translate_max_pending > 0 and self._translate_q.qsize() >= self.translate_max_pending:
                    self.live_line.emit(f"{turn_prefix}{prefix}[EN] {t}")
                    return
            except Exception:
                self.live_line.emit(f"{turn_prefix}{prefix}[EN] {t}")
                return
            try:
                self._translate_q.put_nowait((msg_id, t, speaker))
            except Exception:
                self.live_line.emit(f"{turn_prefix}{prefix}[EN] {t}")
        else:
            self.live_line.emit(f"{turn_prefix}{prefix}[EN] {t}")

    def _norm_text(self, text: str) -> str:
        t = (text or "").strip().lower()
        if not t:
            return ""
        t = re.sub(r"[^\w\s]", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _buffer_and_split_sentences(self, text: str) -> list[str]:
        """
        Buffer finalized turns and emit full sentences (one message per sentence).

        AssemblyAI can end a "turn" before the punctuation shows up; by buffering across turns,
        we get much cleaner 1-sentence bubbles without adding too much latency.
        """
        t = (text or "").strip()
        if not t:
            return []
        t = re.sub(r"\s+", " ", t).strip()
        if not t:
            return []

        if self._sentence_buf:
            self._sentence_buf = (self._sentence_buf.rstrip() + " " + t).strip()
        else:
            self._sentence_buf = t

        buf = self._sentence_buf.strip()
        if not buf:
            self._sentence_buf = ""
            return []

        # Split on strong punctuation. Keep the last fragment as remainder if it doesn't end the sentence.
        parts = re.split(r"(?<=[.!?])\s+", buf)
        out: list[str] = []

        if re.search(r"[.!?]$", buf):
            out = [p.strip() for p in parts if p.strip()]
            self._sentence_buf = ""
            return out

        for p in parts[:-1]:
            p = (p or "").strip()
            if not p:
                continue
            if not re.search(r"[\w\d]", p, flags=re.UNICODE):
                continue
            out.append(p)

        self._sentence_buf = (parts[-1] or "").strip()

        # If user said something very short (common in FR) and there is no punctuation, still emit it.
        # This keeps the UI responsive without spamming partial-word bubbles.
        if not out and self._sentence_buf:
            wc = len(self._sentence_buf.split())
            if wc <= 4:
                out.append(self._sentence_buf)
                self._sentence_buf = ""
            elif len(self._sentence_buf) > 240 or wc >= 30:
                out.append(self._sentence_buf)
                self._sentence_buf = ""

        return out

    def _start_translation_worker(self) -> bool:
        if not self.enable_translation:
            return False

        api_key = getsecret(self.cfg, "openai_api_key") or ""
        if not api_key:
            self.status.emit("⚠ OpenAI API key manquante (traduction live)")
            self.enable_translation = False
            return False

        try:
            from openai import OpenAI
        except Exception:
            self.status.emit("⚠ Dépendance OpenAI manquante (pip install openai)")
            self.enable_translation = False
            return False

        self._openai_client = OpenAI(api_key=api_key)

        def _worker():
            while not self._stop_flag:
                item = self._translate_q.get()
                if item is None:
                    break
                if isinstance(item, tuple):
                    if len(item) >= 3:
                        turn_order = item[0]
                        text = str(item[1]).strip()
                        speaker = item[2]
                    else:
                        turn_order = None
                        text = str(item[0]).strip()
                        speaker = item[1] if len(item) > 1 else None
                else:
                    turn_order = None
                    text = str(item).strip()
                    speaker = None
                if not text:
                    continue
                try:
                    t0 = time.time()
                    r = self._openai_client.chat.completions.create(
                        model=self.translate_model,
                        temperature=0,
                        messages=[
                            {"role": "user", "content": "Traduis en français naturel, sans ajouter de contexte :\n" + text},
                        ],
                    )
                    out = (r.choices[0].message.content or "").strip()
                    # Emit EN then FR together for reliable pairing
                    turn_prefix = f"[T:{turn_order}] " if turn_order is not None else ""
                    prefix = f"[SPK:{speaker}] " if speaker else ""
                    self.live_line.emit(f"{turn_prefix}{prefix}[EN] {text}")
                    if out:
                        self.live_line.emit(f"{turn_prefix}{prefix}[FR] {out}")
                except Exception:
                    # Fallback: still emit EN
                    turn_prefix = f"[T:{turn_order}] " if turn_order is not None else ""
                    prefix = f"[SPK:{speaker}] " if speaker else ""
                    self.live_line.emit(f"{turn_prefix}{prefix}[EN] {text}")

        self._translate_thread = threading.Thread(target=_worker, daemon=True)
        self._translate_thread.start()
        return True

    def _audio_iter(self):
        import audioop

        buf = bytearray()
        min_frames = max(1, int(self.target_sr * (self.min_chunk_ms / 1000.0)))
        max_frames = max(min_frames, int(self.target_sr * (self.max_chunk_ms / 1000.0)))
        min_bytes = min_frames * 2
        max_bytes = max_frames * 2

        while not self._stop_flag:
            try:
                item = self.recorder.live_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if item is None:
                break

            data_bytes, frames = item
            frames = int(frames) if frames else 0
            if not data_bytes or frames <= 0:
                continue

            if self.input_channels and self.input_channels != 1:
                try:
                    data_bytes = audioop.tomono(data_bytes, 2, 0.5, 0.5)
                except Exception:
                    pass

            if self.input_samplerate and self.input_samplerate != self.target_sr:
                try:
                    data_bytes, self._ratecv_state = audioop.ratecv(
                        data_bytes, 2, 1, self.input_samplerate, self.target_sr, self._ratecv_state
                    )
                except Exception:
                    pass

            if data_bytes:
                buf.extend(data_bytes)

            while len(buf) >= min_bytes:
                chunk = bytes(buf[:max_bytes])
                del buf[:len(chunk)]
                if chunk:
                    if self.enable_delayed_speaker:
                        with self._dia_lock:
                            self._dia_buffer.extend(chunk)
                            # keep last window seconds
                            max_bytes_window = int(self.speaker_window_s * self.target_sr) * 2
                            if len(self._dia_buffer) > max_bytes_window:
                                extra = len(self._dia_buffer) - max_bytes_window
                                del self._dia_buffer[:extra]
                        self._audio_seconds += len(chunk) / 2 / float(self.target_sr)
                    yield chunk

    def run(self):
        from assemblyai.streaming.v3 import (
            BeginEvent,
            StreamingClient,
            StreamingClientOptions,
            StreamingError,
            StreamingEvents,
            StreamingParameters,
            TerminationEvent,
            TurnEvent,
        )
        from assemblyai.streaming.v3 import models as aai_models

        try:
            api_key = getsecret(self.cfg, "assemblyai_api_key") or ""
            if not api_key:
                self.status.emit("⚠ AssemblyAI API key manquante (Setup)")
                return

            self._start_translation_worker()
            if self.enable_delayed_speaker:
                self._start_delayed_diarization_worker(api_key)

            def on_begin(_self, event: BeginEvent):
                self.status.emit(f"Live: AssemblyAI connecté ({event.id})")

            def on_turn(_self, event: TurnEvent):
                end_turn = bool(getattr(event, "end_of_turn", False))
                if not end_turn:
                    return
                t = (getattr(event, "transcript", "") or "").strip()
                if not t:
                    return

                turn_order = getattr(event, "turn_order", None)
                try:
                    if isinstance(turn_order, bool):
                        turn_order = None
                    elif turn_order is not None:
                        turn_order = int(turn_order)
                except Exception:
                    turn_order = None

                # Dedupe: AssemblyAI can emit the same finalized turn multiple times.
                if turn_order is not None:
                    norm_full = self._norm_text(t)
                    prev = self._turn_text_norm.get(turn_order)
                    if prev == norm_full and norm_full:
                        return
                    self._turn_text_norm[turn_order] = norm_full
                    while len(self._turn_text_norm) > 250:
                        try:
                            self._turn_text_norm.popitem(last=False)
                        except Exception:
                            break

                if self._debug:
                    try:
                        conf = getattr(event, "end_of_turn_confidence", None)
                        lang = getattr(event, "language_code", None)
                        log_line(
                            f"[AaiLive] turn={getattr(event,'turn_order',None)} end={end_turn} formatted={getattr(event,'turn_is_formatted',None)} conf={conf} lang={lang} text='{t[:200]}'"
                        )
                    except Exception:
                        pass

                # Build full sentences across turns for better readability (1 sentence = 1 bubble).
                chunks = self._buffer_and_split_sentences(t)
                for chunk in chunks:
                    chunk = (chunk or "").strip()
                    if not chunk:
                        continue
                    self._emit_seq += 1
                    self._emit_text(self._emit_seq, chunk, speaker=None)

            def on_terminated(_self, event: TerminationEvent):
                # Flush remainder if any.
                try:
                    if self._sentence_buf.strip():
                        self._emit_seq += 1
                        self._emit_text(self._emit_seq, self._sentence_buf.strip(), speaker=None)
                        self._sentence_buf = ""
                except Exception:
                    pass
                self.status.emit("Live: AssemblyAI terminé")

            def on_error(_self, error: StreamingError):
                self.status.emit(f"Live: AssemblyAI erreur ({error})")

            client = StreamingClient(
                StreamingClientOptions(
                    api_key=api_key,
                    api_host=self.api_host,
                )
            )

            client.on(StreamingEvents.Begin, on_begin)
            client.on(StreamingEvents.Turn, on_turn)
            client.on(StreamingEvents.Termination, on_terminated)
            client.on(StreamingEvents.Error, on_error)

            self.status.emit("Live: connexion AssemblyAI...")
            if self.source_language in ("fr", "auto"):
                speech_model = aai_models.SpeechModel.universal_streaming_multilingual
            else:
                speech_model = aai_models.SpeechModel.universal_streaming_english

            params = {
                "sample_rate": self.target_sr,
                "encoding": aai_models.Encoding.pcm_s16le,
                "format_turns": self.format_turns,
                "speech_model": speech_model,
                "end_of_turn_confidence_threshold": self.end_of_turn_confidence_threshold,
                "min_end_of_turn_silence_when_confident": self.min_end_of_turn_silence_when_confident,
                "max_turn_silence": self.max_turn_silence,
            }
            if self.source_language == "auto":
                params["language_detection"] = True
            try:
                client.connect(StreamingParameters(**params))
            except TypeError:
                # Back-compat: try progressively smaller parameter sets (SDK versions differ).
                try:
                    minimal = dict(params)
                    minimal.pop("end_of_turn_confidence_threshold", None)
                    minimal.pop("min_end_of_turn_silence_when_confident", None)
                    minimal.pop("max_turn_silence", None)
                    client.connect(StreamingParameters(**minimal))
                except TypeError:
                    client.connect(StreamingParameters(sample_rate=self.target_sr))

            self.status.emit("Live: prêt (AssemblyAI streaming)")
            try:
                client.stream(self._audio_iter())
            finally:
                client.disconnect(terminate=True)

        except Exception:
            err = traceback.format_exc()
            log_line("=== LiveAssemblyAIThread exception ===\n" + err)
            self.status.emit("Live: erreur AssemblyAI (voir log)")

    def _start_delayed_diarization_worker(self, api_key: str):
        def _worker():
            last_sent = 0.0
            while not self._stop_flag:
                time.sleep(self.speaker_interval_s)
                if self._stop_flag:
                    break
                with self._dia_lock:
                    if len(self._dia_buffer) == 0:
                        continue
                    window_bytes = bytes(self._dia_buffer)
                    window_dur = len(window_bytes) / 2 / float(self.target_sr)
                    if window_dur < self.speaker_window_s * 0.8:
                        continue
                    start_sec = max(0.0, self._audio_seconds - window_dur)
                if start_sec <= last_sent:
                    continue
                try:
                    utterances = self._run_async_speaker_labels(api_key, window_bytes)
                    if utterances:
                        payload = {"utterances": []}
                        for u in utterances:
                            payload["utterances"].append(
                                {
                                    "start": start_sec + (u.get("start", 0) / 1000.0),
                                    "end": start_sec + (u.get("end", 0) / 1000.0),
                                    "speaker": u.get("speaker", ""),
                                }
                            )
                        self.live_line.emit("[DIA] " + json.dumps(payload, ensure_ascii=False))
                    last_sent = start_sec
                except Exception:
                    continue

        self._dia_thread = threading.Thread(target=_worker, daemon=True)
        self._dia_thread.start()

    def _run_async_speaker_labels(self, api_key: str, wav_bytes: bytes):
        import requests

        def _make_wav_bytes(pcm: bytes, sr: int) -> bytes:
            bio = io.BytesIO()
            with wave.open(bio, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(pcm)
            return bio.getvalue()

        headers = {"authorization": api_key}
        upload_url = "https://api.assemblyai.com/v2/upload"
        transcript_url = "https://api.assemblyai.com/v2/transcript"

        # upload
        wav_data = _make_wav_bytes(wav_bytes, self.target_sr)
        r = requests.post(upload_url, headers=headers, data=wav_data, timeout=60)
        r.raise_for_status()
        upload = r.json()
        audio_url = upload.get("upload_url")
        if not audio_url:
            return []

        payload = {
            "audio_url": audio_url,
            "speaker_labels": True,
            "punctuate": True,
            "format_text": True,
        }
        if self.source_language in ("fr", "en"):
            payload["language_code"] = self.source_language

        r = requests.post(transcript_url, headers={"authorization": api_key, "content-type": "application/json"}, json=payload, timeout=60)
        r.raise_for_status()
        tid = r.json().get("id")
        if not tid:
            return []

        for _ in range(40):
            time.sleep(1.0)
            status_r = requests.get(f"{transcript_url}/{tid}", headers=headers, timeout=30)
            status_r.raise_for_status()
            data = status_r.json()
            status = data.get("status")
            if status == "completed":
                return data.get("utterances") or []
            if status == "error":
                return []
        return []
