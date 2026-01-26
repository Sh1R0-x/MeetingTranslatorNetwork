from __future__ import annotations

import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional, Dict

import numpy as np
import torch
from faster_whisper import WhisperModel


_PART_RE = re.compile(r"partie(\d+)", re.IGNORECASE)


@dataclass
class DiarizationConfig:
    # Whisper
    model_size: str = "small"
    device: str = "auto"  # "cpu" | "cuda" | "auto"
    compute_type: str = "int8"
    beam_size: int = 5
    vad_filter: bool = True
    vad_min_silence_ms: int = 400

    # Pyannote
    diarization_model: str = "pyannote/speaker-diarization-3.1"
    fallback_model: str = "pyannote/speaker-diarization-community-1"

    # Pour éviter "1 seul speaker" côté participants (à ajuster si besoin)
    min_speakers: Optional[int] = 2
    max_speakers: Optional[int] = None

    # Tuning clustering (best effort selon version pyannote)
    clustering_threshold: Optional[float] = 0.70
    min_cluster_size: Optional[int] = 8

    # Speaker label pour ta piste micro
    mic_speaker_label: str = "ME"


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _wav_duration_seconds(p: Path) -> float:
    with wave.open(str(p), "rb") as wf:
        return float(wf.getnframes()) / float(wf.getframerate())


def _format_ts(seconds: float) -> str:
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    return f"{h:02d}:{m:02d}:{ss:02d}"


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _resample_linear(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr or x.size < 2:
        return x.astype(np.float32, copy=False)
    n = int(round(x.size * (dst_sr / float(src_sr))))
    n = max(n, 2)
    xp = np.arange(x.size, dtype=np.float32)
    fp = x.astype(np.float32, copy=False)
    x_new = np.linspace(0, x.size - 1, num=n, dtype=np.float32)
    return np.interp(x_new, xp, fp).astype(np.float32, copy=False)


def _load_wav_as_mono_float32(wav_path: Path) -> Tuple[np.ndarray, int]:
    """
    Charge WAV PCM 16-bit via wave (évite ffmpeg/torchcodec).
    Retourne mono float32 [-1,1] + sample rate.
    """
    with wave.open(str(wav_path), "rb") as wf:
        channels = int(wf.getnchannels())
        sr = int(wf.getframerate())
        sampwidth = int(wf.getsampwidth())
        nframes = int(wf.getnframes())
        if sampwidth != 2:
            raise RuntimeError(f"WAV non supporté (sampwidth={sampwidth}), attendu 16-bit PCM.")
        raw = wf.readframes(nframes)

    data = np.frombuffer(raw, dtype=np.int16)

    if channels == 1:
        mono_i16 = data
    else:
        frames = data.size // channels
        data = data[: frames * channels].reshape(frames, channels)
        mono_i16 = data.mean(axis=1).astype(np.int16)

    mono = (mono_i16.astype(np.float32) / 32768.0).astype(np.float32)
    return mono, sr


def _pyannote_annotation_from_output(diar_out):
    """
    pyannote.audio 3.x: output est souvent une Annotation (a .itertracks)
    pyannote.audio 4.x: output est souvent un DiarizeOutput et l'Annotation est dans .speaker_diarization
    """
    if hasattr(diar_out, "itertracks"):
        return diar_out
    if hasattr(diar_out, "speaker_diarization"):
        return diar_out.speaker_diarization
    if isinstance(diar_out, dict) and "speaker_diarization" in diar_out:
        return diar_out["speaker_diarization"]
    raise RuntimeError(f"Type diarization inattendu: {type(diar_out)}")


def _load_pyannote_pipeline(model_id: str, hf_token: str):
    from pyannote.audio import Pipeline
    try:
        return Pipeline.from_pretrained(model_id, token=hf_token)
    except TypeError:
        return Pipeline.from_pretrained(model_id, use_auth_token=hf_token)


def _extract_part_index(p: Path) -> int:
    m = _PART_RE.search(p.name)
    if not m:
        return 999999
    try:
        return int(m.group(1))
    except Exception:
        return 999999


def _is_participants(p: Path) -> bool:
    n = p.name.lower()
    return ("participants" in n) or ("audio des participants" in n)


def _is_mic(p: Path) -> bool:
    n = p.name.lower()
    return ("mon audio" in n) or ("micro" in n) or ("mic" in n)


def _best_effort_pipeline_tuning(pipeline, cfg: DiarizationConfig):
    try:
        params = {}
        if cfg.clustering_threshold is not None:
            params.setdefault("clustering", {})["threshold"] = float(cfg.clustering_threshold)
        if cfg.min_cluster_size is not None:
            params.setdefault("clustering", {})["min_cluster_size"] = int(cfg.min_cluster_size)
        if params:
            pipeline.instantiate(params)
    except Exception:
        pass


def diarize_participants_wav(
    wav_path: Path,
    hf_token: str,
    cfg: DiarizationConfig,
) -> List[Tuple[float, float, str]]:
    # load + resample 16k
    mono, sr = _load_wav_as_mono_float32(wav_path)
    target_sr = 16000
    mono_16k = _resample_linear(mono, sr, target_sr)

    # pipeline
    try:
        pipeline = _load_pyannote_pipeline(cfg.diarization_model, hf_token)
    except Exception:
        pipeline = _load_pyannote_pipeline(cfg.fallback_model, hf_token)

    device = _resolve_device(cfg.device)
    try:
        pipeline.to(torch.device(device))
    except Exception:
        pass

    _best_effort_pipeline_tuning(pipeline, cfg)

    waveform = torch.from_numpy(mono_16k[None, :])  # (1, samples)

    diar_kwargs = {}
    if cfg.min_speakers is not None:
        diar_kwargs["min_speakers"] = int(cfg.min_speakers)
    if cfg.max_speakers is not None:
        diar_kwargs["max_speakers"] = int(cfg.max_speakers)

    diar_out = pipeline({"waveform": waveform, "sample_rate": target_sr}, **diar_kwargs)
    ann = _pyannote_annotation_from_output(diar_out)

    turns: List[Tuple[float, float, str]] = []
    for turn, _, speaker in ann.itertracks(yield_label=True):
        turns.append((float(turn.start), float(turn.end), str(speaker)))

    return turns


def transcribe_wav(
    wav_path: Path,
    language: str,
    cfg: DiarizationConfig,
) -> List[Tuple[float, float, str]]:
    # load + resample 16k
    mono, sr = _load_wav_as_mono_float32(wav_path)
    target_sr = 16000
    mono_16k = _resample_linear(mono, sr, target_sr)

    device = _resolve_device(cfg.device)
    wmodel = WhisperModel(cfg.model_size, device=device, compute_type=cfg.compute_type)

    vad_parameters = {"min_silence_duration_ms": int(cfg.vad_min_silence_ms)} if cfg.vad_filter else None
    lang = None if (language or "auto") == "auto" else language

    segments, _info = wmodel.transcribe(
        mono_16k,
        language=lang,
        task="transcribe",
        beam_size=int(cfg.beam_size),
        vad_filter=bool(cfg.vad_filter),
        vad_parameters=vad_parameters,
        condition_on_previous_text=False,
        temperature=0.0,
    )

    out: List[Tuple[float, float, str]] = []
    for seg in segments:
        start = float(getattr(seg, "start", 0.0))
        end = float(getattr(seg, "end", start))
        text = (seg.text or "").strip()
        if not text:
            continue
        out.append((start, end, text))
    return out


def diarize_and_transcribe_participants(
    wav_path: Path,
    hf_token: str,
    language: str,
    cfg: DiarizationConfig,
) -> List[Tuple[float, float, str, str]]:
    turns = diarize_participants_wav(wav_path, hf_token=hf_token, cfg=cfg)
    segs = transcribe_wav(wav_path, language=language, cfg=cfg)

    out: List[Tuple[float, float, str, str]] = []
    for start, end, text in segs:
        best_spk = "SPEAKER_00"
        best_ov = 0.0
        for t0, t1, spk in turns:
            ov = _overlap(start, end, t0, t1)
            if ov > best_ov:
                best_ov = ov
                best_spk = spk
        out.append((start, end, best_spk, text))

    return out


def diarize_session(
    session_dir: Path,
    hf_token: str,
    language: str = "auto",
    out_txt: Optional[Path] = None,
    cfg: Optional[DiarizationConfig] = None,
) -> Path:
    """
    Version améliorée:
    - traite participants + micro sur la même timeline
    - participants => diarization + transcription
    - micro => transcription, speaker fixe ME
    - fusionne et trie par timecode
    """
    cfg = cfg or DiarizationConfig()
    session_dir = Path(session_dir)

    suffix = language if language != "auto" else "mix"
    out_txt = out_txt or (session_dir / f"transcript_speakers_{suffix}.txt")

    wavs = sorted(session_dir.rglob("*.wav"))
    if not wavs:
        raise RuntimeError(f"Aucun WAV trouvé dans: {session_dir}")

    parts_participants = sorted([p for p in wavs if _is_participants(p)], key=_extract_part_index)
    parts_mic = sorted([p for p in wavs if _is_mic(p)], key=_extract_part_index)

    # Fallback si naming différent: on prend par nb de channels
    if not parts_participants or not parts_mic:
        # classer grossièrement via header wav
        by_channels: Dict[int, List[Path]] = {}
        for p in wavs:
            try:
                with wave.open(str(p), "rb") as wf:
                    ch = int(wf.getnchannels())
                by_channels.setdefault(ch, []).append(p)
            except Exception:
                continue
        if not parts_mic and 1 in by_channels:
            parts_mic = sorted(by_channels[1], key=_extract_part_index)
        if not parts_participants:
            # participants souvent 2ch, mais pas toujours
            for ch in sorted(by_channels.keys(), reverse=True):
                if ch != 1 and by_channels[ch]:
                    parts_participants = sorted(by_channels[ch], key=_extract_part_index)
                    break

    if not parts_participants and not parts_mic:
        raise RuntimeError("Aucune piste exploitable (participants/micro) trouvée.")

    # Map part_index -> path
    mp_part = { _extract_part_index(p): p for p in parts_participants }
    mp_mic = { _extract_part_index(p): p for p in parts_mic }
    all_idx = sorted(set(mp_part.keys()) | set(mp_mic.keys()))

    merged: List[Tuple[float, float, str, str]] = []
    offset_ref = 0.0

    for idx in all_idx:
        p_part = mp_part.get(idx)
        p_mic = mp_mic.get(idx)

        dur_part = _wav_duration_seconds(p_part) if p_part else None
        dur_mic = _wav_duration_seconds(p_mic) if p_mic else None

        # Référence temps: participants si dispo sinon mic
        ref_dur = dur_part if dur_part is not None else (dur_mic if dur_mic is not None else 0.0)
        if ref_dur <= 0:
            continue

        # Participants segments (diarized)
        if p_part is not None:
            segs_p = diarize_and_transcribe_participants(
                p_part, hf_token=hf_token, language=language, cfg=cfg
            )
            for s, e, spk, txt in segs_p:
                merged.append((s + offset_ref, e + offset_ref, spk, txt))

        # Mic segments (speaker fixe)
        if p_mic is not None:
            segs_m = transcribe_wav(p_mic, language=language, cfg=cfg)

            # Aligner la timeline mic sur la timeline ref (évite drift sample rate)
            scale = 1.0
            if dur_mic and dur_mic > 0 and dur_part and dur_part > 0:
                scale = float(dur_part) / float(dur_mic)

            for s, e, txt in segs_m:
                merged.append((s * scale + offset_ref, e * scale + offset_ref, cfg.mic_speaker_label, txt))

        offset_ref += ref_dur

    merged.sort(key=lambda x: (x[0], x[1]))

    lines = [f"[{_format_ts(s)} - {_format_ts(e)}] {spk}: {txt}" for s, e, spk, txt in merged]
    out_txt.write_text("\n".join(lines), encoding="utf-8")
    return out_txt
