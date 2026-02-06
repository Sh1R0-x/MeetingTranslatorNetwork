from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from faster_whisper import WhisperModel
from scipy.signal import resample_poly


@dataclass
class Segment:
    start: float
    end: float
    speaker: str
    text: str


def _wav_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return float(frames) / float(rate) if rate else 0.0
    except Exception:
        return 0.0


def _read_wav_mono_float32(path: Path) -> Tuple[int, np.ndarray]:
    try:
        with wave.open(str(path), "rb") as wf:
            channels = int(wf.getnchannels())
            sr = int(wf.getframerate())
            frames = int(wf.getnframes())
            raw = wf.readframes(frames)
        data = np.frombuffer(raw, dtype=np.int16)
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        return sr, (data.astype(np.float32) / 32768.0)
    except Exception:
        return 0, np.zeros((0,), dtype=np.float32)


def _resample_mono(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr <= 0 or dst_sr <= 0 or x.size == 0:
        return np.zeros((0,), dtype=np.float32)
    if src_sr == dst_sr:
        return x.astype(np.float32)
    g = math.gcd(int(src_sr), int(dst_sr))
    up = int(dst_sr // g)
    down = int(src_sr // g)
    y = resample_poly(x.astype(np.float32), up=up, down=down)
    return y.astype(np.float32)


def _write_wav_mono_i16(path: Path, sr: int, x: np.ndarray) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    y = np.clip(x, -1.0, 1.0)
    data = (y * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sr))
        wf.writeframes(data.tobytes())


def _emit_progress(progress_cb, stage: str, ratio: float, message: str) -> None:
    if not callable(progress_cb):
        return
    try:
        r = max(0.0, min(1.0, float(ratio)))
    except Exception:
        r = 0.0
    try:
        progress_cb(str(stage), r, str(message))
    except Exception:
        pass


def _mix_tracks_for_diarization(
    participants_path: Path,
    mic_path: Optional[Path],
    session_dir: Path,
    target_sr: int = 16000,
) -> Path:
    session_dir = Path(session_dir)
    out_path = session_dir / "mix_for_diarization.wav"

    sr_p, x_p = _read_wav_mono_float32(Path(participants_path))
    x_p = _resample_mono(x_p, sr_p, target_sr) if sr_p else np.zeros((0,), dtype=np.float32)

    xs = [x_p]

    if mic_path and Path(mic_path).exists():
        sr_m, x_m = _read_wav_mono_float32(Path(mic_path))
        x_m = _resample_mono(x_m, sr_m, target_sr) if sr_m else np.zeros((0,), dtype=np.float32)
        xs.append(x_m)

    max_len = max((x.size for x in xs), default=0)
    if max_len <= 0:
        _write_wav_mono_i16(out_path, target_sr, np.zeros((0,), dtype=np.float32))
        return out_path

    mix = np.zeros((max_len,), dtype=np.float32)
    for x in xs:
        if x.size < max_len:
            pad = np.zeros((max_len - x.size,), dtype=np.float32)
            x = np.concatenate([x, pad])
        mix += x

    mix /= max(1.0, float(len(xs)))
    _write_wav_mono_i16(out_path, target_sr, mix)
    return out_path


def _pick_whisper_model(cfg) -> str:
    # Keep it simple & stable
    q = str(getattr(cfg, "quality", "") or "").lower()
    if q in ("precise", "high", "best"):
        return "medium"
    return "small"


def _pick_language(cfg) -> Optional[str]:
    lang = str(getattr(cfg, "language", "") or "").strip().lower()
    if not lang or lang == "auto":
        return None
    return lang


def _pick_whisper_device_and_compute(cfg) -> Tuple[str, str]:
    quality = str(getattr(cfg, "quality", "") or "").lower()
    device_pref = str(getattr(cfg, "device", "") or "auto").lower()

    try:
        import torch
    except Exception:
        torch = None

    has_cuda = bool(torch and torch.cuda.is_available())

    if device_pref == "cuda" and has_cuda:
        return "cuda", "float16"
    if device_pref == "cpu":
        return "cpu", "float32" if quality in ("precise", "high", "best") else "int8"
    if has_cuda:
        return "cuda", "float16"
    return "cpu", "float32" if quality in ("precise", "high", "best") else "int8"


def _transcribe_whisper(
    model: WhisperModel,
    wav_path: Path,
    language: Optional[str],
    progress_cb: Optional[Callable[[float], None]] = None,
) -> List[Tuple[float, float, str]]:
    # Conservative threading on Windows to avoid native crashes
    # If the installed faster-whisper doesn't accept these args, it will raise TypeError, so we keep defaults.
    segments: List[Tuple[float, float, str]] = []
    try:
        gen, info = model.transcribe(
            str(wav_path),
            language=language,
            vad_filter=True,
            beam_size=5,
        )
    except TypeError:
        gen, info = model.transcribe(str(wav_path), language=language)

    duration = 0.0
    try:
        duration = float(getattr(info, "duration", 0.0) or 0.0)
    except Exception:
        duration = 0.0
    if duration <= 0.0:
        duration = max(0.0, _wav_duration_seconds(Path(wav_path)))

    last_ratio = -1.0
    for s in gen:
        txt = (s.text or "").strip()
        if not txt:
            continue
        start_s = float(s.start)
        end_s = float(s.end)
        segments.append((start_s, end_s, txt))
        if callable(progress_cb) and duration > 0:
            ratio = max(0.0, min(1.0, end_s / duration))
            if ratio >= 1.0 or (ratio - last_ratio) >= 0.01:
                try:
                    progress_cb(ratio)
                except Exception:
                    pass
                last_ratio = ratio

    if callable(progress_cb):
        try:
            progress_cb(1.0)
        except Exception:
            pass
    return segments


def _transcribe_file(
    model: WhisperModel,
    wav_path: Path,
    speaker: str,
    language: Optional[str],
    progress_cb: Optional[Callable[[float], None]] = None,
) -> List[Segment]:
    segments = _transcribe_whisper(model, wav_path, language=language, progress_cb=progress_cb)
    return [Segment(start=s, end=e, speaker=speaker, text=t) for (s, e, t) in segments]


def _merge_segments(a: List[Segment], b: List[Segment]) -> List[Segment]:
    all_segs = list(a) + list(b)
    all_segs.sort(key=lambda x: (x.start, x.end))
    return all_segs


def _format_segments(segs: List[Segment]) -> str:
    def fmt_ts(t: float) -> str:
        if t < 0:
            t = 0.0
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    lines = []
    for s in segs:
        if s.speaker:
            lines.append(f"[{fmt_ts(s.start)} - {fmt_ts(s.end)}] {s.speaker}: {s.text}")
        else:
            lines.append(f"[{fmt_ts(s.start)} - {fmt_ts(s.end)}] {s.text}")
    return "\n".join(lines) + ("\n" if lines else "")


def _run_pyannote_diarization(
    wav_path: Path,
    model_name: str,
    hf_token: str,
    device: str,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
) -> List[Tuple[float, float, str]]:
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(model_name, use_auth_token=hf_token)

    if device == "cuda":
        try:
            import torch

            if torch.cuda.is_available():
                pipeline = pipeline.to(torch.device("cuda"))
        except Exception:
            pass

    kwargs = {}
    if isinstance(min_speakers, int) and min_speakers > 0:
        kwargs["min_speakers"] = int(min_speakers)
    if isinstance(max_speakers, int) and max_speakers > 0:
        kwargs["max_speakers"] = int(max_speakers)
    try:
        diar = pipeline(str(wav_path), **kwargs) if kwargs else pipeline(str(wav_path))
    except TypeError:
        diar = pipeline(str(wav_path))
    segments: List[Tuple[float, float, str]] = []
    for seg, _track, label in diar.itertracks(yield_label=True):
        segments.append((float(seg.start), float(seg.end), str(label)))
    return segments


def _count_unique_labels(diar_segs: List[Tuple[float, float, str]]) -> int:
    labels = {lbl for _s, _e, lbl in diar_segs if lbl is not None}
    return len(labels)


def _normalize_speaker_labels(diar_segs: List[Tuple[float, float, str]]) -> Dict[str, str]:
    order: List[str] = []
    for _s, _e, lbl in diar_segs:
        if lbl not in order:
            order.append(lbl)
    return {lbl: f"SPEAKER_{i:02d}" for i, lbl in enumerate(order)}


def _assign_speakers_to_transcript(
    transcript_segs: List[Tuple[float, float, str]],
    diar_segs: List[Tuple[float, float, str]],
) -> List[Segment]:
    if not diar_segs:
        return [Segment(start=s, end=e, speaker="SPEAKER_00", text=t) for (s, e, t) in transcript_segs]

    label_map = _normalize_speaker_labels(diar_segs)

    out: List[Segment] = []
    for s, e, text in transcript_segs:
        best_lbl = None
        best_ov = 0.0
        for ds, de, lbl in diar_segs:
            ov = min(e, de) - max(s, ds)
            if ov > best_ov:
                best_ov = ov
                best_lbl = lbl
        if best_lbl is None:
            speaker = "SPEAKER_??"
        else:
            speaker = label_map.get(best_lbl, "SPEAKER_??")
        out.append(Segment(start=s, end=e, speaker=speaker, text=text))
    return out


def diarize_session(
    wav_path: Path,
    mic_wav_path: Optional[Path],
    session_dir: Path,
    hf_token: str,
    cfg,
    progress_cb: Optional[Callable[[str, float, str], None]] = None,
) -> str:
    """
    Voice-based diarization (pyannote) when available, with fallback to 2-track labeling.
    """
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    wav_path = Path(wav_path)
    mic_path = Path(mic_wav_path) if mic_wav_path else None
    _emit_progress(progress_cb, "prepare", 0.0, "Préparation des fichiers audio")

    model_name = _pick_whisper_model(cfg)
    language = _pick_language(cfg)

    # Prefer voice diarization if HF token is present and pyannote is available
    diar_mode = str(getattr(cfg, "diarization_mode", "") or "").lower()
    if diar_mode not in ("voice", "source"):
        diar_mode = "voice"
    enable_diarization = bool(getattr(cfg, "enable_diarization", True))
    use_voice_diarization = diar_mode == "voice" and bool(hf_token) and enable_diarization
    diarization_model = str(getattr(cfg, "diarization_model", "") or "pyannote/speaker-diarization-3.1")
    fallback_model = str(getattr(cfg, "fallback_model", "") or "pyannote/speaker-diarization")

    device, compute_type = _pick_whisper_device_and_compute(cfg)
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    _emit_progress(progress_cb, "prepare", 1.0, "Préparation terminée")

    merged: List[Segment] = []
    hide_fallback_speakers = False

    if use_voice_diarization:
        try:
            _emit_progress(progress_cb, "diarization", 0.05, "Préparation mix audio")
            mix_path = _mix_tracks_for_diarization(wav_path, mic_path, session_dir=session_dir, target_sr=16000)
            _emit_progress(progress_cb, "diarization", 0.25, "Analyse des voix en cours")
            diar_segs = _run_pyannote_diarization(mix_path, diarization_model, hf_token, device=device)
            if not diar_segs and fallback_model:
                _emit_progress(progress_cb, "diarization", 0.45, "Relance modèle de secours")
                diar_segs = _run_pyannote_diarization(mix_path, fallback_model, hf_token, device=device)

            # Heuristic: if only 1 speaker is detected on a long file, retry with constraints
            if diar_segs:
                try:
                    dur_s = _wav_duration_seconds(mix_path)
                except Exception:
                    dur_s = 0.0
                if dur_s >= 30.0 and _count_unique_labels(diar_segs) <= 1:
                    _emit_progress(progress_cb, "diarization", 0.65, "Affinage des voix détectées")
                    diar_retry = _run_pyannote_diarization(
                        mix_path,
                        diarization_model,
                        hf_token,
                        device=device,
                        min_speakers=2,
                        max_speakers=6,
                    )
                    if _count_unique_labels(diar_retry) > _count_unique_labels(diar_segs):
                        diar_segs = diar_retry
            _emit_progress(progress_cb, "diarization", 1.0, "Analyse des voix terminée")
            if diar_segs and _count_unique_labels(diar_segs) > 1:
                def _voice_transcribe_progress(r: float) -> None:
                    _emit_progress(progress_cb, "transcription", r, "Transcription du mix audio")

                transcript_segs = _transcribe_whisper(
                    model,
                    mix_path,
                    language=language,
                    progress_cb=_voice_transcribe_progress,
                )
                merged = _assign_speakers_to_transcript(transcript_segs, diar_segs)
            else:
                merged = []
                hide_fallback_speakers = True
        except Exception:
            merged = []
            hide_fallback_speakers = True
    else:
        # Voice diarization requested but unavailable (no token) -> hide labels in fallback
        if diar_mode == "voice":
            hide_fallback_speakers = True

    if not merged:
        spk_part = "" if hide_fallback_speakers else "SPEAKER_00"
        dur_part = max(0.0, _wav_duration_seconds(wav_path))
        dur_mic = max(0.0, _wav_duration_seconds(mic_path)) if mic_path and mic_path.exists() else 0.0
        total_dur = max(0.001, dur_part + dur_mic)
        part_weight = dur_part / total_dur if total_dur > 0 else 0.5

        def _part_transcribe_progress(r: float) -> None:
            _emit_progress(progress_cb, "transcription", r * part_weight, "Transcription piste participants")

        participants = _transcribe_file(
            model,
            wav_path,
            speaker=spk_part,
            language=language,
            progress_cb=_part_transcribe_progress,
        )
        mic_segs: List[Segment] = []
        if mic_path and mic_path.exists():
            spk_mic = "" if hide_fallback_speakers else "SPEAKER_01"

            def _mic_transcribe_progress(r: float) -> None:
                base = part_weight
                mic_weight = max(0.0, 1.0 - part_weight)
                _emit_progress(progress_cb, "transcription", base + (r * mic_weight), "Transcription piste micro")

            mic_segs = _transcribe_file(
                model,
                mic_path,
                speaker=spk_mic,
                language=language,
                progress_cb=_mic_transcribe_progress,
            )
        merged = _merge_segments(participants, mic_segs)
    _emit_progress(progress_cb, "transcription", 1.0, "Transcription terminée")

    out_mix = session_dir / "transcript-speakers.mix.txt"
    out_fr = session_dir / "transcript_speakers_fr.txt"

    text = _format_segments(merged)
    _emit_progress(progress_cb, "write", 0.5, "Écriture des fichiers transcription")
    out_mix.write_text(text, encoding="utf-8")
    out_fr.write_text(text, encoding="utf-8")
    _emit_progress(progress_cb, "write", 1.0, "Fichiers transcription écrits")

    return str(out_mix)
