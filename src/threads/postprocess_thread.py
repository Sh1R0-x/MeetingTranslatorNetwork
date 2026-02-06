from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from config.secure_store import getsecret
from services.postprocess_service import build_postprocess_config


class PostProcessThread(QThread):
    """Thread Qt qui exécute le compte rendu (diarization + transcription + docx).

    Solution pérenne Windows : exécuter le compte rendu dans un **processus séparé**.
    Ça évite les "Windows fatal exception: access violation" (onnxruntime/ctranslate2/torch)
    qui peuvent arriver quand on charge des libs natives dans des threads secondaires.

    Résultat : même si pyannote/whisper plante, l'UI ne se ferme pas.
    """

    finished_ok = pyqtSignal(str)  # transcript_path
    failed = pyqtSignal(str)       # traceback

    def __init__(self, cfg: dict, recorder, session_dir: Path, parent=None):
        super().__init__(parent)
        self.cfg = cfg or {}
        self.recorder = recorder
        self.session_dir = Path(session_dir)
        self._proc = None
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
        except Exception:
            pass

    def _write_log(self, text: str) -> None:
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            p = self.session_dir / "postprocess_log.txt"
            with p.open("a", encoding="utf-8") as f:
                f.write(text.rstrip() + "\n")
        except Exception:
            pass

    def _write_error(self, text: str) -> None:
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            p = self.session_dir / "postprocess_error.txt"
            with p.open("w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    def run(self) -> None:
        try:
            self._write_log("[PostProcessThread] start")

            hf_token = getsecret(self.cfg, "hf_token") or ""
            if not hf_token:
                try:
                    v = self.cfg.get("hf_token")
                    if isinstance(v, str):
                        hf_token = v
                except Exception:
                    pass
            self._write_log("[PostProcessThread] hf_token: present" if hf_token else "[PostProcessThread] hf_token: empty")

            pp_cfg = build_postprocess_config(self.cfg)
            self._write_log(f"[PostProcessThread] cfg: language={pp_cfg.language} quality={pp_cfg.quality} enable_docx={pp_cfg.enable_docx}")

            wav_path = Path(getattr(self.recorder, "wav_path"))
            mic_wav_path = Path(getattr(self.recorder, "mic_wav_path")) if getattr(self.recorder, "mic_wav_path", None) else None

            self._write_log(f"[PostProcessThread] wav_path={wav_path}")
            self._write_log(f"[PostProcessThread] mic_wav_path={mic_wav_path}")
            self._write_log(f"[PostProcessThread] session_dir={self.session_dir}")

            # Ecriture des arguments dans un fichier JSON (évite les limites de longueur de ligne de commande)
            args_path = self.session_dir / "postprocess_args.json"
            payload = {
                "wav_path": str(wav_path),
                "mic_wav_path": str(mic_wav_path) if mic_wav_path else "",
                "session_dir": str(self.session_dir),
                "hf_token": hf_token,
                "pp_cfg": {
                    "language": pp_cfg.language,
                    "quality": pp_cfg.quality,
                    "enable_docx": bool(pp_cfg.enable_docx),
                    "enable_diarization": bool(getattr(pp_cfg, "enable_diarization", True)),
                    "device": pp_cfg.device,
                    "diarization_model": pp_cfg.diarization_model,
                    "fallback_model": pp_cfg.fallback_model,
                    "diarization_mode": getattr(pp_cfg, "diarization_mode", "voice"),
                },
                "cfg": self.cfg,
            }
            args_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            # Lancer le worker dans un processus séparé
            project_root = Path(__file__).resolve().parents[2]  # .../src/threads -> project
            src_dir = project_root / "src"

            env = os.environ.copy()
            # expose src/ pour que "from services..." marche dans le worker
            env["PYTHONPATH"] = str(src_dir) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            if hf_token:
                env["HF_TOKEN"] = hf_token

            cmd = [sys.executable, "-u", "-m", "workers.postprocess_worker", "--args", str(args_path)]
            self._write_log(f"[PostProcessThread] spawn: {' '.join(cmd)}")

            self._proc = subprocess.Popen(
                cmd,
                cwd=str(project_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            while True:
                if self._stop_flag:
                    try:
                        if self._proc and self._proc.poll() is None:
                            self._proc.terminate()
                    except Exception:
                        pass
                    self._write_log("[PostProcessThread] cancelled by user")
                    self.failed.emit("Compte rendu annulé")
                    return
                if self._proc.poll() is not None:
                    break
                time.sleep(0.2)

            stdout, stderr = self._proc.communicate(timeout=5)

            # Log stdout/stderr pour debug
            if stdout:
                self._write_log("[Worker stdout]\n" + stdout.rstrip())
            if stderr:
                self._write_log("[Worker stderr]\n" + stderr.rstrip())

            if self._proc.returncode != 0:
                raise RuntimeError(f"worker failed rc={self._proc.returncode}")

            # Le worker écrit un résultat JSON
            result_path = self.session_dir / "postprocess_result.json"
            if not result_path.exists():
                raise RuntimeError("worker finished but postprocess_result.json not found")

            result = json.loads(result_path.read_text(encoding="utf-8"))
            transcript_path = result.get("transcript_path") or ""
            if not transcript_path:
                raise RuntimeError("worker result missing transcript_path")

            self._write_log(f"[PostProcessThread] transcript_path={transcript_path}")
            if result.get("docx_path"):
                self._write_log(f"[PostProcessThread] docx ok: {result['docx_path']}")

            self._write_log("[PostProcessThread] done ok")
            self.finished_ok.emit(str(transcript_path))

        except Exception:
            err = traceback.format_exc()
            self._write_log("=== PostProcessThread exception ===")
            self._write_error(err)
            self.failed.emit(err)


class ChunkPostProcessThread(QThread):
    """
    Transcription/diarization for a single WAV part (runs in a separate process).
    Generates only transcript outputs (no DOCX) to keep it light and safe.
    """

    finished_ok = pyqtSignal(int, str)  # part_index, transcript_path
    failed = pyqtSignal(int, str)

    def __init__(self, cfg: dict, part_index: int, wav_path: Path, mic_wav_path: Path | None, session_dir: Path, parent=None):
        super().__init__(parent)
        self.cfg = cfg or {}
        self.part_index = int(part_index)
        self.wav_path = Path(wav_path)
        self.mic_wav_path = Path(mic_wav_path) if mic_wav_path else None
        self.session_dir = Path(session_dir)
        self._proc = None
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
        except Exception:
            pass

    def _write_log(self, text: str) -> None:
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            p = self.session_dir / "chunk_log.txt"
            with p.open("a", encoding="utf-8") as f:
                f.write(text.rstrip() + "\n")
        except Exception:
            pass

    def _write_error(self, text: str) -> None:
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            p = self.session_dir / "chunk_error.txt"
            with p.open("w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    def run(self) -> None:
        try:
            self._write_log(f"[ChunkPostProcess] start part={self.part_index}")

            hf_token = getsecret(self.cfg, "hf_token") or ""
            if not hf_token:
                try:
                    v = self.cfg.get("hf_token")
                    if isinstance(v, str):
                        hf_token = v
                except Exception:
                    pass

            pp_cfg = build_postprocess_config(self.cfg)
            # Chunk: no DOCX/summary (we merge later)
            pp_cfg.enable_docx = False

            args_path = self.session_dir / "postprocess_args.json"
            payload = {
                "wav_path": str(self.wav_path),
                "mic_wav_path": str(self.mic_wav_path) if self.mic_wav_path else "",
                "session_dir": str(self.session_dir),
                "hf_token": hf_token,
                "pp_cfg": {
                    "language": pp_cfg.language,
                    "quality": pp_cfg.quality,
                    "enable_docx": False,
                    "enable_diarization": bool(getattr(pp_cfg, "enable_diarization", True)),
                    "device": pp_cfg.device,
                    "diarization_model": pp_cfg.diarization_model,
                    "fallback_model": pp_cfg.fallback_model,
                    "diarization_mode": getattr(pp_cfg, "diarization_mode", "voice"),
                },
                "cfg": self.cfg,
            }
            args_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            project_root = Path(__file__).resolve().parents[2]
            src_dir = project_root / "src"

            env = os.environ.copy()
            env["PYTHONPATH"] = str(src_dir) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            if hf_token:
                env["HF_TOKEN"] = hf_token

            cmd = [sys.executable, "-u", "-m", "workers.postprocess_worker", "--args", str(args_path)]
            self._write_log(f"[ChunkPostProcess] spawn: {' '.join(cmd)}")

            self._proc = subprocess.Popen(
                cmd,
                cwd=str(project_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            while True:
                if self._stop_flag:
                    try:
                        if self._proc and self._proc.poll() is None:
                            self._proc.terminate()
                    except Exception:
                        pass
                    self._write_log("[ChunkPostProcess] cancelled by user")
                    self.failed.emit(self.part_index, "Chunk annulé")
                    return
                if self._proc.poll() is not None:
                    break
                time.sleep(0.2)

            stdout, stderr = self._proc.communicate(timeout=5)
            if stdout:
                self._write_log("[Worker stdout]\n" + stdout.rstrip())
            if stderr:
                self._write_log("[Worker stderr]\n" + stderr.rstrip())

            if self._proc.returncode != 0:
                raise RuntimeError(f"worker failed rc={self._proc.returncode}")

            result_path = self.session_dir / "postprocess_result.json"
            if not result_path.exists():
                raise RuntimeError("worker finished but postprocess_result.json not found")

            result = json.loads(result_path.read_text(encoding="utf-8"))
            transcript_path = result.get("transcript_path") or ""
            if not transcript_path:
                raise RuntimeError("worker result missing transcript_path")

            self._write_log(f"[ChunkPostProcess] transcript_path={transcript_path}")
            self.finished_ok.emit(self.part_index, str(transcript_path))

        except Exception:
            err = traceback.format_exc()
            self._write_log("=== ChunkPostProcess exception ===")
            self._write_error(err)
            self.failed.emit(self.part_index, err)
