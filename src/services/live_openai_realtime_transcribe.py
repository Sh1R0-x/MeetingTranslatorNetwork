from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Callable, Optional

import websockets
from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.secure_store import loadconfig, getsecret


class LiveOpenAIRealtimeTranscribeV2:
    """
    Client OpenAI Realtime (WebSocket) pour transcription LIVE.

    Points importants (fiabilité) :
    - Envoyer de l'audio en PCM16 MONO 24 kHz.
      La conversion (stéréo->mono + resample->24k) est faite côté main.py avant l'envoi.
    - Pour obtenir des transcriptions "final", il faut :
        * Server VAD (turn_detection=server_vad) OU
        * input_audio_buffer.commit() (manuel)
      Ici on active Server VAD par défaut, et on garde commit_audio() en fallback.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        realtime_model: str = "gpt-4o-realtime-preview-2024-12-26",
        transcribe_model: str = "gpt-4o-mini-transcribe",
        translate_model: str = "gpt-4o-mini",
        source_language: str = "auto",  # auto, fr, en
        enable_server_vad: bool = True,
    ):
        if api_key:
            self.api_key = api_key
        else:
            cfg = loadconfig()
            self.api_key = getsecret(cfg, "openai_api_key") or ""

        if not self.api_key:
            raise ValueError("OpenAI API key manquante (Setup -> OpenAI API Key).")

        self.realtime_model = realtime_model
        self.transcribe_model = transcribe_model
        self.translate_model = translate_model
        self.source_language = (source_language or "auto").lower().strip()
        self.enable_server_vad = bool(enable_server_vad)

        self.client = OpenAI(api_key=self.api_key)

        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected = False

        # Callbacks
        # on_transcript(lang, src_text, fr_text)
        self.on_transcript: Optional[Callable[[str, Optional[str], str], None]] = None
        self.on_partial: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None

    async def connect(self) -> bool:
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "OpenAI-Beta": "realtime=v1",
            }

            uri = f"wss://api.openai.com/v1/realtime?model={self.realtime_model}"
            self.ws = await websockets.connect(
                uri,
                subprotocols=["realtime"],
                extra_headers=headers,
                ping_interval=20,
                ping_timeout=20,
            )
            self.is_connected = True

            transcription_cfg = {"model": self.transcribe_model}
            if self.source_language in ("fr", "en"):
                transcription_cfg["language"] = self.source_language

            # IMPORTANT: pcm16 => 24kHz mono (côté envoi)
            session = {
                "modalities": ["text", "audio"],
                "input_audio_format": "pcm16",
                "input_audio_transcription": transcription_cfg,
            }
            if self.enable_server_vad:
                session["turn_detection"] = {"type": "server_vad"}

            await self.ws.send(json.dumps({"type": "session.update", "session": session}))
            return True

        except Exception as e:
            self.is_connected = False
            self.ws = None
            if self.on_error:
                self.on_error(f"[Realtime] Connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        self.ws = None
        self.is_connected = False

    async def append_audio_pcm16(self, audio_bytes_pcm16: bytes) -> None:
        """Envoie un chunk PCM16 (MONO 24 kHz idéalement)."""
        if not self.is_connected or not self.ws:
            return
        if not audio_bytes_pcm16:
            return
        try:
            b64 = base64.b64encode(audio_bytes_pcm16).decode("ascii")
            await self.ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": b64}))
        except Exception as e:
            if self.on_error:
                self.on_error(f"[Realtime] append_audio failed: {e}")

    async def commit_audio(self) -> None:
        """Fallback si VAD off : commit pour déclencher une transcription."""
        if not self.is_connected or not self.ws:
            return
        try:
            await self.ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        except Exception as e:
            if self.on_error:
                self.on_error(f"[Realtime] commit_audio failed: {e}")

    async def listen(self) -> None:
        """Boucle de réception des events serveur."""
        if not self.is_connected or not self.ws:
            return

        try:
            async for msg_str in self.ws:
                try:
                    msg = json.loads(msg_str)
                except Exception:
                    continue

                t = msg.get("type", "")

                if t == "error":
                    err = msg.get("error") or {}
                    message = err.get("message") or str(err) or "Unknown error"
                    if self.on_error:
                        self.on_error(f"[Realtime] {message}")
                    continue

                if t == "conversation.item.input_audio_transcription.delta":
                    transcript = (msg.get("transcript") or "").strip()
                    if transcript and self.on_partial:
                        self.on_partial(transcript)
                    continue

                if t == "conversation.item.input_audio_transcription.completed":
                    transcript = (msg.get("transcript") or "").strip()
                    if transcript:
                        await self._process_final_transcript(transcript)
                    continue

        except Exception as e:
            if self.on_error:
                self.on_error(f"[Realtime] listen error: {e}")
        finally:
            self.is_connected = False

    async def _process_final_transcript(self, text: str) -> None:
        try:
            if self.source_language == "fr":
                if self.on_transcript:
                    self.on_transcript("FR", None, text)
                return

            if self.source_language == "en":
                fr = self._translate_en_to_fr(text)
                if self.on_transcript:
                    self.on_transcript("EN", text, fr)
                return

            detected = self._detect_lang_en_fr(text)
            if detected == "EN":
                fr = self._translate_en_to_fr(text)
                if self.on_transcript:
                    self.on_transcript("EN", text, fr)
            else:
                if self.on_transcript:
                    self.on_transcript("FR", None, text)
        except Exception as e:
            if self.on_error:
                self.on_error(f"[Realtime] process transcript error: {e}")

    def _detect_lang_en_fr(self, text: str) -> str:
        try:
            r = self.client.chat.completions.create(
                model=self.translate_model,
                temperature=0,
                messages=[
                    {"role": "user", "content": "Réponds uniquement par EN ou FR. Langue de ce texte :\n" + text},
                ],
            )
            out = (r.choices[0].message.content or "").strip().upper()
            return "EN" if out.startswith("EN") else "FR"
        except Exception:
            return "FR"

    def _translate_en_to_fr(self, en_text: str) -> str:
        try:
            r = self.client.chat.completions.create(
                model=self.translate_model,
                temperature=0,
                messages=[
                    {"role": "user", "content": "Traduis en français naturel, sans ajouter de contexte :\n" + en_text},
                ],
            )
            return (r.choices[0].message.content or "").strip()
        except Exception:
            return en_text
