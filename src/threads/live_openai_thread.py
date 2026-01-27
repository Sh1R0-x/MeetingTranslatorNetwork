from __future__ import annotations

import queue
import traceback

from PyQt5.QtCore import QThread, pyqtSignal

from common import log_line
from config.secure_store import getsecret
from services.live_openai_realtime_transcribe import LiveOpenAIRealtimeTranscribeV2
from services.recorder_service import RecorderService


class LiveOpenAIThread(QThread):
    live_line = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, cfg: dict, recorder: RecorderService, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.recorder = recorder

        self._stop_flag = False
        self._transcriber: LiveOpenAIRealtimeTranscribeV2 | None = None

        self.commit_every_seconds = float(self.cfg.get("live_commit_every", 1.0))
        self.input_samplerate = int(self.recorder.participants_rate or 48000)
        self.input_channels = int(self.recorder.participants_channels or 2)

    def stop(self):
        self._stop_flag = True
        try:
            if self._transcriber:
                self._transcriber.stop()
        except Exception:
            pass

    def _on_transcript(self, lang: str, src_text: str, fr_text: str):
        if fr_text:
            line = f"[{lang}] {fr_text}"
        else:
            line = f"[{lang}] {src_text}"
        self.live_line.emit(line)

    def run(self):
        import asyncio

        async def _runner():
            api_key = getsecret(self.cfg, "openai_api_key") or ""
            if not api_key:
                self.status.emit("⚠ OpenAI API key manquante (Setup)")
                return

            self.status.emit("Live: connexion OpenAI Realtime...")
            self._transcriber = LiveOpenAIRealtimeTranscribeV2(
                api_key=api_key,
                input_samplerate=self.input_samplerate,
                input_channels=self.input_channels,
                commit_every_seconds=self.commit_every_seconds,
                source_language=(self.cfg.get("live_source_language") or "auto"),
                on_transcript=self._on_transcript,
            )

            try:
                await self._transcriber.start()
            except Exception as e:
                self.status.emit(f"Live: erreur connexion ({e})")
                return

            self.status.emit("Live: prêt (streaming audio)")

            frames_since_commit = 0

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

                # --- Conversion Realtime ---
                # OpenAI Realtime est plus stable avec du PCM16 MONO 24kHz.
                import audioop

                target_sr = 24000
                target_ch = 1

                if self.input_channels and self.input_channels != target_ch:
                    try:
                        data_bytes = audioop.tomono(data_bytes, 2, 0.5, 0.5)
                    except Exception:
                        pass

                if not hasattr(self, "_ratecv_state"):
                    self._ratecv_state = None

                if self.input_samplerate and self.input_samplerate != target_sr:
                    try:
                        data_bytes, self._ratecv_state = audioop.ratecv(
                            data_bytes, 2, 1, self.input_samplerate, target_sr, self._ratecv_state
                        )
                    except Exception:
                        pass

                frames_out = len(data_bytes) // 2  # PCM16 mono

                await self._transcriber.append_audio_pcm16(data_bytes)
                frames_since_commit += frames_out

                if target_sr > 0:
                    seconds = frames_since_commit / float(target_sr)
                    if seconds >= self.commit_every_seconds:
                        await self._transcriber.commit_audio()
                        frames_since_commit = 0

            try:
                await self._transcriber.stop_async()
            except Exception:
                pass

            self.status.emit("Live: arrêté")

        try:
            asyncio.run(_runner())
        except Exception:
            err = traceback.format_exc()
            log_line("=== LiveOpenAIThread exception ===\n" + err)
            self.status.emit("Live: erreur (voir log)")
