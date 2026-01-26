from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from config.secure_store import getsecret
from services.diarization_service import diarize_session, DiarizationConfig


class PostProcessThread(QThread):
    status = pyqtSignal(str)
    done = pyqtSignal(str)   # message final
    error = pyqtSignal(str)

    def __init__(self, cfg: dict, session_dir: Path, language: str = "auto", quality: str = "standard", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.session_dir = session_dir
        self.language = language
        self.quality = quality

    def _perplexity_summary(self, text: str) -> Optional[str]:
        api_key = getsecret(self.cfg, "perplexityapikey") or ""
        if not api_key:
            return None

        try:
            import requests

            prompt = (
                "Résume cette transcription de réunion en Français en 5 bullets max.\n"
                "Sois concret: décisions, actions, risques, points ouverts.\n\n"
                f"TRANSCRIPTION:\n{text}\n"
            )

            payload = {
                "model": "sonar-pro",
                "messages": [{"role": "user", "content": prompt}],
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            r = requests.post(
                "https://api.perplexity.ai/chat/completions",
                json=payload,
                headers=headers,
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content.strip() if content else None

        except Exception:
            return None

    def run(self):
        try:
            hf_token = getsecret(self.cfg, "hf_token") or ""
            if not hf_token:
                raise RuntimeError("HuggingFace token manquant (setup).")

            self.status.emit("Diarization + transcription en cours (2 à 10 min)...")

            # Qualité: standard => small, precise => medium
            model_size = "small" if self.quality.lower().startswith("standard") else "medium"

            cfg = DiarizationConfig(model_size=model_size, device="auto", compute_type="int8")
            transcript_path = diarize_session(
                self.session_dir,
                hf_token=hf_token,
                language=self.language,
                cfg=cfg,
            )

            self.status.emit(f"Transcription OK: {transcript_path.name}")

            # Résumé Perplexity (optionnel)
            txt = transcript_path.read_text(encoding="utf-8")
            summ = self._perplexity_summary(txt)
            if summ:
                out = self.session_dir / "summary_fr.txt"
                out.write_text(summ, encoding="utf-8")
                self.status.emit("Résumé Perplexity OK: summary_fr.txt")
            else:
                self.status.emit("Résumé Perplexity ignoré (pas de clé ou erreur).")

            self.done.emit(f"Post-process terminé. Dossier: {self.session_dir}")

        except Exception as e:
            self.error.emit(str(e))
