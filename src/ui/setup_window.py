from __future__ import annotations

import queue
import sys
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio
import sounddevice as sd
from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
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
    QFrame,
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
from common import LOG_PATH

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
    b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    b.setMinimumWidth(b.sizeHint().width() + 18)
    b.setMinimumHeight(b.sizeHint().height() + 4)
    return b


def _fix_field(w):
    w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    w.setMinimumWidth(FIELD_W)
    w.setMaximumWidth(FIELD_W)
    return w


def _fix_progress(p: QProgressBar):
    p.setMinimumWidth(PROGRESS_W)
    p.setMaximumWidth(PROGRESS_W)
    p.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return p


def _make_form(parent: QWidget) -> QFormLayout:
    f = QFormLayout(parent)
    f.setContentsMargins(0, 0, 0, 0)
    f.setHorizontalSpacing(10)
    f.setVerticalSpacing(8)
    f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    f.setFormAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    return f


def _section_title(text: str) -> QLabel:
    lbl = QLabel(str(text).upper())
    lbl.setObjectName("SectionTitle")
    lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return lbl


def _make_card(parent: QWidget) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(parent)
    frame.setProperty("kind", "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)
    return frame, layout


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

    def __init__(self, hf_token: str, model_name: str, parent=None):
        super().__init__(parent)
        self.hf_token = hf_token
        self.model_name = model_name

    def run(self):
        try:
            from pyannote.audio import Pipeline

            try:
                _ = Pipeline.from_pretrained(self.model_name, use_auth_token=self.hf_token)
            except TypeError:
                _ = Pipeline.from_pretrained(self.model_name, token=self.hf_token)
            self.done.emit("Modèles voix téléchargés OK.")
            return
        except Exception as e_py:
            # Fallback: validate access/token via HF Hub if available
            try:
                from huggingface_hub import HfApi, snapshot_download

                api = HfApi(token=self.hf_token)
                _ = api.model_info(self.model_name)
                snapshot_download(self.model_name, token=self.hf_token, allow_patterns=["*.json", "*.yaml", "*.yml"])
                self.done.emit("Token HF validé. Modèle accessible.")
                return
            except Exception as e_hf:
                self.error.emit(f"{e_py} | {e_hf}")


class SetupWindow(QDialog):
    def __init__(self, start_page: str | None = None):
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
        self.menu.setObjectName("SettingsMenu")
        self.menu.setFixedWidth(210)
        self.menu.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        self.stack = QStackedWidget()

        center.addWidget(self.menu)
        center.addWidget(self.stack, 1)

        root.addLayout(center, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        bottom.addStretch(1)
        self.btnsave = _make_button("Enregistrer")
        self.btnclose = _make_button("Fermer")
        self.btnsave.setProperty("variant", "primary")
        self.btnclose.setProperty("variant", "ghost")
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
        self._add_menu_item("COMPTE RENDU")
        self._add_menu_item("API")
        self._add_menu_item("DEBUG / SUPPORT")

        self.menu.currentRowChanged.connect(self.stack.setCurrentIndex)
        if start_page:
            self._select_page(start_page)
        else:
            self.menu.setCurrentRow(0)

        self._wire_signals()
        self.loaddevices()
        self._apply_enabled_states()

    def _add_menu_item(self, title: str):
        self.menu.addItem(QListWidgetItem(title))

    def _select_page(self, title: str):
        title = (title or "").strip().upper()
        if title == "POST-PROCESS":
            title = "COMPTE RENDU"
        for i in range(self.menu.count()):
            if self.menu.item(i).text().strip().upper() == title:
                self.menu.setCurrentRow(i)
                return
        self.menu.setCurrentRow(0)

    def _build_page_audio(self):
        lay = QVBoxLayout(self.page_audio)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(PAGE_SPACING)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        title = QLabel("AUDIO")
        title.setStyleSheet("font-weight: bold;")
        lay.addWidget(title)

        card_sources, cs = _make_card(self.page_audio)
        cs.addWidget(_section_title("Sources audio"))
        rowp = QHBoxLayout()
        rowp.setSpacing(10)
        rowp.setAlignment(Qt.AlignmentFlag.AlignLeft)
        rowp.addWidget(QLabel("Sortie audio"))
        self.cbparticipants = _fix_field(QComboBox())
        rowp.addWidget(self.cbparticipants)
        self.btntestparticipants = _make_button("Tester")
        self.btntestparticipants.setProperty("variant", "ghost")
        rowp.addWidget(self.btntestparticipants)
        rowp.addStretch(1)
        cs.addLayout(rowp)

        self.pbparticipants = _fix_progress(QProgressBar())
        self.pbparticipants.setRange(0, 1000)
        self.pbparticipants.setValue(0)
        cs.addWidget(self.pbparticipants, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(card_sources)

        card_mic, cm = _make_card(self.page_audio)
        cm.addWidget(_section_title("Micro"))

        rowm = QHBoxLayout()
        rowm.setSpacing(10)
        rowm.setAlignment(Qt.AlignmentFlag.AlignLeft)
        rowm.addWidget(QLabel("Entrée audio"))
        self.cbmicro = _fix_field(QComboBox())
        rowm.addWidget(self.cbmicro)
        self.btntestmicro = _make_button("Tester")
        self.btntestmicro.setProperty("variant", "ghost")
        rowm.addWidget(self.btntestmicro)
        rowm.addStretch(1)
        cm.addLayout(rowm)

        self.pbmicro = _fix_progress(QProgressBar())
        self.pbmicro.setRange(0, 1000)
        self.pbmicro.setValue(0)
        cm.addWidget(self.pbmicro, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(card_mic)

        self.lblstatus = QLabel("Statut: prêt")
        self.lblstatus.setVisible(False)

        self.btnrefresh = _make_button("Rafraîchir la liste")
        self.btnrefresh.setProperty("variant", "ghost")
        lay.addWidget(self.btnrefresh, alignment=Qt.AlignmentFlag.AlignLeft)

        lay.addStretch(1)

    def _build_page_transcription(self):
        lay = QVBoxLayout(self.page_transcription)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(PAGE_SPACING)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        title = QLabel("LIVE (transcription & traduction)")
        title.setStyleSheet("font-weight: bold;")
        lay.addWidget(title)

        card_trans, ct = _make_card(self.page_transcription)
        ct.addWidget(_section_title("Transcription"))

        self.chk_enable_live = QCheckBox("Transcription live (recommandé)")
        self.chk_enable_live.setChecked(bool(self.cfg.get("enable_live", self.cfg.get("enable_live_openai", True))))
        ct.addWidget(self.chk_enable_live)

        form_engine = _make_form(self.page_transcription)
        self.cb_live_engine = _fix_field(QComboBox())
        self.cb_live_engine.addItem("Deepgram Nova-3 (meilleur pour le français)", "deepgram")
        self.cb_live_engine.addItem("AssemblyAI (multilingue)", "assemblyai")
        cur_engine = (self.cfg.get("live_engine") or "deepgram").lower()
        for i in range(self.cb_live_engine.count()):
            if self.cb_live_engine.itemData(i) == cur_engine:
                self.cb_live_engine.setCurrentIndex(i)
                break
        form_engine.addRow("Moteur de transcription", self.cb_live_engine)
        ct.addLayout(form_engine)

        self.chk_format_turns = QCheckBox("Améliorer la lisibilité (ponctuation/majuscules)")
        self.chk_format_turns.setChecked(bool(self.cfg.get("live_format_turns", True)))
        ct.addWidget(self.chk_format_turns)
        lay.addWidget(card_trans)

        card_trad, ctr = _make_card(self.page_transcription)
        ctr.addWidget(_section_title("Traduction"))

        self.chk_live_translate = QCheckBox("Traduction live vers le français (OpenAI)")
        self.chk_live_translate.setChecked(bool(self.cfg.get("live_enable_translation", False)))
        ctr.addWidget(self.chk_live_translate)

        form = _make_form(self.page_transcription)
        self.cb_translate_model = _fix_field(QComboBox())
        self.cb_translate_model.addItem("gpt-4o-mini (rapide, économe)", "gpt-4o-mini")
        self.cb_translate_model.addItem("gpt-4o (meilleur, plus coûteux)", "gpt-4o")
        cur_model = (self.cfg.get("live_translate_model") or "gpt-4o-mini").lower()
        for i in range(self.cb_translate_model.count()):
            if self.cb_translate_model.itemData(i) == cur_model:
                self.cb_translate_model.setCurrentIndex(i)
                break
        form.addRow("Qualité de traduction (OpenAI)", self.cb_translate_model)
        ctr.addLayout(form)
        lay.addWidget(card_trad)

        card_people, cp = _make_card(self.page_transcription)
        cp.addWidget(_section_title("Identification des voix"))

        form_voice = _make_form(self.page_transcription)
        self.cb_voice_ident_mode = _fix_field(QComboBox())
        self.cb_voice_ident_mode.addItem(
            "Fiable (recommandé) : voix uniquement dans le compte rendu",
            "report_only",
        )
        self.cb_voice_ident_mode.addItem(
            "Live (beta) : tentative en direct + compte rendu",
            "live_beta",
        )
        cur_voice_mode = str(self.cfg.get("voice_identification_mode") or "").strip().lower()
        if cur_voice_mode not in ("report_only", "live_beta"):
            cur_voice_mode = "live_beta" if bool(self.cfg.get("live_speaker_labels", False)) else "report_only"
        self.cb_voice_ident_mode.setCurrentIndex(1 if cur_voice_mode == "live_beta" else 0)
        form_voice.addRow("Mode", self.cb_voice_ident_mode)
        cp.addLayout(form_voice)

        self.lbl_voice_hint = QLabel(
            "Conseil: en réunion longue, utilise 'Fiable' pour éviter les confusions en live."
        )
        cp.addWidget(self.lbl_voice_hint)

        self.chk_live_speaker_labels = QCheckBox("Activer les labels de voix en live (PERSONNE 1, 2, ...)")
        self.chk_live_speaker_labels.setChecked(bool(self.cfg.get("live_speaker_labels", False)))
        cp.addWidget(self.chk_live_speaker_labels)

        self.chk_live_speaker_labels_delayed = QCheckBox("Affecter les labels avec léger retard (plus stable)")
        self.chk_live_speaker_labels_delayed.setChecked(bool(self.cfg.get("live_speaker_labels_delayed", False)))
        cp.addWidget(self.chk_live_speaker_labels_delayed)
        lay.addWidget(card_people)

        lay.addStretch(1)

    def _build_page_postprocess(self):
        lay = QVBoxLayout(self.page_postprocess)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(PAGE_SPACING)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        title = QLabel("COMPTE RENDU")
        title.setStyleSheet("font-weight: bold;")
        lay.addWidget(title)

        self.chk_enable_post = QCheckBox("Après STOP: lancer transcription + compte rendu")
        self.chk_enable_post.setChecked(bool(self.cfg.get("enable_postprocess", False)))
        lay.addWidget(self.chk_enable_post)

        card_lang, cl = _make_card(self.page_postprocess)
        cl.addWidget(_section_title("Langue & qualité"))
        form = _make_form(self.page_postprocess)

        self.cb_post_lang = _fix_field(QComboBox())
        self.cb_post_lang.addItem("Automatique (mix)", "auto")
        self.cb_post_lang.addItem("Francais", "fr")
        self.cb_post_lang.addItem("Anglais", "en")
        curlang = (self.cfg.get("postprocess_language") or "auto").lower()
        self.cb_post_lang.setCurrentIndex(0 if curlang == "auto" else (1 if curlang == "fr" else 2))
        form.addRow("Langue du compte rendu", self.cb_post_lang)

        self.cb_post_quality = _fix_field(QComboBox())
        self.cb_post_quality.addItem("Standard (plus rapide)", "standard")
        self.cb_post_quality.addItem("Précis (meilleur, plus lent)", "precise")
        curq = (self.cfg.get("postprocess_quality") or "standard").lower()
        self.cb_post_quality.setCurrentIndex(0 if curq == "standard" else 1)
        form.addRow("Qualité du compte rendu", self.cb_post_quality)

        self.cb_post_device = _fix_field(QComboBox())
        self.cb_post_device.addItem("Auto (prend le GPU si dispo)", "auto")
        self.cb_post_device.addItem("GPU (plus rapide)", "cuda")
        self.cb_post_device.addItem("CPU (plus lent)", "cpu")
        curd = (self.cfg.get("postprocess_device") or self.cfg.get("device") or "auto").lower()
        self.cb_post_device.setCurrentIndex(0 if curd == "auto" else (1 if curd == "cuda" else 2))
        form.addRow("Exécution", self.cb_post_device)

        cl.addLayout(form)
        lay.addWidget(card_lang)

        card_out, co = _make_card(self.page_postprocess)
        co.addWidget(_section_title("Sorties"))
        self.chk_generate_docx = QCheckBox("Générer le document DOCX")
        self.chk_generate_docx.setChecked(bool(self.cfg.get("postprocess_generate_docx", True)))
        co.addWidget(self.chk_generate_docx)

        self.chk_enable_pplx = QCheckBox("Résumé structuré automatique (Perplexity)")
        self.chk_enable_pplx.setChecked(bool(self.cfg.get("postprocess_enable_perplexity_summary", True)))
        co.addWidget(self.chk_enable_pplx)
        lay.addWidget(card_out)

        card_people, cp = _make_card(self.page_postprocess)
        cp.addWidget(_section_title("Personnes & voix"))
        self.cb_diar_mode = _fix_field(QComboBox())
        self.cb_diar_mode.addItem("Par voix (plus précis, nécessite token HF)", "voice")
        self.cb_diar_mode.addItem("Par source (simple: micro/participants)", "source")
        cur_mode = str(self.cfg.get("postprocess_diarization_mode") or "").lower()
        if not cur_mode:
            if not bool(self.cfg.get("postprocess_enable_diarization", self.cfg.get("enable_diarization", True))):
                cur_mode = "source"
            else:
                cur_mode = "voice"
        self.cb_diar_mode.setCurrentIndex(0 if cur_mode == "voice" else 1)
        form2 = _make_form(self.page_postprocess)
        form2.addRow("Séparation des voix", self.cb_diar_mode)
        cp.addLayout(form2)

        self.chk_extract_participants = QCheckBox("Déduire les noms des participants (IA, option)")
        self.chk_extract_participants.setChecked(bool(self.cfg.get("postprocess_extract_participants", False)))
        cp.addWidget(self.chk_extract_participants)
        lay.addWidget(card_people)

        lay.addStretch(1)

    def _build_page_api(self):
        lay = QVBoxLayout(self.page_api)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(PAGE_SPACING)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        title = QLabel("API")
        title.setStyleSheet("font-weight: bold;")
        lay.addWidget(title)

        card_keys, ck = _make_card(self.page_api)
        ck.addWidget(_section_title("Clés API"))
        ck.addWidget(QLabel("Optionnel mais nécessaire pour certaines fonctions."))

        form = _make_form(self.page_api)

        self.in_openai = _fix_field(QLineEdit())
        self.in_openai.setEchoMode(QLineEdit.EchoMode.Password)
        if getsecret(self.cfg, "openai_api_key"):
            self.in_openai.setPlaceholderText("Déjà configuré (laisser vide pour ne pas changer)")
        form.addRow("OpenAI API Key (traduction live)", self.in_openai)

        self.in_deepgram = _fix_field(QLineEdit())
        self.in_deepgram.setEchoMode(QLineEdit.EchoMode.Password)
        if getsecret(self.cfg, "deepgram_api_key"):
            self.in_deepgram.setPlaceholderText("Déjà configuré (laisser vide pour ne pas changer)")
        form.addRow("Deepgram API Key (transcription live)", self.in_deepgram)

        self.in_assemblyai = _fix_field(QLineEdit())
        self.in_assemblyai.setEchoMode(QLineEdit.EchoMode.Password)
        if getsecret(self.cfg, "assemblyai_api_key"):
            self.in_assemblyai.setPlaceholderText("Déjà configuré (laisser vide pour ne pas changer)")
        form.addRow("AssemblyAI API Key (optionnel)", self.in_assemblyai)

        self.inpplx = _fix_field(QLineEdit())
        self.inpplx.setEchoMode(QLineEdit.EchoMode.Password)
        if getsecret(self.cfg, "perplexityapikey"):
            self.inpplx.setPlaceholderText("Déjà configuré (laisser vide pour ne pas changer)")
        form.addRow("Perplexity API Key (résumé)", self.inpplx)

        ck.addLayout(form)
        lay.addWidget(card_keys)

        card_voice, cv = _make_card(self.page_api)
        cv.addWidget(_section_title("Séparation des voix"))

        form2 = _make_form(self.page_api)

        self.inhftoken = _fix_field(QLineEdit())
        self.inhftoken.setEchoMode(QLineEdit.EchoMode.Password)
        if getsecret(self.cfg, "hf_token"):
            self.inhftoken.setPlaceholderText("Déjà configuré (laisser vide pour ne pas changer)")
        form2.addRow("HuggingFace Token (diarization)", self.inhftoken)

        cv.addLayout(form2)

        self.btndownloadmodels = _make_button("Télécharger/Tester les modèles voix")
        self.btndownloadmodels.setProperty("variant", "ghost")
        cv.addWidget(self.btndownloadmodels, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(card_voice)

        lay.addStretch(1)

    def _build_page_debug(self):
        lay = QVBoxLayout(self.page_debug)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(PAGE_SPACING)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        title = QLabel("DEBUG / SUPPORT")
        title.setStyleSheet("font-weight: bold;")
        lay.addWidget(title)

        card_logs, cl = _make_card(self.page_debug)
        cl.addWidget(_section_title("Logs & support"))

        self.chk_debug = QCheckBox("Activer logs debug (utile pour support)")
        self.chk_debug.setChecked(bool(self.cfg.get("debug_enabled", False)))
        cl.addWidget(self.chk_debug)

        self.chk_show_debug_panel = QCheckBox("Afficher panneau logs debug dans l'app")
        self.chk_show_debug_panel.setChecked(bool(self.cfg.get("debug_show_panel", False)))
        cl.addWidget(self.chk_show_debug_panel)

        cl.addWidget(QLabel(f"Chemin logs: {LOG_PATH}"))
        lay.addWidget(card_logs)

        lay.addStretch(1)

    def _wire_signals(self):
        self.btnrefresh.clicked.connect(self.loaddevices)
        self.btntestparticipants.clicked.connect(lambda: self.toggletest("participants"))
        self.btntestmicro.clicked.connect(lambda: self.toggletest("micro"))

        self.chk_enable_live.stateChanged.connect(self._apply_enabled_states)
        self.chk_live_translate.stateChanged.connect(self._apply_enabled_states)
        self.chk_enable_post.stateChanged.connect(self._apply_enabled_states)
        self.chk_debug.stateChanged.connect(self._apply_enabled_states)
        self.cb_voice_ident_mode.currentIndexChanged.connect(self._apply_enabled_states)

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
        self.chk_format_turns.setEnabled(live_on)
        self.chk_live_translate.setEnabled(live_on)
        self.cb_translate_model.setEnabled(live_on and self.chk_live_translate.isChecked())
        mode = str(self.cb_voice_ident_mode.currentData() or "report_only")
        live_beta = mode == "live_beta"
        self.lbl_voice_hint.setEnabled(live_on)
        self.chk_live_speaker_labels.setEnabled(live_on and live_beta)
        self.chk_live_speaker_labels_delayed.setEnabled(live_on and live_beta and self.chk_live_speaker_labels.isChecked())

        post_on = bool(self.chk_enable_post.isChecked())
        self.cb_post_lang.setEnabled(post_on)
        self.cb_post_quality.setEnabled(post_on)
        self.cb_post_device.setEnabled(post_on)
        self.chk_generate_docx.setEnabled(post_on)
        self.chk_enable_pplx.setEnabled(post_on)
        self.cb_diar_mode.setEnabled(post_on)
        self.chk_extract_participants.setEnabled(post_on)

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
            QMessageBox.warning(
                self,
                "Token manquant",
                "Renseigne un HuggingFace Token pour la séparation par voix puis retente.",
            )
            return

        self.btndownloadmodels.setEnabled(False)
        self.lblstatus.setText("Statut: téléchargement diarization en cours...")

        model_name = str(self.cfg.get("diarization_model") or "pyannote/speaker-diarization-3.1")
        self._dlthread = DiarizationDownloadThread(token, model_name)
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
        msg = err
        low = (err or "").lower()
        if "pyannote" in low or "huggingface_hub" in low:
            msg = (
                "Impossible de télécharger/tester les modèles voix.\n"
                "Vérifie le token HF et que pyannote.audio est installé.\n"
                "Sinon, utilise la séparation par source (micro/participants)."
            )
        QMessageBox.critical(self, "Erreur modèles voix", msg)

    def save(self):
        self.cfg["participantsoutputdeviceid"] = int(self.cbparticipants.currentData())
        self.cfg["microdeviceid"] = int(self.cbmicro.currentData())

        self.cfg["enable_live"] = bool(self.chk_enable_live.isChecked())
        self.cfg["live_engine"] = str(self.cb_live_engine.currentData())
        self.cfg["live_format_turns"] = bool(self.chk_format_turns.isChecked())
        self.cfg["live_enable_translation"] = bool(self.chk_live_translate.isChecked())
        self.cfg["live_translate_model"] = str(self.cb_translate_model.currentData())
        voice_mode = str(self.cb_voice_ident_mode.currentData() or "report_only")
        self.cfg["voice_identification_mode"] = voice_mode
        if voice_mode == "live_beta":
            self.cfg["live_speaker_labels"] = bool(self.chk_live_speaker_labels.isChecked())
            self.cfg["live_speaker_labels_delayed"] = bool(self.chk_live_speaker_labels_delayed.isChecked())
        else:
            self.cfg["live_speaker_labels"] = False
            self.cfg["live_speaker_labels_delayed"] = False
        for k in (
            "enable_live_openai",
            "live_provider",
            "transcription_source_language",
            "transcription_translate_model",
            "live_transcribe_model",
            "live_noise_reduction",
            "live_commit_every_seconds",
            "live_use_server_vad",
            "live_vad_threshold",
            "live_vad_prefix_padding_ms",
            "live_vad_silence_duration_ms",
            "live_stitch_gap_s",
        ):
            self.cfg.pop(k, None)

        self.cfg["enable_postprocess"] = bool(self.chk_enable_post.isChecked())
        self.cfg["postprocess_language"] = str(self.cb_post_lang.currentData())
        self.cfg["postprocess_quality"] = str(self.cb_post_quality.currentData())
        self.cfg["postprocess_device"] = str(self.cb_post_device.currentData())
        self.cfg["postprocess_generate_docx"] = bool(self.chk_generate_docx.isChecked())
        self.cfg["postprocess_enable_perplexity_summary"] = bool(self.chk_enable_pplx.isChecked())
        self.cfg["postprocess_diarization_mode"] = str(self.cb_diar_mode.currentData())
        self.cfg["postprocess_enable_diarization"] = bool(self.cb_diar_mode.currentData() == "voice")
        self.cfg["postprocess_extract_participants"] = bool(self.chk_extract_participants.isChecked())

        self.cfg["debug_enabled"] = bool(self.chk_debug.isChecked())
        self.cfg["debug_show_panel"] = bool(self.chk_show_debug_panel.isChecked())

        openai = self.in_openai.text().strip()
        deepgram = self.in_deepgram.text().strip()
        aai = self.in_assemblyai.text().strip()
        pplx = self.inpplx.text().strip()
        hft = self.inhftoken.text().strip()

        if openai:
            setsecret(self.cfg, "openai_api_key", openai)
        if deepgram:
            setsecret(self.cfg, "deepgram_api_key", deepgram)
        if aai:
            setsecret(self.cfg, "assemblyai_api_key", aai)
        if pplx:
            setsecret(self.cfg, "perplexityapikey", pplx)
        if hft:
            setsecret(self.cfg, "hf_token", hft)

        saveconfig(self.cfg)
        QMessageBox.information(self, "OK", "Configuration enregistrée.")
        self.accept()
