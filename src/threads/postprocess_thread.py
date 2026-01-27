from __future__ import annotations

import traceback
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from common import log_line
from config.secure_store import getsecret
from services.diarization_service import diarize_session
from services.meeting_summary_service import generate_meeting_docx
from services.postprocess_service import PostProcessConfig
from services.recorder_service import RecorderService


class PostProcessThread(QThread):
    finished_ok = pyqtSignal(str)  # transcript_path
    failed = pyqtSignal(str)

    def __init__(self, cfg: dict, recorder: RecorderService, session_dir: Path, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.recorder = recorder
        self.session_dir = Path(session_dir)

    def run(self):
        try:
            hf_token = getsecret(self.cfg, "hf_token") or ""
            if not hf_token:
                log_line("[PostProcess] ⚠ HuggingFace token absent : diarization pyannote sera désactivée (fallback 1 speaker).")

            language = (self.cfg.get("postprocess_language") or "auto").lower()
            quality = (self.cfg.get("whisper_quality") or "standard").lower()
            generate_docx = bool(self.cfg.get("generate_docx", True))

            log_line(f"[PostProcess] language={language} quality={quality} docx={generate_docx}")

            pp_cfg = PostProcessConfig(
                language=language,
                quality=quality,
                enable_docx=generate_docx,
            )

            transcript_path = diarize_session(
                wav_path=self.recorder.wav_path,
                mic_wav_path=self.recorder.mic_wav_path,
                session_dir=self.session_dir,
                hf_token=hf_token,
                cfg=pp_cfg,
            )

            # DOCX generation
            if generate_docx:
                try:
                    generate_meeting_docx(
                        transcript_path=Path(transcript_path),
                        session_dir=self.session_dir,
                        cfg=self.cfg,
                    )
                except Exception as e:
                    log_line(f"[PostProcess] DOCX generation error: {e}")

            self.finished_ok.emit(str(transcript_path))

        except Exception:
            err = traceback.format_exc()
            log_line("=== PostProcessThread exception ===\n" + err)
            self.failed.emit(err)
