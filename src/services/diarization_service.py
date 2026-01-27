from __future__ import annotations

import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional, Dict

import numpy as np
import torch
from faster_whisper import WhisperModel


# --- HuggingFace Hub compatibility shim ---
# Certaines versions de pyannote.audio appellent hf_hub_download(..., use_auth_token=...)
# alors que certaines versions récentes de huggingface_hub n'acceptent plus ce paramètre.
# On patch hf_hub_download pour rester compatible, sans toucher aux dépendances.

def _patch_hf_hub_download_compat() -> None:
    try:
        import huggingface_hub
        from huggingface_hub import hf_hub_download as _orig

        # Déjà patché ?
        if getattr(huggingface_hub.hf_hub_download, "__mt_patched__", False):
            return

        def _wrapped_hf_hub_download(*args, **kwargs):
            # Remap use_auth_token -> token (nouvelle API)
            if "use_auth_token" in kwargs and "token" not in kwargs:
                kwargs["token"] = kwargs.pop("use_auth_token")
            else:
                kwargs.pop("use_auth_token", None)
            return _orig(*args, **kwargs)

        _wrapped_hf_hub_download.__mt_patched__ = True  # type: ignore[attr-defined]
        huggingface_hub.hf_hub_download = _wrapped_hf_hub_download  # type: ignore[assignment]
    except Exception:
        # Si le patch échoue, on ne bloque pas (on tentera quand même la diarization).
        return


@dataclass
class DiarizationConfig:
    diarization_model: str = "pyannote/speaker-diarization-3.1"
    fallback_model: str = "pyannote/speaker-diarization@2.1"
    device: str = "auto"
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    whisper_beam_size: int = 5
    whisper_vad_filter: bool = True


def resolve_device(device: str) -> str:
    device = (device or "auto").lower()
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device


def resample_linear(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Resample with numpy 1.24.3 compatibility (no copy parameter)."""
    if src_sr == dst_sr or x.size < 2:
        return x.astype(np.float32)

    n = int(round(x.size * dst_sr / float(src_sr)))
    n = max(n, 2)

    xp = np.arange(x.size, dtype=np.float32)
    fp = x.astype(np.float32)
    xnew = np.linspace(0, x.size - 1, num=n, dtype=np.float32)

    return np.interp(xnew, xp, fp)


def load_wav_as_mono_float32(wav_path: Path) -> Tuple[np.ndarray, int]:
    """Charge WAV PCM 16-bit via wave. Retourne mono float32 [-1,1]."""
    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        if sampwidth != 2:
            raise RuntimeError(f"WAV sampwidth != 16-bit (got {sampwidth*8} bits)")

        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

        if n_channels > 1:
            audio = audio.reshape(-1, n_channels).mean(axis=1)

        return audio.astype(np.float32), int(sr)


def _load_pyannote_pipeline(model_id: str, hf_token: str = ""):
    """Charge le pipeline pyannote - Compatible toutes versions."""
    from pyannote.audio import Pipeline

    _patch_hf_hub_download_compat()

    # ✅ Méthode universelle : login d'abord, puis load
    if hf_token:
        try:
            from huggingface_hub import login
            login(token=hf_token, add_to_git_credential=False)
        except Exception as e:
            print(f"Warning: huggingface_hub.login() failed: {e}")

    # Charger le pipeline (sans passer de token, car déjà logged in)
    try:
        return Pipeline.from_pretrained(model_id)
    except Exception as e:
        error_msg = (
            f"❌ Impossible de charger le modèle {model_id}\n\n"
            f"Erreur: {str(e)[:200]}\n\n"
            f"Vérifiez:\n"
            f"1. HuggingFace token valide dans Setup\n"
            f"2. Accepté les conditions sur: https://huggingface.co/{model_id}\n"
            f"3. Connexion internet active\n"
            f"4. Redémarrez l'application après avoir configuré le token\n"
        )
        raise RuntimeError(error_msg)


def diarize_participants_wav(
    wav_path: Path,
    hf_token: str,
    cfg: DiarizationConfig,
) -> List[Tuple[float, float, str]]:
    """Speaker-diarization sur piste participants."""
    mono, sr = load_wav_as_mono_float32(wav_path)
    target_sr = 16000
    mono_16k = resample_linear(mono, sr, target_sr)

    pipeline = None
    try:
        pipeline = _load_pyannote_pipeline(cfg.diarization_model, hf_token)
    except Exception:
        try:
            pipeline = _load_pyannote_pipeline(cfg.fallback_model, hf_token)
        except Exception:
            pipeline = None

    # Si diarization indisponible (token absent / modèle inaccessible / incompat deps),
    # on ne casse PAS le post-process : on retourne un seul speaker sur toute la piste.
    if pipeline is None:
        duration = float(mono_16k.size) / float(target_sr) if target_sr > 0 else 0.0
        return [(0.0, max(duration, 0.0), "SPEAKER_00")]

    device = resolve_device(cfg.device)
    try:
        pipeline.to(torch.device(device))
    except Exception:
        pass

    waveform = torch.from_numpy(mono_16k).unsqueeze(0)
    diar_out = pipeline({"waveform": waveform, "sample_rate": target_sr})

    turns: List[Tuple[float, float, str]] = []
    for turn, _, speaker in diar_out.itertracks(yield_label=True):
        turns.append((float(turn.start), float(turn.end), str(speaker)))

    if not turns:
        duration = float(mono_16k.size) / float(target_sr) if target_sr > 0 else 0.0
        return [(0.0, max(duration, 0.0), "SPEAKER_00")]

    return turns


def transcribe_with_whisper(
    wav_path: Path,
    cfg: DiarizationConfig,
    language: str = "auto",
) -> List[Dict]:
    """Transcription faster-whisper. Retourne segments = [{'start','end','text'}]."""
    device = resolve_device(cfg.device)

    model = WhisperModel(
        cfg.whisper_model,
        device=device,
        compute_type=cfg.whisper_compute_type,
    )

    kwargs = dict(
        beam_size=cfg.whisper_beam_size,
        vad_filter=cfg.whisper_vad_filter,
    )
    if language and language != "auto":
        kwargs["language"] = language

    segments, _ = model.transcribe(str(wav_path), **kwargs)

    out = []
    for s in segments:
        out.append({"start": float(s.start), "end": float(s.end), "text": (s.text or "").strip()})
    return out


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def diarize_and_transcribe_participants(
    wav_path: Path,
    hf_token: str,
    cfg: DiarizationConfig,
    language: str = "auto",
) -> List[Dict]:
    """Retourne une liste de segments alignés speaker+text."""
    turns = diarize_participants_wav(wav_path, hf_token=hf_token, cfg=cfg)
    segments = transcribe_with_whisper(wav_path, cfg=cfg, language=language)

    # Simple alignment by overlap:
    out: List[Dict] = []
    for seg in segments:
        s0, s1 = float(seg["start"]), float(seg["end"])
        txt = _clean_text(seg["text"])

        if not txt:
            continue

        # pick speaker with max overlap
        best_spk = "SPEAKER_00"
        best_ov = 0.0
        for t0, t1, spk in turns:
            ov = max(0.0, min(s1, t1) - max(s0, t0))
            if ov > best_ov:
                best_ov = ov
                best_spk = spk

        out.append(
            {
                "start": s0,
                "end": s1,
                "speaker": best_spk,
                "text": txt,
            }
        )

    return out


def _fmt_ts(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _write_transcript_speakers_txt(path: Path, segs: List[Dict]) -> None:
    lines = []
    for s in segs:
        lines.append(
            f"{_fmt_ts(s['start'])} - {_fmt_ts(s['end'])} {s['speaker']} {s['text']}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def diarize_session(
    wav_path: Path,
    mic_wav_path: Optional[Path],
    session_dir: Path,
    hf_token: str,
    cfg,
) -> str:
    """
    Pipeline de post-process:
    - diarize+transcribe piste participants
    - (optionnel) prendre micro en compte plus tard
    - export transcript-speakers.mix.txt
    """
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    # PostProcessConfig -> DiarizationConfig mapping (simple)
    dcfg = DiarizationConfig()

    language = getattr(cfg, "language", "auto") or "auto"
    quality = getattr(cfg, "quality", "standard") or "standard"

    # switch whisper models based on quality
    if quality == "precise":
        dcfg.whisper_model = "medium"
        dcfg.whisper_compute_type = "int8"
        dcfg.whisper_beam_size = 5
    else:
        dcfg.whisper_model = "small"
        dcfg.whisper_compute_type = "int8"
        dcfg.whisper_beam_size = 5

    segs_p = diarize_and_transcribe_participants(
        wav_path=Path(wav_path),
        hf_token=hf_token or "",
        cfg=dcfg,
        language=language,
    )

    transcript_path = session_dir / "transcript-speakers.mix.txt"
    _write_transcript_speakers_txt(transcript_path, segs_p)

    return str(transcript_path)
