from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import requests
from docx import Document
from docx.shared import Pt

try:
    from argostranslate import translate as argos_translate
except Exception:
    argos_translate = None


# Support 2 formats :
# 1) [HH:MM:SS - HH:MM:SS] SPEAKER: TEXT
# 2) HH:MM:SS - HH:MM:SS SPEAKER TEXT
_TRANSCRIPT_RE = re.compile(
    r"^(?:\[(\d{2}:\d{2}:\d{2})\s*-\s*(\d{2}:\d{2}:\d{2})\]\s+([A-Za-z0-9_]+)\s*:\s*(.*)"
    r"|(\d{2}:\d{2}:\d{2})\s*-\s*(\d{2}:\d{2}:\d{2})\s+([A-Za-z0-9_]+)\s+(.*))$"
)


@dataclass
class TranscriptLine:
    start: str
    end: str
    speaker: str
    text: str


class MeetingSummaryService:
    """
    Génère un DOCX final.
    - Ajoute une table de transcription + traduction FR (si Argos est dispo).
    - Ajoute un résumé via Perplexity si API key fournie (sinon section simple).
    """

    def __init__(self, perplexity_api_key: str = ""):
        self.perplexity_api_key = perplexity_api_key or ""

        # Init translator once (Argos)
        self._translator_en_fr = None
        if argos_translate is not None:
            try:
                langs = argos_translate.get_installed_languages()
                en = next((l for l in langs if l.code == "en"), None)
                fr = next((l for l in langs if l.code == "fr"), None)
                if en and fr:
                    self._translator_en_fr = en.get_translation(fr)
            except Exception:
                self._translator_en_fr = None

    def _parse_transcript(self, transcript_path: Path) -> List[TranscriptLine]:
        lines = transcript_path.read_text(encoding="utf-8", errors="replace").splitlines()
        out: List[TranscriptLine] = []

        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue

            m = _TRANSCRIPT_RE.match(ln)
            if not m:
                continue

            # Groupes pour format 1
            if m.group(1) is not None:
                start, end, spk, text = m.group(1), m.group(2), m.group(3), m.group(4)
            else:
                # Groupes pour format 2
                start, end, spk, text = m.group(5), m.group(6), m.group(7), m.group(8)

            text = (text or "").strip()
            if not text:
                continue

            out.append(TranscriptLine(start=start, end=end, speaker=spk, text=text))

        return out

    def _translate_en_to_fr(self, text: str) -> str:
        if not text.strip():
            return ""
        if self._translator_en_fr is None:
            return ""
        try:
            res = self._translator_en_fr.translate(text)
            return (res or "").strip()
        except Exception:
            return ""

    def _perplexity_summary(self, transcript_text: str) -> str:
        """
        Appel Perplexity (best-effort). Si ça échoue, on renvoie "".
        """
        if not self.perplexity_api_key.strip():
            return ""

        url = "https://api.perplexity.ai/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.perplexity_api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": "sonar",
            "messages": [
                {
                    "role": "system",
                    "content": "Tu es un assistant qui résume des réunions en français, clairement et de façon structurée.",
                },
                {
                    "role": "user",
                    "content": (
                        "Résume cette réunion en français.\n"
                        "- Points clés\n- Décisions\n- Actions / TODO (avec responsables si possible)\n\n"
                        f"Transcription:\n{transcript_text}"
                    ),
                },
            ],
            "temperature": 0.2,
        }

        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            r.raise_for_status()
            data = r.json()
            return (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
        except Exception:
            return ""

    def generate_meeting_docx(
        self,
        session_dir: Path,
        transcript_path: Path,
        title: str = "Résumé de Réunion",
        meeting_date: Optional[str] = None,
        participants: str = "N/A",
    ) -> Path:
        session_dir = Path(session_dir)
        transcript_path = Path(transcript_path)

        meeting_date = meeting_date or datetime.now().strftime("%d/%m/%Y %H:%M")

        doc = Document()

        # Title
        p = doc.add_paragraph()
        r = p.add_run(title)
        r.bold = True
        r.font.size = Pt(20)

        doc.add_paragraph(f"Date: {meeting_date}")
        doc.add_paragraph(f"Participants: {participants}")
        doc.add_paragraph("")

        # Transcript bilingual
        doc.add_heading("Transcription + Traduction (FR)", level=1)

        segs = self._parse_transcript(transcript_path)
        if not segs:
            doc.add_paragraph("(Aucune transcription trouvée.)")
        else:
            table = doc.add_table(rows=1, cols=4)
            hdr = table.rows[0].cells
            hdr[0].text = "Temps"
            hdr[1].text = "Speaker"
            hdr[2].text = "Texte"
            hdr[3].text = "Traduction (FR)"

            # Header bold
            for c in hdr:
                for run in c.paragraphs[0].runs:
                    run.bold = True

            for s in segs:
                row = table.add_row().cells
                row[0].text = f"{s.start} - {s.end}"
                row[1].text = s.speaker
                row[2].text = s.text

                fr = self._translate_en_to_fr(s.text)
                row[3].text = fr if fr else "(Traduction indisponible)"

        doc.add_paragraph("")

        # Summary
        doc.add_heading("Résumé", level=1)
        transcript_text = transcript_path.read_text(encoding="utf-8", errors="replace")
        summary = self._perplexity_summary(transcript_text)

        if summary:
            doc.add_paragraph(summary)
        else:
            doc.add_paragraph("Résumé non disponible (clé Perplexity absente ou erreur d'appel).")

        out_path = session_dir / "Résumé de Réunion.docx"
        doc.save(str(out_path))
        return out_path


# ---------------------------------------------------------------------
# ✅ Wrapper attendu par main.py (pour compatibilité)
# ---------------------------------------------------------------------
def generate_meeting_docx(transcript_path: Path, session_dir: Path, cfg: dict) -> Path:
    """
    Compat main.py :
    generate_meeting_docx(transcript_path=..., session_dir=..., cfg=...)
    """
    transcript_path = Path(transcript_path)
    session_dir = Path(session_dir)

    # Récupère la clé Perplexity depuis secure_store si possible
    perplexity_key = ""
    try:
        from config.secure_store import getsecret
        perplexity_key = getsecret(cfg, "perplexity_api_key") or ""
    except Exception:
        perplexity_key = cfg.get("perplexity_api_key", "") or ""

    title = cfg.get("docx_title", "Résumé de Réunion") or "Résumé de Réunion"
    participants = cfg.get("docx_participants", "N/A") or "N/A"

    service = MeetingSummaryService(perplexity_api_key=perplexity_key)
    return service.generate_meeting_docx(
        session_dir=session_dir,
        transcript_path=transcript_path,
        title=title,
        participants=participants,
    )
