from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

import requests
import websocket
from PyQt5.QtCore import QThread, pyqtSignal


@dataclass
class LiveOpenAIRealtimeConfig:
    # Transcription
    model: str = "gpt-4o-mini-transcribe"  # meilleur streaming delta que whisper-1
    language: str = "auto"  # "auto" | "fr" | "en"
    commit_ms: int = 1500  # on commit souvent => texte quasi continu

    # Traduction
    translate_to_fr: bool = True
    translation_model: str = "gpt-4o-mini"


class LiveOpenAIRealtimeThread(QThread):
    new_line = pyqtSignal(str)
    status = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        audio_q: "queue.Queue[Optional[Tuple[bytes, int]]]",
        input_samplerate: int,
        input_channels: int,
        openai_api_key: str,
        cfg: Optional[LiveOpenAIRealtimeConfig] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.audio_q = audio_q
        self.input_samplerate = int(input_samplerate)
        self.input_channels = int(input_channels)
        self.openai_api_key = (openai_api_key or "").strip()
        self.cfg = cfg or LiveOpenAIRealtimeConfig()

        self._stop_evt = threading.Event()
        self._ws = None
        self._ws_open = threading.Event()

        # cache anti spam
        self._last_src = ""
        self._last_fr = ""

    def stop(self):
        self._stop_evt.set()
        try:
            self.audio_q.put_nowait(None)
        except Exception:
            pass
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def _translate_to_fr(self, text: str) -> str:
        if not self.cfg.translate_to_fr:
            return ""

        txt = (text or "").strip()
        if not txt:
            return ""

        # Astuce simple: on demande "si déjà FR, renvoie inchangé"
        prompt = (
            "Tu es un traducteur.\n"
            "Traduis ce texte en français.\n"
            "Si le texte est déjà en français, renvoie exactement le même texte sans le modifier.\n\n"
            f"TEXTE:\n{txt}"
        )

        try:
            r = requests.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.cfg.translation_model,
                    "input": prompt,
                },
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()

            # extraction best-effort du texte
            out = ""
            for item in data.get("output", []) or []:
                for c in item.get("content", []) or []:
                    if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                        out = (c.get("text") or "").strip()
                        if out:
                            return out
            return ""
        except Exception:
            return ""

    def _emit_src_fr(self, src: str, fr: str):
        ts = datetime.now().strftime("%H:%M:%S")
        if src:
            self.new_line.emit(f"{ts} | SRC: {src}")
        if fr:
            self.new_line.emit(f"{ts} | FR:  {fr}")
        self.new_line.emit("")

    def run(self):
        if not self.openai_api_key:
            self.error.emit("OpenAI API Key manquante (Setup -> Clés API).")
            return

        # URL & headers OpenAI Realtime (transcription)
        # URL: wss://api.openai.com/v1/realtime?intent=transcription
        # Headers: Authorization + OpenAI-Beta: realtime=v1
        url = "wss://api.openai.com/v1/realtime?intent=transcription"
        headers = [
            "Authorization: Bearer " + self.openai_api_key,
            "OpenAI-Beta: realtime=v1",
        ]

        def on_open(ws):
            self._ws_open.set()
            self.status.emit("Live OpenAI: connecté.")

            lang = "" if self.cfg.language == "auto" else self.cfg.language

            # On désactive la VAD serveur (turn_detection=null) et on commit nous-mêmes souvent
            # => ça donne un rendu plus "continu".
            msg = {
                "type": "transcription_session.update",
                "input_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": self.cfg.model,
                    "prompt": "",
                    "language": lang,
                },
                "turn_detection": None,
                "input_audio_noise_reduction": {"type": "near_field"},
            }
            try:
                ws.send(json.dumps(msg))
            except Exception as e:
                self.error.emit(str(e))

        def on_message(_ws, message):
            try:
                data = json.loads(message)
            except Exception:
                return

            t = data.get("type", "")

            # Le guide indique de lire delta + completed
            # delta = incrémental pour gpt-4o-transcribe / gpt-4o-mini-transcribe
            # completed = phrase finalisée
            if t == "conversation.item.input_audio_transcription.completed":
                src = (data.get("transcript") or "").strip()
                if not src:
                    return

                # anti doublons
                if src == self._last_src:
                    return
                self._last_src = src

                fr = self._translate_to_fr(src).strip()
                if fr and fr == self._last_fr:
                    fr = ""
                if fr:
                    self._last_fr = fr

                self._emit_src_fr(src, fr)
                return

            if t == "error":
                err = data.get("error", {}).get("message") or str(data)
                self.error.emit(err)
                return

        def on_error(_ws, err):
            self.error.emit(str(err))

        def on_close(_ws, status_code, msg):
            self.status.emit("Live OpenAI: déconnecté.")

        self._ws = websocket.WebSocketApp(
            url,
            header=headers,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        t_ws = threading.Thread(target=lambda: self._ws.run_forever(), daemon=True)
        t_ws.start()

        if not self._ws_open.wait(timeout=10):
            self.error.emit("Impossible de se connecter au serveur OpenAI Realtime.")
            return

        last_commit = time.monotonic()

        # loop audio -> input_audio_buffer.append + commit régulier
        while not self._stop_evt.is_set():
            item = self.audio_q.get()
            if item is None:
                break

            in_data, _frame_count = item
            if not in_data:
                continue

            try:
                audio_b64 = base64.b64encode(in_data).decode("utf-8")
                self._ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64}))
            except Exception:
                pass

            now = time.monotonic()
            if (now - last_commit) * 1000.0 >= float(self.cfg.commit_ms):
                last_commit = now
                try:
                    self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                except Exception:
                    pass

        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass
