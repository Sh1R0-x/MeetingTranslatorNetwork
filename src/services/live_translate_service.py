from __future__ import annotations

import base64
import json
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

# Traduction locale (on garde votre base : Marian si dispo sinon Argos)
try:
    import torch
    from transformers import MarianMTModel, MarianTokenizer
except Exception:
    torch = None
    MarianMTModel = None
    MarianTokenizer = None

try:
    from argostranslate import translate as argos_translate
except Exception:
    argos_translate = None

# Realtime WS (OpenAI)
try:
    import websocket  # websocket-client
except Exception:
    websocket = None


def _to_mono_float32_i16_pcm(raw_i16: np.ndarray, channels: int) -> np.ndarray:
    """Convert PCM16 to mono float32 [-1, 1] - numpy 1.24.3 compatible"""
    if channels <= 1:
        mono = raw_i16.astype(np.float32)
    else:
        frames = raw_i16.size // channels
        data = raw_i16[: frames * channels].reshape(frames, channels).astype(np.float32)
        mono = data.mean(axis=1)
    return (mono / 32768.0).astype(np.float32)


def _resample_linear(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Resample audio - numpy 1.24.3 compatible (no copy parameter)"""
    if src_sr == dst_sr or x.size < 2:
        return x.astype(np.float32)
    n = int(round(x.size * (dst_sr / float(src_sr))))
    n = max(n, 2)
    xp = np.arange(x.size, dtype=np.float32)
    fp = x.astype(np.float32)
    x_new = np.linspace(0, x.size - 1, num=n, dtype=np.float32)
    return np.interp(x_new, xp, fp).astype(np.float32)


def _float32_to_i16_bytes(x: np.ndarray) -> bytes:
    """Convert float32 to PCM16 bytes"""
    x = np.clip(x, -1.0, 1.0)
    y = (x * 32767.0).astype(np.int16)
    return y.tobytes()


def _looks_english(text: str) -> bool:
    """Heuristique simple pour éviter de traduire du FR avec un modèle EN->FR."""
    t = (text or "").strip().lower()
    if not t:
        return False

    accents = "àâäçéèêëîïôöùûüÿœ"
    if sum(1 for c in t if c in accents) >= 2:
        return False

    en_hits = 0
    fr_hits = 0

    en_words = {"the", "and", "to", "of", "is", "are", "you", "we", "i", "it", "this", "that", "in", "for", "on"}
    fr_words = {"le", "la", "les", "des", "de", "du", "et", "est", "sont", "vous", "nous", "je", "tu", "dans", "pour", "sur"}

    words = [w.strip(".,;:!?()[]{}") for w in t.split()]
    for w in words[:30]:
        if w in en_words:
            en_hits += 1
        if w in fr_words:
            fr_hits += 1

    return en_hits > fr_hits


@dataclass
class LiveTranslateConfig:
    # Champs gardés pour compatibilité avec votre main.py
    src_lang: str = "en"
    tgt_lang: str = "fr"
    model_size: str = "base"
    compute_type: str = "float32"
    device: str = "cpu"

    # Traduction locale (EN->FR)
    translation_engine: str = "marian"  # "marian" | "argos"
    marian_model_name: str = "Helsinki-NLP/opus-mt-en-fr"

    # OpenAI
    openai_api_key: str = ""  # vient du Setup
    openai_model: str = "gpt-4o-mini-transcribe"
    openai_language: str = ""  # "" = auto (FR + EN)

    # VAD serveur (détection de tours de parole)
    openai_vad_threshold: float = 0.5
    openai_vad_prefix_padding_ms: int = 300
    openai_vad_silence_duration_ms: int = 500

    openai_noise_reduction: str = "near_field"  # "near_field" | "far_field" | ""

    # Audio envoyé à OpenAI
    target_sr: int = 16000


class LiveTranslateThread(QThread):
    new_line = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        audio_q: "queue.Queue[Optional[Tuple[bytes, int]]]",
        input_samplerate: int,
        input_channels: int,
        cfg: Optional[LiveTranslateConfig] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.audio_q = audio_q
        self.input_samplerate = int(input_samplerate)
        self.input_channels = int(input_channels)
        self.cfg = cfg or LiveTranslateConfig()

        self._stop_evt = threading.Event()
        self._ws = None
        self._ws_thread = None
        self._ws_send_lock = threading.Lock()

        self._translator_mode = None
        self._argos_translator = None
        self._marian_tok = None
        self._marian_model = None

        self._init_translator()

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

    def _emit(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.new_line.emit(f"{ts} | {text}")

    def _init_translator(self):
        if self.cfg.translation_engine == "marian" and MarianMTModel is not None and MarianTokenizer is not None:
            try:
                self._marian_tok = MarianTokenizer.from_pretrained(self.cfg.marian_model_name)
                self._marian_model = MarianMTModel.from_pretrained(self.cfg.marian_model_name)

                if torch is not None and torch.cuda.is_available():
                    self._marian_model = self._marian_model.to("cuda")

                self._translator_mode = "marian"
                return
            except Exception:
                self._marian_tok = None
                self._marian_model = None

        if argos_translate is None:
            self._translator_mode = None
            return

        try:
            langs = argos_translate.get_installed_languages()
            src = next((l for l in langs if l.code == "en"), None)
            tgt = next((l for l in langs if l.code == "fr"), None)
            if src and tgt:
                self._argos_translator = src.get_translation(tgt)
                self._translator_mode = "argos"
        except Exception:
            self._translator_mode = None

    def _translate_en_to_fr(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""

        if self._translator_mode == "marian" and self._marian_tok is not None and self._marian_model is not None:
            try:
                dev = None
                if torch is not None:
                    dev = next(self._marian_model.parameters()).device

                batch = self._marian_tok([text], return_tensors="pt", padding=True, truncation=True)
                if torch is not None and dev is not None:
                    batch = {k: v.to(dev) for k, v in batch.items()}

                out = self._marian_model.generate(**batch, max_length=256)
                fr = self._marian_tok.decode(out[0], skip_special_tokens=True)
                return (fr or "").strip()
            except Exception:
                return ""

        if self._translator_mode == "argos" and self._argos_translator is not None:
            try:
                fr = self._argos_translator.translate(text)
                return (fr or "").strip()
            except Exception:
                return ""

        return ""

    # ------------- OpenAI Realtime -------------

    def _ws_on_open(self, ws):
        payload = {
            "type": "transcription_session.update",
            "input_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": self.cfg.openai_model,
                "prompt": "",
                "language": self.cfg.openai_language,
            },
            "turn_detection": {
                "type": "server_vad",
                "threshold": float(self.cfg.openai_vad_threshold),
                "prefix_padding_ms": int(self.cfg.openai_vad_prefix_padding_ms),
                "silence_duration_ms": int(self.cfg.openai_vad_silence_duration_ms),
            },
        }

        if (self.cfg.openai_noise_reduction or "").strip():
            payload["input_audio_noise_reduction"] = {"type": self.cfg.openai_noise_reduction}

        try:
            ws.send(json.dumps(payload))
            self._emit("Live OpenAI: connecté.")
        except Exception as e:
            self.error.emit(f"OpenAI: init session impossible: {e}")

    def _ws_on_message(self, ws, message: str):
        try:
            data = json.loads(message)
        except Exception:
            return

        t = data.get("type", "")

        # On affiche uniquement les phrases finalisées (plus lisible)
        if t == "conversation.item.input_audio_transcription.completed":
            transcript = (data.get("transcript") or "").strip()
            if not transcript:
                return

            self._emit(f"SRC: {transcript}")

            if _looks_english(transcript):
                fr = self._translate_en_to_fr(transcript)
                if fr:
                    self._emit(f"FR : {fr}")
                else:
                    self._emit("FR : (traduction indisponible)")
            else:
                self._emit(f"FR : {transcript}")

            self._emit("")

    def _ws_on_error(self, ws, error):
        self.error.emit(f"OpenAI WS error: {error}")

    def _ws_on_close(self, ws, code, reason):
        if not self._stop_evt.is_set():
            self.error.emit(f"OpenAI WS closed: {code} {reason}")

    def _ws_send_json(self, obj: dict):
        if self._ws is None:
            return
        s = json.dumps(obj, ensure_ascii=False)
        with self._ws_send_lock:
            try:
                self._ws.send(s)
            except Exception:
                pass

    def run(self):
        try:
            if websocket is None:
                raise RuntimeError("Dépendance manquante: pip install websocket-client")

            api_key = (self.cfg.openai_api_key or "").strip()
            if not api_key:
                raise RuntimeError("OpenAI API Key manquante (Setup).")

            url = "wss://api.openai.com/v1/realtime?intent=transcription"
            headers = [
                "Authorization: Bearer " + api_key,
                "OpenAI-Beta: realtime=v1",
            ]

            self._ws = websocket.WebSocketApp(
                url,
                header=headers,
                on_open=self._ws_on_open,
                on_message=self._ws_on_message,
                on_error=self._ws_on_error,
                on_close=self._ws_on_close,
            )

            self._ws_thread = threading.Thread(
                target=self._ws.run_forever,
                kwargs={"ping_interval": 20, "ping_timeout": 10},
                daemon=True,
            )
            self._ws_thread.start()

            while not self._stop_evt.is_set():
                item = self.audio_q.get()

                if item is None:
                    break

                in_data, _frame_count = item
                if not in_data:
                    continue

                raw_i16 = np.frombuffer(in_data, dtype=np.int16)
                mono = _to_mono_float32_i16_pcm(raw_i16, self.input_channels)
                mono_sr = _resample_linear(mono, self.input_samplerate, int(self.cfg.target_sr))
                pcm16 = _float32_to_i16_bytes(mono_sr)

                evt = {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm16).decode("ascii"),
                }
                self._ws_send_json(evt)

        except Exception as e:
            self.error.emit(str(e))
        finally:
            try:
                if self._ws is not None:
                    self._ws.close()
            except Exception:
                pass