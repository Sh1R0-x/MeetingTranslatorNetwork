from __future__ import annotations

import sys
import queue
import traceback
from pathlib import Path

# IMPORTANT: précharger faster_whisper AVANT PyQt5
try:
    from faster_whisper import WhisperModel  # noqa: F401
except Exception:
    pass

ENABLE_LIVE = True

BASE_DIR = Path(__file__).resolve().parent  # ...\src
PROJECT_ROOT = BASE_DIR.parent  # ...\ (C:\MeetingTranslatorNetwork)

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QPushButton,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QInputDialog,
)
from PyQt5.QtCore import QTimer, QThread, pyqtSignal

from config.secure_store import load_config, get_secret
from services.recorder_service import RecorderService
from services.meeting_summary_service import MeetingSummaryService
from services.diarization_service import diarize_session, DiarizationConfig
from ui.setup_window import SetupWindow

LOG_PATH = BASE_DIR / "debug_runtime.log"


def log_line(msg: str):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    except Exception:
        pass


def excepthook(exc_type, exc, tb):
    log_line("=== UNCAUGHT EXCEPTION ===")
    log_line("".join(traceback.format_exception(exc_type, exc, tb)))
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = excepthook


class RecorderThread(QThread):
    recording_started = pyqtSignal()
    recording_stopped = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, recorder: RecorderService):
        super().__init__()
        self.recorder = recorder
        self.action = None

    def set_action(self, action: str):
        self.action = action

    def run(self):
        try:
            if self.action == "start":
                self.recorder.start()
                self.recording_started.emit()
            elif self.action == "stop":
                self.recorder.stop()
                session_path = str(self.recorder.session_dir) if self.recorder.session_dir else ""
                self.recording_stopped.emit(session_path)
        except Exception as e:
            log_line("=== RecorderThread exception ===")
            log_line(traceback.format_exc())
            self.error_occurred.emit(str(e))


class PostProcessThread(QThread):
    status = pyqtSignal(str)
    done = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, cfg: dict, session_dir: Path, language: str, quality: str, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.session_dir = Path(session_dir)
        self.language = language  # "fr" | "en" | "auto"
        self.quality = quality  # "standard" | "precise"

    def run(self):
        try:
            hf_token = get_secret(self.cfg, "hf_token") or ""
            if not hf_token:
                raise RuntimeError("HuggingFace token manquant (Setup -> HuggingFace Token).")

            pplx = get_secret(self.cfg, "perplexityapikey") or ""

            self.status.emit("Post-process: diarization + transcription (2-10 min)...")

            model_size = "small" if self.quality == "standard" else "medium"
            dcfg = DiarizationConfig(
                model_size=model_size,
                device="auto",
                compute_type="int8",
                beam_size=5,
                vad_filter=True,
                vad_min_silence_ms=400,
            )

            transcript_path = diarize_session(
                self.session_dir,
                hf_token=hf_token,
                language=self.language,
                cfg=dcfg,
            )

            self.status.emit(f"Transcription OK: {transcript_path.name}")

            svc = MeetingSummaryService(perplexity_api_key=pplx)
            docx_path = svc.generate_meeting_docx(
                session_dir=self.session_dir,
                transcript_path=transcript_path,
                title="Résumé de Réunion",
                participants="N/A",
            )

            self.status.emit(f"DOCX généré: {docx_path.name}")
            self.done.emit(f"Terminé ! Fichiers en:\n{self.session_dir}")

        except Exception as e:
            self.error.emit(str(e))


class MeetingWindow(QMainWindow):
    def __init__(self, config: dict):
        super().__init__()
        self.setWindowTitle("MeetingTranslator - Réunion")
        self.setMinimumSize(900, 650)

        self.cfg = config

        self._build_recorder()

        self.recorder_thread = RecorderThread(self.recorder)
        self.recorder_thread.recording_started.connect(self.on_recording_started)
        self.recorder_thread.recording_stopped.connect(self.on_recording_stopped)
        self.recorder_thread.error_occurred.connect(self.on_error)

        # LIVE
        self._live_q = None
        self._live_thread = None
        self._live_error_shown = False

        # Post-process
        self._pp_thread = None

        # UI
        central = QWidget()
        layout = QVBoxLayout()

        layout.addWidget(QLabel("Enregistrement Réunion"))

        self.status_label = QLabel("Statut: Prêt")
        layout.addWidget(self.status_label)

        self.duration_label = QLabel("Durée: 00:00:00")
        layout.addWidget(self.duration_label)

        btn_layout = QHBoxLayout()

        self.btn_start = QPushButton("Démarrer")
        self.btn_start.setMinimumHeight(50)
        self.btn_start.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold;")
        self.btn_start.clicked.connect(self.start_recording)
        btn_layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("Arrêter")
        self.btn_stop.setMinimumHeight(50)
        self.btn_stop.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_recording)
        btn_layout.addWidget(self.btn_stop)

        layout.addLayout(btn_layout)

        self.btn_setup = QPushButton("Setup Devices/API")
        self.btn_setup.clicked.connect(self.open_setup)
        layout.addWidget(self.btn_setup)

        layout.addWidget(QLabel("Live (Participants) :"))
        self.live_log = QPlainTextEdit()
        self.live_log.setReadOnly(True)
        self.live_log.setMaximumBlockCount(3000)
        layout.addWidget(self.live_log)

        central.setLayout(layout)
        self.setCentralWidget(central)

        self.duration_seconds = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_duration)

    def _get_cfg_device_ids(self):
        part_id = self.cfg.get("participants_output_device_id")
        if part_id is None:
            part_id = self.cfg.get("participantsoutputdeviceid")

        mic_id = self.cfg.get("micro_device_id")
        if mic_id is None:
            mic_id = self.cfg.get("microdeviceid")

        if part_id is None or mic_id is None:
            raise RuntimeError("IDs devices manquants. Ouvre Setup Devices/API puis Enregistrer.")

        return int(part_id), int(mic_id)

    def _build_recorder(self):
        part_id, mic_id = self._get_cfg_device_ids()

        # ✅ On force la sortie dans le dossier du projet:
        # C:\MeetingTranslatorNetwork\recordings\DD-MM-YYYY\HHhMMmSSs\...
        output_root = PROJECT_ROOT / "recordings"
        output_root.mkdir(parents=True, exist_ok=True)

        self.recorder = RecorderService(
            participants_output_device_id=part_id,
            mic_device_id=mic_id,
            output_root=output_root,
        )

    def _start_live_translation(self):
        if self._live_thread is not None:
            return

        from services.live_translate_service import LiveTranslateThread, LiveTranslateConfig

        if self._live_q is None:
            raise RuntimeError("Queue live non initialisée.")

        if not self.recorder.participants_rate or not self.recorder.participants_channels:
            raise RuntimeError("Participants rate/channels non initialisés (start recorder d'abord).")

        cfg = LiveTranslateConfig(
            src_lang="en",
            tgt_lang="fr",
            model_size="base",
            compute_type="float32",
            device="cpu",
            openai_api_key=(get_secret(self.cfg, "openaiapikey") or ""),
        )

        self._live_thread = LiveTranslateThread(
            audio_q=self._live_q,
            input_samplerate=self.recorder.participants_rate,
            input_channels=self.recorder.participants_channels,
            cfg=cfg,
        )

        self._live_thread.new_line.connect(self.live_log.appendPlainText)
        self._live_thread.error.connect(lambda msg: QMessageBox.critical(self, "Live translate error", msg))
        self._live_thread.start()

    def _stop_live_translation(self):
        if self._live_thread is not None:
            try:
                self._live_thread.stop()
                self._live_thread.wait(3000)
            except Exception:
                pass
        self._live_thread = None

        try:
            self.recorder.set_live_participants_queue(None)
        except Exception:
            pass

        self._live_q = None

    def start_recording(self):
        try:
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.status_label.setText("Statut: Démarrage...")

            self.duration_seconds = 0
            self.timer.start(1000)

            self._live_q = queue.Queue(maxsize=300)
            self.recorder.set_live_participants_queue(self._live_q)

            self.recorder_thread.set_action("start")
            self.recorder_thread.start()

        except Exception as e:
            log_line("=== start_recording exception ===")
            log_line(traceback.format_exc())
            self.status_label.setText(f"Erreur: {e}")
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)

    def stop_recording(self):
        try:
            self.btn_stop.setEnabled(False)
            self.status_label.setText("Statut: Arrêt...")
            self.timer.stop()

            self._stop_live_translation()

            self.recorder_thread.set_action("stop")
            self.recorder_thread.start()

        except Exception as e:
            log_line("=== stop_recording exception ===")
            log_line(traceback.format_exc())
            self.status_label.setText(f"Erreur: {e}")

    def on_recording_started(self):
        self.status_label.setText("Enregistrement en cours...")

        if not ENABLE_LIVE:
            self.live_log.appendPlainText("Live disabled (ENABLE_LIVE=False).")
            self.live_log.appendPlainText("")
            return

        try:
            self._start_live_translation()
        except Exception as e:
            msg = f"Live OFF: {e}"
            self.live_log.appendPlainText(msg)
            self.live_log.appendPlainText("")
            if not self._live_error_shown:
                self._live_error_shown = True
                QMessageBox.critical(self, "Live translate error", str(e))

    def on_recording_stopped(self, session_path: str):
        self.timer.stop()

        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

        self._stop_live_translation()

        if not session_path:
            self.status_label.setText("Terminé, mais session_dir vide (erreur ?)")
            return

        if bool(self.cfg.get("enable_diarization", False)):
            lang_choice, ok = QInputDialog.getItem(
                self,
                "Langue réunion (post-process)",
                "Choisir :",
                ["MIX (auto)", "FR", "EN"],
                0,
                False,
            )

            if not ok:
                self.status_label.setText(f"Terminé ! Fichiers en:\n{session_path}")
                return

            language = "auto"
            if lang_choice == "FR":
                language = "fr"
            elif lang_choice == "EN":
                language = "en"

            qual_choice, ok2 = QInputDialog.getItem(
                self,
                "Qualité (post-process)",
                "Choisir :",
                ["STANDARD (plus rapide)", "PRECISE (meilleur)"],
                0,
                False,
            )

            quality = "standard"
            if ok2 and qual_choice.startswith("PRECISE"):
                quality = "precise"

            self.status_label.setText("Statut: post-process en cours (2-10 min)...")
            self.btn_start.setEnabled(False)

            self._pp_thread = PostProcessThread(
                cfg=self.cfg,
                session_dir=Path(session_path),
                language=language,
                quality=quality,
                parent=self,
            )

            self._pp_thread.status.connect(self.live_log.appendPlainText)
            self._pp_thread.done.connect(self._on_postprocess_done)
            self._pp_thread.error.connect(self._on_postprocess_error)
            self._pp_thread.start()
        else:
            self.status_label.setText(f"Terminé ! Fichiers en:\n{session_path}")

    def _on_postprocess_done(self, msg: str):
        self.status_label.setText(msg)
        self.btn_start.setEnabled(True)

    def _on_postprocess_error(self, err: str):
        self.status_label.setText("Erreur post-process.")
        self.btn_start.setEnabled(True)
        QMessageBox.critical(self, "Post-process error", err)

    def on_error(self, error_msg: str):
        self.timer.stop()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.status_label.setText(f"Erreur: {error_msg}")
        self._stop_live_translation()
        QMessageBox.critical(self, "Erreur", error_msg)

    def update_duration(self):
        self.duration_seconds += 1
        h = self.duration_seconds // 3600
        m = (self.duration_seconds % 3600) // 60
        s = self.duration_seconds % 60
        self.duration_label.setText(f"Durée: {h:02d}:{m:02d}:{s:02d}")

    def open_setup(self):
        self._stop_live_translation()

        setup = SetupWindow()
        result = setup.exec_() if hasattr(setup, "exec_") else setup.exec()
        if result == 1:
            self.cfg = load_config()
            self._build_recorder()
            self.recorder_thread.recorder = self.recorder


def main():
    app = QApplication(sys.argv)
    cfg = load_config()

    has_part = ("participants_output_device_id" in cfg) or ("participantsoutputdeviceid" in cfg)
    has_mic = ("micro_device_id" in cfg) or ("microdeviceid" in cfg)

    if not has_part or not has_mic:
        setup = SetupWindow()
        result = setup.exec_() if hasattr(setup, "exec_") else setup.exec()
        if result != 1:
            sys.exit(0)
        cfg = load_config()

    w = MeetingWindow(cfg)
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
