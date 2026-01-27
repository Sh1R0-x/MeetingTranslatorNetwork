from __future__ import annotations

from pathlib import Path

APP_NAME = "MeetingTranslator"
APP_VERSION = "2026.01"
DEFAULT_SESSIONS_DIR = Path.home() / "MeetingTranslatorSessions"

# Debug log to file (helps when bundled)
DEBUG_ENABLED = True
LOG_PATH = Path.home() / "MeetingTranslatorNetwork_debug.log"


def log_line(msg: str):
    if not DEBUG_ENABLED:
        return
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass
