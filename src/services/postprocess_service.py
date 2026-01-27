from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from services.diarization_service import diarize_session
from services.meeting_summary_service import MeetingSummaryService


@dataclass
class PostProcessConfig:
    """
    Configuration de post-traitement après enregistrement.

    Objectifs:
    - Ne jamais bloquer le logiciel si la diarization (pyannote/HF) est indisponible.
    - Permettre à terme de brancher d'autres traitements (résumé, export, etc.).
    """
    # Diarization
    hf_token: str = ""  # vide = diarization best-effort + fallback 1 speaker
    diarization_model: str = "pyannote/speaker-diarization-3.1"
    fallback_model: str = "pyannote/speaker-diarization"
    device: str = "auto"  # "cpu", "cuda", "auto"

    # Exports
    generate_docx: bool = True

    # Output
    output_dir: str = "output"


LogFn = Callable[[str], None]


def postprocess_recording(
    wav_path: str,
    cfg: PostProcessConfig,
    log: Optional[LogFn] = None,
) -> Tuple[str, Optional[str]]:
    """
    Lance le post-process:
    1) diarization + transcription (diarize_session)
    2) génération docx (optionnel)

    Retour:
    - transcript_path (txt/json selon ton implémentation de diarize_session)
    - docx_path (ou None si désactivé/échec)
    """
    def _log(msg: str) -> None:
        if log:
            log(msg)

    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not cfg.hf_token:
        _log("[PostProcess] ⚠ HuggingFace token absent : diarization pyannote en best-effort (fallback 1 speaker si besoin).")

    transcript_path = diarize_session(
        wav_path=wav_path,
        hf_token=cfg.hf_token or "",
        diarization_model=cfg.diarization_model,
        fallback_model=cfg.fallback_model,
        device=cfg.device,
        output_dir=str(out_dir),
        log=_log,
    )

    docx_path: Optional[str] = None
    if cfg.generate_docx:
        try:
            docx_path = str(out_dir / (Path(transcript_path).stem + ".docx"))
            ok = MeetingSummaryService().generate_meeting_docx(transcript_path, docx_path)
            if ok:
                _log(f"[PostProcess] ✅ Docx généré: {docx_path}")
            else:
                docx_path = None
                _log("[PostProcess] ⚠ Docx non généré (voir logs).")
        except Exception as e:
            docx_path = None
            _log(f"[PostProcess] ⚠ Erreur génération docx: {e}")

    return transcript_path, docx_path
