from __future__ import annotations

import queue
import sys
from pathlib import Path

import numpy as np
import sounddevice as sd
import pyaudiowpatch as pyaudio

from PyQt5.QtCore import QTimer, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QProgressBar,
    QLineEdit,
    QMessageBox,
    QCheckBox,
)

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.secure_store import loadconfig, saveconfig, setsecret, getsecret
from audio.wasapi_loopback import list_wasapi_output_devices, get_loopback_for_output


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
            self.peak = max(0.0, min(1.0, pmax))
        else:
            self.peak *= 0.90
        return self.peak

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

        lb = get_loopback_for_output(outputdeviceindex)
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
            return indata, pyaudio.paContinue

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
            self.peak = max(0.0, min(1.0, pmax))
        else:
            self.peak *= 0.90
        return self.peak

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

            _ = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-community-1",
                token=self.hf_token,
            )
            self.done.emit("Modèles diarization téléchargés OK.")
        except Exception as e:
            self.error.emit(str(e))


class SetupWindow(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MeetingTranslator - Configuration")
        self.setMinimumWidth(950)

        self.cfg = loadconfig()

        self.metermic = LevelMeterSoundDevice()
        self.meterparticipants = LevelMeterLoopback()
        self.activetest = None  # "participants" | "micro" | None

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.refreshmeter)

        root = QVBoxLayout()
        root.addWidget(QLabel("Configuration initiale"))
        root.addWidget(QLabel("Participants: sortie Windows capturée en loopback (WASAPI)."))
        root.addWidget(QLabel("Micro: ton micro en entrée."))

        rowp = QHBoxLayout()
        rowp.addWidget(QLabel("Sortie Windows (Participants)"))
        self.cbparticipants = QComboBox()
        rowp.addWidget(self.cbparticipants, 1)
        self.btntestparticipants = QPushButton("Tester")
        rowp.addWidget(self.btntestparticipants)
        root.addLayout(rowp)

        self.pbparticipants = QProgressBar()
        self.pbparticipants.setRange(0, 1000)
        self.pbparticipants.setValue(0)
        root.addWidget(self.pbparticipants)

        rowm = QHBoxLayout()
        rowm.addWidget(QLabel("Micro (toi)"))
        self.cbmicro = QComboBox()
        rowm.addWidget(self.cbmicro, 1)
        self.btntestmicro = QPushButton("Tester")
        rowm.addWidget(self.btntestmicro)
        root.addLayout(rowm)

        self.pbmicro = QProgressBar()
        self.pbmicro.setRange(0, 1000)
        self.pbmicro.setValue(0)
        root.addWidget(self.pbmicro)

        self.lblstatus = QLabel("Statut: prêt")
        root.addWidget(self.lblstatus)

        root.addWidget(QLabel("Clés API (optionnel)"))

        rowd = QHBoxLayout()
        rowd.addWidget(QLabel("DeepL API Key"))
        self.indeepl = QLineEdit()
        self.indeepl.setEchoMode(QLineEdit.Password)
        rowd.addWidget(self.indeepl, 1)
        root.addLayout(rowd)

        rowpx = QHBoxLayout()
        rowpx.addWidget(QLabel("Perplexity API Key"))
        self.inpplx = QLineEdit()
        self.inpplx.setEchoMode(QLineEdit.Password)
        rowpx.addWidget(self.inpplx, 1)
        root.addLayout(rowpx)

        # ✅ OpenAI
        rowoa = QHBoxLayout()
        rowoa.addWidget(QLabel("OpenAI API Key (Realtime)"))
        self.inoai = QLineEdit()
        self.inoai.setEchoMode(QLineEdit.Password)
        rowoa.addWidget(self.inoai, 1)
        root.addLayout(rowoa)

        if getsecret(self.cfg, "deeplapikey"):
            self.indeepl.setPlaceholderText("Déjà configuré (laisser vide pour ne pas changer)")
        if getsecret(self.cfg, "perplexityapikey"):
            self.inpplx.setPlaceholderText("Déjà configuré (laisser vide pour ne pas changer)")
        if getsecret(self.cfg, "openaiapikey"):
            self.inoai.setPlaceholderText("Déjà configuré (laisser vide pour ne pas changer)")

        root.addWidget(QLabel("Speaker A/B (optionnel, post-process)"))

        self.chk_diar = QCheckBox("Activer Speaker A/B (diarization après STOP)")
        self.chk_diar.setChecked(bool(self.cfg.get("enable_diarization", False)))
        root.addWidget(self.chk_diar)

        rowhf = QHBoxLayout()
        rowhf.addWidget(QLabel("HuggingFace Token (read)"))
        self.inhftoken = QLineEdit()
        self.inhftoken.setEchoMode(QLineEdit.Password)
        rowhf.addWidget(self.inhftoken, 1)
        root.addLayout(rowhf)

        if getsecret(self.cfg, "hf_token"):
            self.inhftoken.setPlaceholderText("Déjà configuré (laisser vide pour ne pas changer)")

        rowdl = QHBoxLayout()
        self.btndownloadmodels = QPushButton("Télécharger/Tester modèles diarization")
        rowdl.addWidget(self.btndownloadmodels)
        root.addLayout(rowdl)

        rowbtn = QHBoxLayout()
        self.btnrefresh = QPushButton("Rafraîchir la liste")
        self.btnsave = QPushButton("Enregistrer et fermer")
        rowbtn.addWidget(self.btnrefresh)
        rowbtn.addStretch(1)
        rowbtn.addWidget(self.btnsave)
        root.addLayout(rowbtn)

        self.setLayout(root)

        self.btnrefresh.clicked.connect(self.loaddevices)
        self.btntestparticipants.clicked.connect(lambda: self.toggletest("participants"))
        self.btntestmicro.clicked.connect(lambda: self.toggletest("micro"))
        self.btnsave.clicked.connect(self.save)

        self.btndownloadmodels.clicked.connect(self.download_models)
        self._dlthread = None

        self.loaddevices()

    def closeEvent(self, event):
        self.stoptest()
        self.reject()
        event.accept()

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
        self.cfg["enable_diarization"] = bool(self.chk_diar.isChecked())

        deepl = self.indeepl.text().strip()
        pplx = self.inpplx.text().strip()
        oai = self.inoai.text().strip()
        hft = self.inhftoken.text().strip()

        if deepl:
            setsecret(self.cfg, "deeplapikey", deepl)
        if pplx:
            setsecret(self.cfg, "perplexityapikey", pplx)
        if oai:
            setsecret(self.cfg, "openaiapikey", oai)
        if hft:
            setsecret(self.cfg, "hf_token", hft)

        saveconfig(self.cfg)
        QMessageBox.information(self, "OK", "Configuration enregistrée.")
        self.accept()
