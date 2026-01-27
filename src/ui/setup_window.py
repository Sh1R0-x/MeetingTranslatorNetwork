from __future__ import annotations

import queue
import sys
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio
import sounddevice as sd
from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from audio.wasapi_loopback import get_loopback_for_output, list_wasapi_output_devices
from config.secure_store import appdir, getsecret, loadconfig, saveconfig, setsecret

# ---------- UI constants
FIELD_W = 520
PROGRESS_W = 700
PAGE_MARGINS = (14, 14, 14, 14)
PAGE_SPACING = 8


def list_input_devices_with_api():
    hostapis = sd.query_hostapis()
    devices = sd.query_devices()
    items = []

    for idx, d in enumerate(devices):
        if d.get("max_input_channels", 0) > 0:
            apiname = hostapis[d["hostapi"]]["name"]
            name = d.get("name", "")
            sr = int(d.get("default_samplerate", 0) or 0)
            ch = int(d.get("max_input_channels", 0) or 0)
            items.append((idx, f"{idx} | {name} | {apiname} | in={ch}ch | {sr}Hz"))

    return items


def _make_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    b.setMinimumWidth(b.sizeHint().width() + 18)
    b.setMinimumHeight(b.sizeHint().height() + 4)
    return b


def _fix_field(w):
    w.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    w.setMinimumWidth(FIELD_W)
    w.setMaximumWidth(FIELD_W)
    return w


def _fix_progress(p: QProgressBar):
    p.setMinimumWidth(PROGRESS_W)
    p.setMaximumWidth(PROGRESS_W)
    p.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return p


def _make_form(parent: QWidget) -> QFormLayout:
    f = QFormLayout(parent)
    f.setContentsMargins(0, 0, 0, 0)
    f.setHorizontalSpacing(10)
    f.setVerticalSpacing(8)
    f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    f.setFormAlignment(Qt.AlignTop | Qt.AlignLeft)
    f.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
    return f


class LevelMeterSoundDevice:
    def __init__(self):
        self.stream = None
        self.q = queue.Queue()
        self.peak = 0.0

    def start(self, deviceid: int, channels: int = 1):
        self.stop()
        dev = sd.query_devices(deviceid, "input")
        sr = int(dev["default_samplerate"])

        def callback(indata, frames, timeinfo, status):
            p = float(np.max(np.abs(indata))) if indata is not None else 0.0
            self.q.put(p)

        self.stream = sd.InputStream(
            device=deviceid,
            channels=channels,
            samplerate=sr,
            blocksize=2048,
            latency="high",
            dtype="float32",
            callback=callback,
        )
        self.stream.start()

    def poll(self) -> float:
        got = False
        pmax = 0.0
        while True:
            try:
                p = self.q.get_nowait()
                got = True
                if p > pmax:
                    pmax = p
            except queue.Empty:
                break

        if got:
            self.peak = max(0.0, min(1.0, float(pmax)))
        else:
            self.peak *= 0.90
        return float(self.peak)

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
        self.stream = None
        self.peak = 0.0

        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break


class LevelMeterLoopback:
    def __init__(self):
        self.p = None
        self.stream = None
        self.q = queue.Queue()
        self.peak = 0.0
        self.channels = 2

    def start_from_output(self, outputdeviceindex: int):
        self.stop()
        lb = get_loopback_for_output(int(outputdeviceindex))
        if not lb:
            raise RuntimeError("Loopback introuvable pour cette sortie.")

        rate = int(lb["defaultSampleRate"])
        self.channels = int(lb.get("maxInputChannels", 2) or 2)

        self.p = pyaudio.PyAudio()

        def cb(indata, framecount, timeinfo, status):
            if indata:
                data = np.frombuffer(indata, dtype=np.int16)
                if data.size:
                    p = float(np.max(np.abs(data)) / 32767.0)
                    self.q.put(p)
            return (indata, pyaudio.paContinue)

        self.stream = self.p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=rate,
            input=True,
            input_device_index=int(lb["index"]),
            frames_per_buffer=1024,
            stream_callback=cb,
        )
        self.stream.start_stream()

    def poll(self) -> float:
        got = False
        pmax = 0.0
        while True:
            try:
                p = self.q.get_nowait()
                got = True
                if p > pmax:
                    pmax = p
            except queue.Empty:
                break

        if got:
            self.peak = max(0.0, min(1.0, float(pmax)))
        else:
            self.peak *= 0.90
        return float(self.peak)

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
        self.stream = None

        if self.p is not None:
            try:
                self.p.terminate()
            except Exception:
                pass
        self.p = None

        self.peak = 0.0
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break


class DiarizationDownloadThread(QThread):
    done = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, hf_token: str, parent=None):
        super().__init__(parent)
        self.hf_token = hf_token

    def run(self):
        try:
            from pyannote.audio import Pipeline

            _ = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=self.hf_token)
            self.done.emit("Modèles diarization téléchargés OK.")
        except Exception as e:
            self.error.emit(str(e))


class SetupWindow(QDialog):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("MeetingTranslator - Configuration")
        self.setMinimumSize(980, 640)

        self.cfg = loadconfig()

        self.metermic = LevelMeterSoundDevice()
        self.meterparticipants = LevelMeterLoopback()
        self.activetest = None

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.refreshmeter)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        center = QHBoxLayout()
        center.setSpacing(10)

        self.menu = QListWidget()
        self.menu.setFixedWidth(210)
        self.menu.setSelectionMode(QAbstractItemView.SingleSelection)

        self.stack = QStackedWidget()

        center.addWidget(self.menu)
        center.addWidget(self.stack, 1)

        root.addLayout(center, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        bottom.addStretch(1)
        self.btnsave = _make_button("Enregistrer")
        self.btnclose = _make_button("Fermer")
        bottom.addWidget(self.btnsave)
        bottom.addWidget(self.btnclose)
        root.addLayout(bottom)

        self.page_audio = QWidget()
        self.page_transcription = QWidget()
        self.page_postprocess = QWidget()
        self.page_api = QWidget()
        self.page_debug = QWidget()

        self._build_page_audio()
        self._build_page_transcription()
        self._build_page_postprocess()
        self._build_page_api()
        self._build_page_debug()

        self.stack.addWidget(self.page_audio)
        self.stack.addWidget(self.page_transcription)
        self.stack.addWidget(self.page_postprocess)
        self.stack.addWidget(self.page_api)
        self.stack.addWidget(self.page_debug)

        self._add_menu_item("AUDIO")
        self._add_menu_item("TRANSCRIPTION")
        self._add_menu_item("POST-PROCESS")
        self._add_menu_item("API")
        self._add_menu_item("DEBUG / SUPPORT")

        self.menu.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.menu.setCurrentRow(0)

        self._wire_signals()
        self.loaddevices()
        self._apply_enabled_states()

    def _add_menu_item(self, title: str):
        self.menu.addItem(QListWidgetItem(title))

    def _build_page_audio(self):
        lay = QVBoxLayout(self.page_audio)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(PAGE_SPACING)
        lay.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        title = QLabel("AUDIO")
        title.setStyleSheet("font-weight: bold;")
        lay.addWidget(title)

        lay.addWidget(QLabel("Participants: sortie Windows capturée en loopback (WASAPI)."))
        lay.addWidget(QLabel("Micro: ton micro en entrée."))

        rowp = QHBoxLayout()
        rowp.setSpacing(10)
        rowp.setAlignment(Qt.AlignLeft)
        rowp.addWidget(QLabel("Sortie Windows (Participants)"))
        self.cbparticipants = _fix_field(QComboBox())
        rowp.addWidget(self.cbparticipants)
        self.btntestparticipants = _make_button("Tester")
        rowp.addWidget(self.btntestparticipants)
        rowp.addStretch(1)
        lay.addLayout(rowp)

        self.pbparticipants = _fix_progress(QProgressBar())
        self.pbparticipants.setRange(0, 1000)
        self.pbparticipants.setValue(0)
        lay.addWidget(self.pbparticipants, alignment=Qt.AlignLeft)

        rowm = QHBoxLayout()
        rowm.setSpacing(10)
        rowm.setAlignment(Qt.AlignLeft)
        rowm.addWidget(QLabel("Micro (toi)"))
        self.cbmicro = _fix_field(QComboBox())
        rowm.addWidget(self.cbmicro)
        self.btntestmicro = _make_button("Tester")
        rowm.addWidget(self.btntestmicro)
        rowm.addStretch(1)
        lay.addLayout(rowm)

        self.pbmicro = _fix_progress(QProgressBar())
        self.pbmicro.setRange(0, 1000)
        self.pbmicro.setValue(0)
        lay.addWidget(self.pbmicro, alignment=Qt.AlignLeft)

        self.lblstatus = QLabel("Statut: prêt")
        lay.addWidget(self.lblstatus)

        self.btnrefresh = _make_button("Rafraîchir la liste")
        lay.addWidget(self.btnrefresh, alignment=Qt.AlignLeft)

        lay.addStretch(1)

    def _build_page_transcription(self):
        lay = QVBoxLayout(self.page_transcription)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(PAGE_SPACING)
        lay.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        title = QLabel("TRANSCRIPTION (Live)")
        title.setStyleSheet("font-weight: bold;")
        lay.addWidget(title)

        self.chk_enable_live = QCheckBox("Transcription temps réel (OpenAI)")
        self.chk_enable_live.setChecked(bool(self.cfg.get("enable_live_openai", True)))
        lay.addWidget(self.chk_enable_live)

        form = _make_form(self.page_transcription)

        self.cb_source_lang = _fix_field(QComboBox())
        self.cb_source_lang.addItem("AUTO (détection auto)", "auto")
        self.cb_source_lang.addItem("FR (Français)", "fr")
        self.cb_source_lang.addItem("EN (English)", "en")

        cur_lang = (self.cfg.get("transcription_source_language") or "auto").lower()
        for i in range(self.cb_source_lang.count()):
            if self.cb_source_lang.itemData(i) == cur_lang:
                self.cb_source_lang.setCurrentIndex(i)
                break

        form.addRow("Langue source (live)", self.cb_source_lang)

        self.cb_translate_model = _fix_field(QComboBox())
        self.cb_translate_model.addItem("gpt-4o-mini (rapide, économe)", "gpt-4o-mini")
        self.cb_translate_model.addItem("gpt-4o (meilleur, plus coûteux)", "gpt-4o")

        cur_model = (self.cfg.get("transcription_translate_model") or "gpt-4o").lower()
        for i in range(self.cb_translate_model.count()):
            if self.cb_translate_model.itemData(i) == cur_model:
                self.cb_translate_model.setCurrentIndex(i)
                break

        form.addRow("Modèle traduction (OpenAI)", self.cb_translate_model)

        self.cb_commit = _fix_field(QComboBox())
        self.cb_commit.addItem("0.5 s (très réactif)", 0.5)
        self.cb_commit.addItem("1.0 s (normal)", 1.0)
        self.cb_commit.addItem("2.0 s (moins de coupures)", 2.0)

        current_commit = float(self.cfg.get("live_commit_every_seconds", 1.0) or 1.0)
        best_i = 1
        for i in range(self.cb_commit.count()):
            if abs(float(self.cb_commit.itemData(i)) - current_commit) < 0.01:
                best_i = i
                break
        self.cb_commit.setCurrentIndex(best_i)

        form.addRow("Découpe phrases live (commit)", self.cb_commit)
        lay.addLayout(form)

        lay.addStretch(1)

    def _build_page_postprocess(self):
        lay = QVBoxLayout(self.page_postprocess)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(PAGE_SPACING)
        lay.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        title = QLabel("POST-PROCESS")
        title.setStyleSheet("font-weight: bold;")
        lay.addWidget(title)

        self.chk_enable_post = QCheckBox("Activer post-process après STOP (speaker A/B + transcription)")
        self.chk_enable_post.setChecked(bool(self.cfg.get("enable_postprocess", False)))
        lay.addWidget(self.chk_enable_post)

        form = _make_form(self.page_postprocess)

        self.cb_post_lang = _fix_field(QComboBox())
        self.cb_post_lang.addItem("AUTO (mix)", "auto")
        self.cb_post_lang.addItem("FR", "fr")
        self.cb_post_lang.addItem("EN", "en")
        curlang = (self.cfg.get("postprocess_language") or "auto").lower()
        self.cb_post_lang.setCurrentIndex(0 if curlang == "auto" else (1 if curlang == "fr" else 2))
        form.addRow("Langue post-process", self.cb_post_lang)

        self.cb_post_quality = _fix_field(QComboBox())
        self.cb_post_quality.addItem("STANDARD (plus rapide)", "standard")
        self.cb_post_quality.addItem("PRECISE (meilleur)", "precise")
        curq = (self.cfg.get("postprocess_quality") or "standard").lower()
        self.cb_post_quality.setCurrentIndex(0 if curq == "standard" else 1)
        form.addRow("Qualité post-process", self.cb_post_quality)

        lay.addLayout(form)

        self.chk_generate_docx = QCheckBox("Générer DOCX (compte-rendu)")
        self.chk_generate_docx.setChecked(bool(self.cfg.get("postprocess_generate_docx", True)))
        lay.addWidget(self.chk_generate_docx)

        self.chk_enable_pplx = QCheckBox("Résumé via Perplexity (si clé)")
        self.chk_enable_pplx.setChecked(bool(self.cfg.get("postprocess_enable_perplexity_summary", True)))
        lay.addWidget(self.chk_enable_pplx)

        lay.addStretch(1)

    def _build_page_api(self):
        lay = QVBoxLayout(self.page_api)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(PAGE_SPACING)
        lay.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        title = QLabel("API")
        title.setStyleSheet("font-weight: bold;")
        lay.addWidget(title)

        lay.addWidget(QLabel("Clés API (optionnel)"))

        form = _make_form(self.page_api)

        self.in_openai = _fix_field(QLineEdit())
        self.in_openai.setEchoMode(QLineEdit.Password)
        if getsecret(self.cfg, "openai_api_key"):
            self.in_openai.setPlaceholderText("Déjà configuré (laisser vide pour ne pas changer)")
        form.addRow("OpenAI API Key", self.in_openai)

        self.inpplx = _fix_field(QLineEdit())
        self.inpplx.setEchoMode(QLineEdit.Password)
        if getsecret(self.cfg, "perplexityapikey"):
            self.inpplx.setPlaceholderText("Déjà configuré (laisser vide pour ne pas changer)")
        form.addRow("Perplexity API Key", self.inpplx)

        lay.addLayout(form)

        lay.addWidget(QLabel("Speaker A/B (post-process)"))

        form2 = _make_form(self.page_api)

        self.inhftoken = _fix_field(QLineEdit())
        self.inhftoken.setEchoMode(QLineEdit.Password)
        if getsecret(self.cfg, "hf_token"):
            self.inhftoken.setPlaceholderText("Déjà configuré (laisser vide pour ne pas changer)")
        form2.addRow("HuggingFace Token (read)", self.inhftoken)

        lay.addLayout(form2)

        self.btndownloadmodels = _make_button("Télécharger/Tester modèles diarization")
        lay.addWidget(self.btndownloadmodels, alignment=Qt.AlignLeft)

        lay.addStretch(1)

    def _build_page_debug(self):
        lay = QVBoxLayout(self.page_debug)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(PAGE_SPACING)
        lay.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        title = QLabel("DEBUG / SUPPORT")
        title.setStyleSheet("font-weight: bold;")
        lay.addWidget(title)

        self.chk_debug = QCheckBox("Activer logs debug (utile pour support)")
        self.chk_debug.setChecked(bool(self.cfg.get("debug_enabled", False)))
        lay.addWidget(self.chk_debug)

        self.chk_show_debug_panel = QCheckBox("Afficher panneau logs debug dans l'app")
        self.chk_show_debug_panel.setChecked(bool(self.cfg.get("debug_show_panel", False)))
        lay.addWidget(self.chk_show_debug_panel)

        lay.addWidget(QLabel(f"Chemin logs: {appdir() / 'debug_runtime.log'}"))

        lay.addStretch(1)

    def _wire_signals(self):
        self.btnrefresh.clicked.connect(self.loaddevices)
        self.btntestparticipants.clicked.connect(lambda: self.toggletest("participants"))
        self.btntestmicro.clicked.connect(lambda: self.toggletest("micro"))

        self.chk_enable_live.stateChanged.connect(self._apply_enabled_states)
        self.chk_enable_post.stateChanged.connect(self._apply_enabled_states)
        self.chk_debug.stateChanged.connect(self._apply_enabled_states)

        self.btndownloadmodels.clicked.connect(self.download_models)

        self.btnsave.clicked.connect(self.save)
        self.btnclose.clicked.connect(self._on_close_clicked)

    def _on_close_clicked(self):
        self.stoptest()
        self.reject()

    def closeEvent(self, event):
        self.stoptest()
        event.accept()

    def _apply_enabled_states(self):
        live_on = bool(self.chk_enable_live.isChecked())
        self.cb_source_lang.setEnabled(live_on)
        self.cb_translate_model.setEnabled(live_on)
        self.cb_commit.setEnabled(live_on)

        post_on = bool(self.chk_enable_post.isChecked())
        self.cb_post_lang.setEnabled(post_on)
        self.cb_post_quality.setEnabled(post_on)
        self.chk_generate_docx.setEnabled(post_on)
        self.chk_enable_pplx.setEnabled(post_on)

        dbg_on = bool(self.chk_debug.isChecked())
        self.chk_show_debug_panel.setEnabled(dbg_on)

    def selectbyid(self, combo: QComboBox, devid: int):
        for i in range(combo.count()):
            if combo.itemData(i) == devid:
                combo.setCurrentIndex(i)
                return

    def loaddevices(self):
        self.cbparticipants.clear()
        self.cbmicro.clear()

        outs = list_wasapi_output_devices()
        for d in outs:
            self.cbparticipants.addItem(f"{d['index']} | {d['name']}", int(d["index"]))

        inputs = list_input_devices_with_api()
        for devid, label in inputs:
            self.cbmicro.addItem(label, int(devid))

        if "participantsoutputdeviceid" in self.cfg:
            self.selectbyid(self.cbparticipants, int(self.cfg["participantsoutputdeviceid"]))
        if "microdeviceid" in self.cfg:
            self.selectbyid(self.cbmicro, int(self.cfg["microdeviceid"]))

    def toggletest(self, which: str):
        if self.activetest == which:
            self.stoptest()
            return

        self.stoptest()

        try:
            if which == "participants":
                outid = int(self.cbparticipants.currentData())
                self.meterparticipants.start_from_output(outid)
                self.activetest = "participants"
                self.btntestparticipants.setText("Arrêter")
                self.btntestmicro.setEnabled(False)
                self.lblstatus.setText("Statut: test Participants en cours")
            else:
                devid = int(self.cbmicro.currentData())
                self.metermic.start(devid, channels=1)
                self.activetest = "micro"
                self.btntestmicro.setText("Arrêter")
                self.btntestparticipants.setEnabled(False)
                self.lblstatus.setText("Statut: test Micro en cours")

            self.timer.start()

        except Exception as e:
            QMessageBox.critical(self, "Erreur test audio", str(e))
            self.stoptest()

    def stoptest(self):
        self.timer.stop()
        self.metermic.stop()
        self.meterparticipants.stop()

        self.activetest = None
        self.pbparticipants.setValue(0)
        self.pbmicro.setValue(0)

        self.btntestparticipants.setText("Tester")
        self.btntestmicro.setText("Tester")
        self.btntestparticipants.setEnabled(True)
        self.btntestmicro.setEnabled(True)

        self.lblstatus.setText("Statut: prêt")

    def refreshmeter(self):
        if self.activetest == "participants":
            p = self.meterparticipants.poll()
            self.pbparticipants.setValue(int(p * 1000))
        elif self.activetest == "micro":
            p = self.metermic.poll()
            self.pbmicro.setValue(int(p * 1000))

    def download_models(self):
        token = self.inhftoken.text().strip()
        if not token:
            token = getsecret(self.cfg, "hf_token") or ""

        if not token:
            QMessageBox.warning(self, "Token manquant", "Renseigne un HuggingFace Token (read) puis retente.")
            return

        self.btndownloadmodels.setEnabled(False)
        self.lblstatus.setText("Statut: téléchargement diarization en cours...")

        self._dlthread = DiarizationDownloadThread(token)
        self._dlthread.done.connect(self._on_dl_done)
        self._dlthread.error.connect(self._on_dl_error)
        self._dlthread.start()

    def _on_dl_done(self, msg: str):
        self.btndownloadmodels.setEnabled(True)
        self.lblstatus.setText(f"Statut: {msg}")
        QMessageBox.information(self, "OK", msg)

    def _on_dl_error(self, err: str):
        self.btndownloadmodels.setEnabled(True)
        self.lblstatus.setText("Statut: prêt")
        QMessageBox.critical(self, "Erreur diarization", err)

    def save(self):
        self.cfg["participantsoutputdeviceid"] = int(self.cbparticipants.currentData())
        self.cfg["microdeviceid"] = int(self.cbmicro.currentData())

        self.cfg["enable_live_openai"] = bool(self.chk_enable_live.isChecked())
        self.cfg["transcription_source_language"] = str(self.cb_source_lang.currentData())
        self.cfg["transcription_translate_model"] = str(self.cb_translate_model.currentData())
        self.cfg["live_commit_every_seconds"] = float(self.cb_commit.currentData())

        self.cfg["enable_postprocess"] = bool(self.chk_enable_post.isChecked())
        self.cfg["postprocess_language"] = str(self.cb_post_lang.currentData())
        self.cfg["postprocess_quality"] = str(self.cb_post_quality.currentData())
        self.cfg["postprocess_generate_docx"] = bool(self.chk_generate_docx.isChecked())
        self.cfg["postprocess_enable_perplexity_summary"] = bool(self.chk_enable_pplx.isChecked())

        self.cfg["debug_enabled"] = bool(self.chk_debug.isChecked())
        self.cfg["debug_show_panel"] = bool(self.chk_show_debug_panel.isChecked())

        openai = self.in_openai.text().strip()
        pplx = self.inpplx.text().strip()
        hft = self.inhftoken.text().strip()

        if openai:
            setsecret(self.cfg, "openai_api_key", openai)
        if pplx:
            setsecret(self.cfg, "perplexityapikey", pplx)
        if hft:
            setsecret(self.cfg, "hf_token", hft)

        saveconfig(self.cfg)
        QMessageBox.information(self, "OK", "Configuration enregistrée.")
        self.accept()
