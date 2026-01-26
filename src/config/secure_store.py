from __future__ import annotations

import base64
import json
from pathlib import Path

import win32crypt

APPNAME = "MeetingTranslatorNetwork"


def appdir() -> Path:
    base = Path.home() / "AppData" / "Local" / APPNAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def cfgpath() -> Path:
    return appdir() / "config.json"


def dpapi_encrypt(plaintext: str) -> str:
    blob = plaintext.encode("utf-8")
    encrypted = win32crypt.CryptProtectData(blob, None, None, None, None, 0)
    return base64.b64encode(encrypted).decode("ascii")


def dpapi_decrypt(ciphertext_b64: str) -> str:
    encrypted = base64.b64decode(ciphertext_b64.encode("ascii"))
    decrypted = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1]
    return decrypted.decode("utf-8")


def loadconfig() -> dict:
    p = cfgpath()
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def saveconfig(cfg: dict) -> None:
    cfgpath().write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def setsecret(cfg: dict, key: str, value: str) -> None:
    cfg[key] = {"enc": dpapi_encrypt(value)}


def getsecret(cfg: dict, key: str):
    obj = cfg.get(key)
    if not obj or "enc" not in obj:
        return None
    return dpapi_decrypt(obj["enc"])


load_config = loadconfig
save_config = saveconfig
set_secret = setsecret
get_secret = getsecret
