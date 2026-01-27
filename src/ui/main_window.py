from __future__ import annotations

import queue
import traceback
from pathlib import Path

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config.secure_store import load_config, save_config
from services.recorder_service import RecorderService


APP_NAME = "MeetingTranslatorNetwork"
APP_VERSION = "2026"
DEFAULT_SESSIONS_DIR = str(Path.cwd() / "recordings")


def log_line(msg: str):
    # Log simple (console). Tu pourras centraliser plus tard.
    try:
        print(msg)
    except Exception:
        pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")

        self.cfg = load_config()

        # Live queue (participants) consumed by LiveOpenAIThread
        self.live_queue = queue.Queue(maxsize=300)

        # Device ids are saved by SetupWindow
        po = self.cfg.get("participantsoutputdeviceid", None)
        mi = self.cfg.get("microdeviceid", None)
        if po is None or mi is None:
            raise RuntimeError(
                "Configuration audio manquante : ouvre Configuration et sélectionne "
                "la sortie Windows (participants) et le micro."
            )

        sessions_dir = Path(self.cfg.get("sessions_dir") or DEFAULT_SESSIONS_DIR)
        sessions_dir.mkdir(parents=True, exist_ok=True)

        self.recorder = RecorderService(
            participants_output_device_id=int(po),
            mic_device_id=int(mi),
            output_root=sessions_dir,
        )

        # Plug live participants queue
        self.recorder.set_live_participants_queue(self.live_queue)
        # Keep attribute name used by LiveOpenAIThread
        self.recorder.live_queue = self.live_queue

        self.session_dir = None

        self.live_thread = None
        self.pp_thread = None

        self._build_ui()
        self._apply_cfg_to_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick_timer)
        self._seconds = 0

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout()

        # Buttons row
        buttons = QHBoxLayout()
        self.btn_start = QPushButton("Démarrer")
        self.btn_stop = QPushButton("Arrêter")
        self.btn_stop.setEnabled(False)
        self.btn_setup = QPushButton("Configuration")
        buttons.addWidget(self.btn_start)
        buttons.addWidget(self.btn_stop)
        buttons.addWidget(self.btn_setup)
        layout.addLayout(buttons)

        # Duration
        self.lbl_duration = QLabel("Durée: 00:00:00")
        layout.addWidget(self.lbl_duration)

        # Options group
        opt = QGroupBox("Options")
        opt_l = QHBoxLayout()

        self.chk_live = QCheckBox("Live OpenAI")
        self.chk_docx = QCheckBox("Générer DOCX")

        self.cmb_lang = QComboBox()
        self.cmb_lang.addItems(["auto", "fr", "en"])

        self.cmb_quality = QComboBox()
        self.cmb_quality.addItems(["standard", "precise"])

        opt_l.addWidget(self.chk_live)
        opt_l.addWidget(self.chk_docx)
        opt_l.addWidget(QLabel("Langue:"))
        opt_l.addWidget(self.cmb_lang)
        opt_l.addWidget(QLabel("Qualité:"))
        opt_l.addWidget(self.cmb_quality)

        opt.setLayout(opt_l)
        layout.addWidget(opt)

        # Tabs (Live / Debug)
        self.tabs = QTabWidget()
        self.txt_live = QPlainTextEdit()
        self.txt_live.setReadOnly(True)

        self.txt_debug = QPlainTextEdit()
        self.txt_debug.setReadOnly(True)

        self.tabs.addTab(self.txt_live, "Live")
        self.tabs.addTab(self.txt_debug, "Debug")
        layout.addWidget(self.tabs)

        root.setLayout(layout)
        self.setCentralWidget(root)

        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_setup.clicked.connect(self._on_setup)

        self.chk_live.stateChanged.connect(self._on_cfg_changed)
        self.chk_docx.stateChanged.connect(self._on_cfg_changed)
        self.cmb_lang.currentTextChanged.connect(self._on_cfg_changed)
        self.cmb_quality.currentTextChanged.connect(self._on_cfg_changed)

    def _apply_cfg_to_ui(self):
        self.chk_live.setChecked(bool(self.cfg.get("enable_live_openai", True)))
        self.chk_docx.setChecked(bool(self.cfg.get("generate_docx", True)))
        self.cmb_lang.setCurrentText((self.cfg.get("postprocess_language") or "auto").lower())
        self.cmb_quality.setCurrentText((self.cfg.get("whisper_quality") or "standard").lower())

    def _save_ui_to_cfg(self):
        self.cfg["enable_live_openai"] = bool(self.chk_live.isChecked())
        self.cfg["generate_docx"] = bool(self.chk_docx.isChecked())
        self.cfg["postprocess_language"] = self.cmb_lang.currentText()
        self.cfg["whisper_quality"] = self.cmb_quality.currentText()
        save_config(self.cfg)

    def _on_cfg_changed(self):
        self._save_ui_to_cfg()

    def _tick_timer(self):
        self._seconds += 1
        hh = self._seconds // 3600
        mm = (self._seconds % 3600) // 60
        ss = self._seconds % 60
        self.lbl_duration.setText(f"Durée: {hh:02d}:{mm:02d}:{ss:02d}")

    def _append_live(self, line: str):
        self.txt_live.appendPlainText(line)

    def _append_debug(self, line: str):
        self.txt_debug.appendPlainText(line)

    def _set_status(self, msg: str):
        self.statusBar().showMessage(msg, 5000)
        self._append_debug(msg)

    def _start_live_openai(self):
        if self.live_thread:
            return

        # Lazy import (évite de casser tant que threads/*.py n’est pas rempli)
        from threads.live_openai_thread import LiveOpenAIThread

        self.live_thread = LiveOpenAIThread(cfg=self.cfg, recorder=self.recorder)
        self.live_thread.live_line.connect(self._append_live)
        self.live_thread.status.connect(self._set_status)
        self.live_thread.start()

    def _stop_live_openai(self):
        if not self.live_thread:
            return
        try:
            self.live_thread.stop()
        except Exception:
            pass
        try:
            self.live_thread.quit()
            self.live_thread.wait(2000)
        except Exception:
            pass
        self.live_thread = None

    def _on_start(self):
        try:
            self.txt_live.clear()
            self.txt_debug.clear()

            self.recorder.start()
            self.session_dir = self.recorder.session_dir

            self._seconds = 0
            self.timer.start(1000)

            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)

            self._set_status("Enregistrement démarré")
            log_line("[UI] Start recording")

            enable_live = bool(self.cfg.get("enable_live_openai", True))
            if enable_live:
                self._start_live_openai()

        except Exception:
            err = traceback.format_exc()
            log_line("=== Start exception ===\n" + err)
            QMessageBox.critical(self, "Erreur", err)

    def _on_stop(self):
        try:
            self.btn_stop.setEnabled(False)
            self.timer.stop()

            self._stop_live_openai()

            self.recorder.stop()
            log_line("[UI] Stop recording")

            self._set_status("Post-processing...")

            if not self.session_dir:
                self.session_dir = self.recorder.session_dir
            if not self.session_dir:
                raise RuntimeError("session_dir manquant (RecorderService)")

            # Expose wav_path / mic_wav_path for PostProcessThread (compat)
            ppaths = list(getattr(self.recorder.participants_track, "wav_paths", []) or [])
            mpaths = list(getattr(self.recorder.my_track, "wav_paths", []) or [])

            if not ppaths:
                raise RuntimeError("Aucun fichier WAV participants généré.")
            if not mpaths:
                raise RuntimeError("Aucun fichier WAV micro généré.")

            self.recorder.wav_path = Path(ppaths[0])
            self.recorder.mic_wav_path = Path(mpaths[0])

            # Lazy import (évite de casser tant que threads/*.py n’est pas rempli)
            from threads.postprocess_thread import PostProcessThread

            self.pp_thread = PostProcessThread(cfg=self.cfg, recorder=self.recorder, session_dir=self.session_dir)
            self.pp_thread.finished_ok.connect(self._on_postprocess_ok)
            self.pp_thread.failed.connect(self._on_postprocess_fail)
            self.pp_thread.start()

        except Exception:
            err = traceback.format_exc()
            log_line("=== Stop exception ===\n" + err)
            QMessageBox.critical(self, "Erreur", err)
            self.btn_start.setEnabled(True)

    def _on_postprocess_ok(self, transcript_path: str):
        self._set_status(f"Terminé: {transcript_path}")
        self.btn_start.setEnabled(True)
        QMessageBox.information(self, "Terminé", f"Transcription générée:\n{transcript_path}")

    def _on_postprocess_fail(self, err: str):
        self._set_status("Post-process: erreur")
        self.btn_start.setEnabled(True)
        QMessageBox.critical(self, "Post-process error", err)

    def _on_setup(self):
        # Lazy import to avoid circular
        from ui.setup_window import SetupWindow

        dlg = SetupWindow()
        dlg.exec_()

        self.cfg = load_config()
        self._apply_cfg_to_ui()

        # Rebuild recorder with updated device ids
        po = self.cfg.get("participantsoutputdeviceid", None)
        mi = self.cfg.get("microdeviceid", None)
        if po is not None and mi is not None:
            sessions_dir = Path(self.cfg.get("sessions_dir") or DEFAULT_SESSIONS_DIR)
            sessions_dir.mkdir(parents=True, exist_ok=True)

            self.recorder = RecorderService(
                participants_output_device_id=int(po),
                mic_device_id=int(mi),
                output_root=sessions_dir,
            )

            self.recorder.set_live_participants_queue(self.live_queue)
            self.recorder.live_queue = self.live_queue
