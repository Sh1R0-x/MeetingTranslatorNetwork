from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_progress(session_dir: Path, percent: int, stage: str, message: str) -> None:
    try:
        payload = {
            "percent": int(percent),
            "stage": str(stage),
            "message": str(message),
        }
        _write_text(session_dir / "postprocess_progress.json", json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="MeetingTranslatorNetwork compte rendu worker")
    ap.add_argument("--args", required=True, help="Path to postprocess_args.json")
    ns = ap.parse_args()

    args_path = Path(ns.args)
    payload = json.loads(args_path.read_text(encoding="utf-8"))

    session_dir = Path(payload["session_dir"])
    log_path = session_dir / "postprocess_worker_log.txt"
    err_path = session_dir / "postprocess_worker_error.txt"
    result_path = session_dir / "postprocess_result.json"

    try:
        _write_progress(session_dir, 5, "start", "Compte rendu démarré")

        # Import inside worker process (isolated from UI)
        from services.postprocess_service import PostProcessConfig, run_postprocess

        pp = payload.get("pp_cfg") or {}
        pp_cfg = PostProcessConfig(
            language=pp.get("language", "auto"),
            quality=pp.get("quality", "standard"),
            enable_docx=bool(pp.get("enable_docx", True)),
            enable_diarization=bool(pp.get("enable_diarization", True)),
            device=pp.get("device", "auto"),
            diarization_model=pp.get("diarization_model", "pyannote/speaker-diarization-3.1"),
            fallback_model=pp.get("fallback_model", "pyannote/speaker-diarization"),
            diarization_mode=pp.get("diarization_mode", "voice"),
        )

        wav_path = Path(payload["wav_path"])
        mic_wav_path = Path(payload["mic_wav_path"]) if payload.get("mic_wav_path") else None
        hf_token = payload.get("hf_token") or os.getenv("HF_TOKEN", "")

        # Run diarization/transcription
        transcript_path = run_postprocess(
            wav_path=wav_path,
            mic_wav_path=mic_wav_path,
            session_dir=session_dir,
            hf_token=hf_token,
            cfg=pp_cfg,
        )

        _write_progress(session_dir, 70, "transcription", "Transcription terminée")

        docx_path = ""
        if bool(pp_cfg.enable_docx):
            try:
                _write_progress(session_dir, 85, "docx", "Génération du DOCX...")
                from services.meeting_summary_service import generate_meeting_docx

                out = generate_meeting_docx(
                    transcript_path=Path(transcript_path),
                    session_dir=session_dir,
                    cfg=payload.get("cfg") or {},
                )
                docx_path = str(out)
                _write_progress(session_dir, 100, "done", "DOCX généré")
            except Exception:
                # Do not fail worker if docx fails
                _write_text(log_path, (log_path.read_text(encoding="utf-8") if log_path.exists() else "") + "\n[docx] error:\n" + traceback.format_exc())
                _write_progress(session_dir, 100, "done", "DOCX terminé avec erreur")
        else:
                _write_progress(session_dir, 100, "done", "Compte rendu terminé")

        result = {"transcript_path": str(transcript_path), "docx_path": docx_path}
        _write_text(result_path, json.dumps(result, ensure_ascii=False))
        _write_text(log_path, "OK")
        print(json.dumps(result, ensure_ascii=False))
        return 0

    except Exception:
        err = traceback.format_exc()
        _write_progress(session_dir, 100, "error", "Compte rendu en erreur")
        _write_text(err_path, err)
        # also print so parent can log it
        print(err, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
