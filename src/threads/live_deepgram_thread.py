from __future__ import annotations

import asyncio
import queue
import re
import threading
import traceback
import json

from PyQt6.QtCore import QThread, pyqtSignal

from common import log_line
from config.secure_store import getsecret
from services.recorder_service import RecorderService


class LiveDeepgramThread(QThread):
    """
    Thread de transcription live utilisant Deepgram Nova-3 via WebSocket.
    Offre une meilleure précision pour le français (~5% WER vs ~8.3% AssemblyAI).
    """

    live_line = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, cfg: dict, recorder: RecorderService, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.recorder = recorder

        self._stop_flag = False
        self._ratecv_state = None
        self._debug = bool(self.cfg.get("debug_enabled", self.cfg.get("debug", False)))

        source_role = str(self.cfg.get("live_source_role") or "Participants").lower()
        if source_role == "moi":
            self.input_samplerate = int(getattr(self.recorder, "mic_rate", None) or self.recorder.participants_rate or 48000)
            self.input_channels = int(getattr(self.recorder, "mic_channels", None) or 1)
        else:
            self.input_samplerate = int(self.recorder.participants_rate or 48000)
            self.input_channels = int(self.recorder.participants_channels or 2)

        # Deepgram fonctionne mieux avec 16kHz
        self.target_sr = int(self.cfg.get("live_target_sr", 16000) or 16000)

        # Langue source
        self.source_language = str(self.cfg.get("live_source_language") or "auto").lower()

        # Paramètres Deepgram optimisés pour le français
        self.model = str(self.cfg.get("deepgram_model") or "nova-3")
        self.smart_format = bool(self.cfg.get("deepgram_smart_format", True))
        self.punctuate = bool(self.cfg.get("deepgram_punctuate", True))
        # Better utterance grouping with Deepgram endpointing events.
        self.interim_results = bool(self.cfg.get("deepgram_interim_results", True))
        self.utterance_end_ms = int(self.cfg.get("deepgram_utterance_end_ms", 1000) or 1000)
        # Slightly higher endpointing helps avoid cut sentence endings.
        self.endpointing = int(self.cfg.get("deepgram_endpointing", 500) or 500)
        self.vad_events = bool(self.cfg.get("deepgram_vad_events", True))
        voice_mode = str(self.cfg.get("voice_identification_mode") or "").strip().lower()
        if voice_mode not in ("report_only", "live_beta"):
            voice_mode = "live_beta" if bool(self.cfg.get("live_speaker_labels", False)) else "report_only"
        self.enable_speaker_labels = bool(voice_mode == "live_beta" and self.cfg.get("live_speaker_labels", False))

        # Traduction
        self.enable_translation = bool(self.cfg.get("live_enable_translation", False))
        self.translate_model = str(self.cfg.get("live_translate_model") or "gpt-4o-mini")
        if self.source_language == "fr":
            self.enable_translation = False

        # Buffers pour éviter les doublons
        self._last_emitted = ""
        self._last_emitted_norm = ""
        self._translate_q: "queue.Queue[object]" = queue.Queue()
        self._translate_thread: threading.Thread | None = None
        self._openai_client = None

        # Sentence buffering
        self._sentence_buf = ""
        self._emit_seq = 0
        self._buffer_max_chars = int(self.cfg.get("deepgram_buffer_max_chars", 240) or 240)
        self._buffer_max_words = int(self.cfg.get("deepgram_buffer_max_words", 30) or 30)
        self._turn_open = False
        self._turn_id = 0
        self._turn_text = ""
        self._turn_speaker: str | None = None
        self._speaker_map: dict[int, str] = {}
        self._turn_start_s: float | None = None
        self._turn_end_s: float | None = None
        self._audio_seconds = 0.0
        self.enable_delayed_speaker = bool(self.enable_speaker_labels and self.cfg.get("live_speaker_labels_delayed", False))
        self._unknown_label = "Personne non identifié"

    def stop(self):
        self._stop_flag = True
        self._flush_turn(force_emit=True)
        # Flush remaining buffered text
        try:
            if self._sentence_buf.strip():
                self._emit_seq += 1
                self._emit_text(self._emit_seq, self._sentence_buf.strip())
                self._sentence_buf = ""
        except Exception:
            pass
        try:
            self.recorder.live_queue.put_nowait(None)
        except Exception:
            pass
        try:
            self._translate_q.put_nowait(None)
        except Exception:
            pass

    def _emit_text(self, msg_id: int | None, text: str, speaker: str | None = None):
        t = (text or "").strip()
        if not t:
            return

        norm = self._norm_text(t)

        # Déduplication
        if t == self._last_emitted:
            return
        if norm and norm == self._last_emitted_norm and len(t) <= len(self._last_emitted):
            return

        self._last_emitted = t
        self._last_emitted_norm = norm

        turn_prefix = f"[T:{msg_id}] " if msg_id is not None else ""
        spk_prefix = f"[SPK:{speaker}] " if speaker else ""

        if self.source_language == "fr":
            self.live_line.emit(f"{turn_prefix}{spk_prefix}[FR] {t}")
            return

        if self.enable_translation:
            try:
                if self._translate_q.qsize() >= 3:
                    self.live_line.emit(f"{turn_prefix}{spk_prefix}[EN] {t}")
                    return
            except Exception:
                self.live_line.emit(f"{turn_prefix}{spk_prefix}[EN] {t}")
                return
            try:
                self._translate_q.put_nowait((msg_id, t, speaker))
            except Exception:
                self.live_line.emit(f"{turn_prefix}{spk_prefix}[EN] {t}")
        else:
            self.live_line.emit(f"{turn_prefix}{spk_prefix}[EN] {t}")

    def _norm_text(self, text: str) -> str:
        t = (text or "").strip().lower()
        if not t:
            return ""
        t = re.sub(r"[^\w\s]", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _buffer_and_split_sentences(self, text: str, force: bool) -> list[str]:
        """Bufferise les fragments et émet des phrases complètes."""
        t = (text or "").strip()
        t = re.sub(r"\s+", " ", t).strip() if t else ""

        if t:
            if self._sentence_buf:
                self._sentence_buf = (self._sentence_buf.rstrip() + " " + t).strip()
            else:
                self._sentence_buf = t

        buf = (self._sentence_buf or "").strip()
        if not buf:
            return []

        # Split on strong punctuation
        parts = re.split(r"(?<=[.!?…])\s+", buf)
        out: list[str] = []

        if re.search(r"[.!?…]$", buf):
            out = [p.strip() for p in parts if p.strip()]
            self._sentence_buf = ""
        else:
            for p in parts[:-1]:
                p = (p or "").strip()
                if not p:
                    continue
                if not re.search(r"[\w\d]", p, flags=re.UNICODE):
                    continue
                out.append(p)
            self._sentence_buf = (parts[-1] or "").strip()

        # Force flush (speech_final / utterance end)
        if force and self._sentence_buf:
            out.append(self._sentence_buf)
            self._sentence_buf = ""

        # Émettre si trop long
        if self._sentence_buf:
            wc = len(self._sentence_buf.split())
            if len(self._sentence_buf) > self._buffer_max_chars or wc >= self._buffer_max_words:
                out.append(self._sentence_buf)
                self._sentence_buf = ""

        return out

    def _start_translation_worker(self) -> bool:
        if not self.enable_translation:
            return False

        api_key = getsecret(self.cfg, "openai_api_key") or ""
        if not api_key:
            self.status.emit("⚠ OpenAI API key manquante (traduction live)")
            self.enable_translation = False
            return False

        try:
            from openai import OpenAI
        except Exception:
            self.status.emit("⚠ Dépendance OpenAI manquante (pip install openai)")
            self.enable_translation = False
            return False

        self._openai_client = OpenAI(api_key=api_key)

        def _worker():
            while not self._stop_flag:
                item = self._translate_q.get()
                if item is None:
                    break
                if isinstance(item, tuple):
                    turn_order = item[0]
                    text = str(item[1]).strip()
                    speaker = item[2] if len(item) >= 3 else None
                else:
                    turn_order = None
                    text = str(item).strip()
                    speaker = None
                if not text:
                    continue
                try:
                    r = self._openai_client.chat.completions.create(
                        model=self.translate_model,
                        temperature=0,
                        messages=[
                            {"role": "user", "content": "Traduis en français naturel, sans ajouter de contexte :\n" + text},
                        ],
                    )
                    out = (r.choices[0].message.content or "").strip()
                    turn_prefix = f"[T:{turn_order}] " if turn_order is not None else ""
                    spk_prefix = f"[SPK:{speaker}] " if speaker else ""
                    self.live_line.emit(f"{turn_prefix}{spk_prefix}[EN] {text}")
                    if out:
                        self.live_line.emit(f"{turn_prefix}{spk_prefix}[FR] {out}")
                except Exception:
                    turn_prefix = f"[T:{turn_order}] " if turn_order is not None else ""
                    spk_prefix = f"[SPK:{speaker}] " if speaker else ""
                    self.live_line.emit(f"{turn_prefix}{spk_prefix}[EN] {text}")

        self._translate_thread = threading.Thread(target=_worker, daemon=True)
        self._translate_thread.start()
        return True

    def _get_audio_chunks(self):
        """Générateur d'audio converti en mono 16kHz PCM16."""
        import audioop

        buf = bytearray()
        # Deepgram recommande des chunks de 100-250ms
        min_frames = max(1, int(self.target_sr * 0.1))  # 100ms
        max_frames = max(min_frames, int(self.target_sr * 0.25))  # 250ms
        min_bytes = min_frames * 2
        max_bytes = max_frames * 2

        while not self._stop_flag:
            try:
                item = self.recorder.live_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if item is None:
                break

            data_bytes, frames = item
            frames = int(frames) if frames else 0
            if not data_bytes or frames <= 0:
                continue

            # Conversion stéréo -> mono
            if self.input_channels and self.input_channels != 1:
                try:
                    data_bytes = audioop.tomono(data_bytes, 2, 0.5, 0.5)
                except Exception:
                    pass

            # Resampling si nécessaire
            if self.input_samplerate and self.input_samplerate != self.target_sr:
                try:
                    data_bytes, self._ratecv_state = audioop.ratecv(
                        data_bytes, 2, 1, self.input_samplerate, self.target_sr, self._ratecv_state
                    )
                except Exception:
                    pass

            if data_bytes:
                buf.extend(data_bytes)

            while len(buf) >= min_bytes:
                chunk = bytes(buf[:max_bytes])
                del buf[:len(chunk)]
                if chunk:
                    try:
                        self._audio_seconds += len(chunk) / 2.0 / float(self.target_sr)
                    except Exception:
                        pass
                    yield chunk

    def run(self):
        try:
            import websockets.sync.client as ws_client
        except ImportError:
            try:
                # Fallback pour les anciennes versions
                import websockets
                ws_client = None
            except ImportError:
                self.status.emit("⚠ websockets non installé (pip install websockets)")
                return

        api_key = getsecret(self.cfg, "deepgram_api_key") or ""
        if not api_key:
            self.status.emit("⚠ Deepgram API key manquante (Setup > API)")
            return

        self._start_translation_worker()

        def _build_url(mode: str = "full") -> str:
            # mode:
            # - full: all configured params
            # - no_utterance_end: keep quality params but drop utterance_end_ms
            # - minimal: only mandatory params
            params = [
                f"model={self.model}",
                "encoding=linear16",
                f"sample_rate={self.target_sr}",
                "channels=1",
            ]
            if self.source_language and self.source_language != "auto":
                params.append(f"language={self.source_language}")
            if mode != "minimal":
                if self.smart_format:
                    params.append("smart_format=true")
                if self.punctuate:
                    params.append("punctuate=true")
                if self.interim_results:
                    params.append("interim_results=true")
                if self.vad_events:
                    params.append("vad_events=true")
                if mode != "no_utterance_end" and self.utterance_end_ms >= 1000:
                    params.append(f"utterance_end_ms={self.utterance_end_ms}")
                if self.endpointing:
                    params.append(f"endpointing={self.endpointing}")
                if self.enable_speaker_labels:
                    params.append("diarize=true")
            query_string = "&".join(params)
            return f"wss://api.deepgram.com/v1/listen?{query_string}"

        # Build primary URL; on HTTP 400 we progressively degrade parameters.
        url = _build_url(mode="full")

        if self._debug:
            log_line(f"[Deepgram] Connect URL: {url}")

        headers = {"Authorization": f"Token {api_key}"}

        self.status.emit("Live: connexion Deepgram...")

        try:
            if ws_client:
                # Utiliser websockets sync API (version moderne)
                try:
                    with ws_client.connect(url, additional_headers=headers) as ws:
                        self.status.emit(f"Live: Deepgram connecté ({self.model})")
                        self._run_websocket_sync(ws)
                except Exception as e:
                    # Retry with progressive fallback if server rejects the query.
                    try:
                        from websockets.exceptions import InvalidStatus
                        if isinstance(e, InvalidStatus) and getattr(e, "response", None) and e.response.status_code == 400:
                            # Step 1: keep formatting/quality params, drop utterance_end_ms only.
                            url_no_ue = _build_url(mode="no_utterance_end")
                            if self._debug:
                                log_line(f"[Deepgram] Retry without utterance_end_ms: {url_no_ue}")
                            try:
                                with ws_client.connect(url_no_ue, additional_headers=headers) as ws:
                                    self.status.emit(f"Live: Deepgram connecté ({self.model})")
                                    self._run_websocket_sync(ws)
                            except Exception as e2:
                                # Step 2: last resort minimal params.
                                if isinstance(e2, InvalidStatus) and getattr(e2, "response", None) and e2.response.status_code == 400:
                                    url_min = _build_url(mode="minimal")
                                    if self._debug:
                                        log_line(f"[Deepgram] Retry with minimal params: {url_min}")
                                    with ws_client.connect(url_min, additional_headers=headers) as ws:
                                        self.status.emit(f"Live: Deepgram connecté ({self.model})")
                                        self._run_websocket_sync(ws)
                                else:
                                    raise
                        else:
                            raise
                    except Exception:
                        raise
            else:
                # Fallback async
                asyncio.run(self._run_websocket_async(url, headers))

        except Exception:
            err = traceback.format_exc()
            log_line("=== LiveDeepgramThread exception ===\n" + err)
            self.status.emit("Live: erreur Deepgram (voir log)")

        # Flush remaining text
        self._flush_turn(force_emit=True)
        try:
            if self._sentence_buf.strip():
                self._emit_seq += 1
                self._emit_text(self._emit_seq, self._sentence_buf.strip(), self._turn_speaker)
                self._sentence_buf = ""
        except Exception:
            pass
        self.status.emit("Live: Deepgram terminé")

    def _run_websocket_sync(self, ws):
        """Exécution synchrone avec le WebSocket."""
        import threading

        # Thread pour envoyer l'audio
        def send_audio():
            try:
                for chunk in self._get_audio_chunks():
                    if self._stop_flag:
                        break
                    try:
                        ws.send(chunk)
                    except Exception:
                        break
                # Envoyer le message de fin
                try:
                    ws.send(json.dumps({"type": "CloseStream"}))
                except Exception:
                    pass
            except Exception:
                pass

        sender = threading.Thread(target=send_audio, daemon=True)
        sender.start()

        self.status.emit(f"Live: prêt (Deepgram {self.model})")

        # Recevoir les transcriptions
        try:
            while not self._stop_flag:
                try:
                    msg = ws.recv(timeout=1.0)
                    if isinstance(msg, bytes):
                        msg = msg.decode("utf-8")
                    self._handle_message(msg)
                except TimeoutError:
                    continue
                except Exception:
                    break
        except Exception:
            pass

        sender.join(timeout=2.0)

    async def _run_websocket_async(self, url: str, headers: dict):
        """Fallback async pour les anciennes versions de websockets."""
        import websockets

        async with websockets.connect(url, extra_headers=headers) as ws:
            self.status.emit(f"Live: Deepgram connecté ({self.model})")

            async def send_audio():
                try:
                    for chunk in self._get_audio_chunks():
                        if self._stop_flag:
                            break
                        await ws.send(chunk)
                    await ws.send(json.dumps({"type": "CloseStream"}))
                except Exception:
                    pass

            async def receive_messages():
                try:
                    async for msg in ws:
                        if self._stop_flag:
                            break
                        if isinstance(msg, bytes):
                            msg = msg.decode("utf-8")
                        self._handle_message(msg)
                except Exception:
                    pass

            self.status.emit(f"Live: prêt (Deepgram {self.model})")

            await asyncio.gather(send_audio(), receive_messages())

    def _handle_message(self, msg: str):
        """Traite un message JSON de Deepgram."""
        try:
            data = json.loads(msg)
        except Exception:
            return

        msg_type = data.get("type", "")

        if msg_type == "Results":
            channel = data.get("channel", {})
            alternatives = channel.get("alternatives", [])
            if not alternatives:
                return

            transcript = alternatives[0].get("transcript", "")
            if not transcript:
                return
            speaker = self._extract_speaker_label(alternatives[0])
            wstart, wend = self._extract_time_range(alternatives[0])

            is_final = bool(data.get("is_final", False))
            speech_final = bool(data.get("speech_final", False))

            if self._debug:
                log_line(f"[Deepgram] is_final={is_final} speech_final={speech_final} text='{transcript[:200]}'")

            # Ignorer les résultats non-finaux
            if not (is_final or speech_final):
                return

            self._append_turn_chunk(transcript, speaker=speaker, start_s=wstart, end_s=wend)
            if self._turn_open and self._turn_text.strip():
                self._emit_turn_update()
            if speech_final:
                self._flush_turn(force_emit=True)

        elif msg_type == "UtteranceEnd":
            # Flush le buffer quand Deepgram détecte la fin d'une utterance
            self._flush_turn(force_emit=True)

        elif msg_type == "Error":
            error_msg = data.get("message", "Unknown error")
            self.status.emit(f"Live: Deepgram erreur ({error_msg})")
            log_line(f"[Deepgram] Error: {data}")

    def _append_turn_chunk(self, chunk: str, speaker: str | None = None, start_s: float | None = None, end_s: float | None = None):
        t = re.sub(r"\s+", " ", (chunk or "").strip())
        if not t:
            return

        if not self._turn_open:
            self._turn_open = True
            self._emit_seq += 1
            self._turn_id = self._emit_seq
            self._turn_text = t
            self._turn_speaker = speaker
            self._turn_start_s = start_s
            self._turn_end_s = end_s
            return

        self._turn_text = self._merge_final_chunks(self._turn_text, t)
        if speaker:
            self._turn_speaker = speaker
        if start_s is not None:
            if self._turn_start_s is None:
                self._turn_start_s = start_s
            else:
                self._turn_start_s = min(self._turn_start_s, start_s)
        if end_s is not None:
            if self._turn_end_s is None:
                self._turn_end_s = end_s
            else:
                self._turn_end_s = max(self._turn_end_s, end_s)

    def _flush_turn(self, force_emit: bool = False):
        if not self._turn_open:
            return
        txt = (self._turn_text or "").strip()
        if txt and force_emit:
            self._emit_turn_update(final=True)
        self._turn_text = ""
        self._turn_open = False
        self._turn_speaker = None
        self._turn_start_s = None
        self._turn_end_s = None

    def _emit_turn_update(self, final: bool = False):
        txt = (self._turn_text or "").strip()
        if not txt:
            return
        if self.enable_delayed_speaker:
            # Emit unknown speaker first, then update when diarization is confident.
            self._emit_text(self._turn_id, txt, self._unknown_label)
            if final:
                self._emit_diarization_update()
            return
        self._emit_text(self._turn_id, txt, self._turn_speaker)
        if final and self.enable_speaker_labels and self.enable_delayed_speaker:
            self._emit_diarization_update()

    def _emit_diarization_update(self):
        if not self.enable_speaker_labels:
            return
        spk = (self._turn_speaker or "").strip()
        if not spk:
            return
        s = self._turn_start_s
        e = self._turn_end_s
        if s is None or e is None:
            return
        payload = {"utterances": [{"start": float(s), "end": float(e), "speaker": spk}]}
        try:
            self.live_line.emit("[DIA] " + json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    def _extract_speaker_label(self, alt: dict) -> str | None:
        if not self.enable_speaker_labels:
            return None
        words = alt.get("words") if isinstance(alt, dict) else None
        if not isinstance(words, list) or not words:
            return None
        counts: dict[int, int] = {}
        for w in words:
            if not isinstance(w, dict):
                continue
            sid = w.get("speaker")
            try:
                if sid is None:
                    continue
                sid_i = int(sid)
            except Exception:
                continue
            counts[sid_i] = counts.get(sid_i, 0) + 1
        if not counts:
            return None
        sid_best = max(counts.items(), key=lambda kv: kv[1])[0]
        label = self._speaker_map.get(sid_best)
        if not label:
            label = f"PERSONNE {len(self._speaker_map) + 1}"
            self._speaker_map[sid_best] = label
        return label

    def _extract_time_range(self, alt: dict) -> tuple[float | None, float | None]:
        words = alt.get("words") if isinstance(alt, dict) else None
        if not isinstance(words, list) or not words:
            return None, None
        starts = []
        ends = []
        for w in words:
            if not isinstance(w, dict):
                continue
            try:
                s = w.get("start")
                e = w.get("end")
                if s is not None:
                    starts.append(float(s))
                if e is not None:
                    ends.append(float(e))
            except Exception:
                continue
        if not starts or not ends:
            return None, None
        return min(starts), max(ends)

    def _merge_final_chunks(self, base: str, new: str) -> str:
        b = re.sub(r"\s+", " ", (base or "").strip())
        n = re.sub(r"\s+", " ", (new or "").strip())
        if not b:
            return n
        if not n:
            return b

        if n.startswith(b):
            return n
        if b.startswith(n):
            return b

        b_norm = self._norm_text(b)
        n_norm = self._norm_text(n)
        if n_norm and b_norm and n_norm.startswith(b_norm):
            return n
        if n_norm and b_norm and b_norm.startswith(n_norm):
            return b

        b_words = b.split()
        n_words = n.split()
        max_k = min(len(b_words), len(n_words), 14)
        overlap = 0
        for k in range(max_k, 0, -1):
            if [w.lower() for w in b_words[-k:]] == [w.lower() for w in n_words[:k]]:
                overlap = k
                break

        if overlap > 0:
            merged = " ".join(b_words + n_words[overlap:]).strip()
        else:
            merged = (b.rstrip() + " " + n.lstrip()).strip()

        merged = re.sub(r"\b(\w+)\s+\1\b", r"\1", merged, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", merged).strip()
