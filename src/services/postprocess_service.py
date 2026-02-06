from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class PostProcessConfig:
    """
    Contrat unique Compte rendu.
    Utilisé par:
    - threads/postprocess_thread.py (construction cfg)
    - services/diarization_service.py (via getattr(cfg, ...))

    Champs garantis:
    - language: "auto" / "fr" / "en"
    - quality: "standard" / "precise"
    - enable_docx: bool
    - enable_diarization: bool
    - device: "auto" / "cpu" / "cuda"
    - diarization_model / fallback_model
    - diarization_mode: "voice" / "source"
    """
    language: str = "auto"
    quality: str = "standard"
    enable_docx: bool = True
    enable_diarization: bool = True

    device: str = "auto"
    diarization_model: str = "pyannote/speaker-diarization-3.1"
    fallback_model: str = "pyannote/speaker-diarization"
    diarization_mode: str = "voice"


def build_postprocess_config(cfg_dict: dict) -> PostProcessConfig:
    """
    Construit une PostProcessConfig depuis la config globale (dict).
    """
    cfg_dict = cfg_dict or {}

    language = (cfg_dict.get("postprocess_language") or "auto").lower()
    quality = (cfg_dict.get("postprocess_quality") or cfg_dict.get("whisper_quality") or "standard").lower()
    enable_docx = bool(cfg_dict.get("postprocess_generate_docx", cfg_dict.get("generate_docx", True)))
    diarization_mode = str(cfg_dict.get("postprocess_diarization_mode") or "").lower()
    if diarization_mode not in ("voice", "source"):
        diarization_mode = "voice" if bool(cfg_dict.get("postprocess_enable_diarization", cfg_dict.get("enable_diarization", True))) else "source"
    enable_diarization = diarization_mode == "voice"

    device = (cfg_dict.get("postprocess_device") or cfg_dict.get("device") or "auto").lower()
    diarization_model = cfg_dict.get("diarization_model") or "pyannote/speaker-diarization-3.1"
    fallback_model = cfg_dict.get("fallback_model") or "pyannote/speaker-diarization"

    return PostProcessConfig(
        language=language,
        quality=quality,
        enable_docx=enable_docx,
        enable_diarization=enable_diarization,
        device=device,
        diarization_model=diarization_model,
        fallback_model=fallback_model,
        diarization_mode=diarization_mode,
    )


def run_postprocess(
    wav_path: Path,
    session_dir: Path,
    hf_token: str,
    cfg: PostProcessConfig,
    mic_wav_path: Optional[Path] = None,
) -> str:
    """
    Lance la diarization/transcription et retourne le chemin du transcript.
    """
    from services.diarization_service import diarize_session

    wav_path = Path(wav_path)
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    transcript_path = diarize_session(
        wav_path=wav_path,
        mic_wav_path=mic_wav_path,
        session_dir=session_dir,
        hf_token=hf_token or "",
        cfg=cfg,
    )
    return str(transcript_path)
