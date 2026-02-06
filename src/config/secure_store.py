from __future__ import annotations

import base64
import json
import os
from pathlib import Path

APPNAME = "MeetingTranslatorNetwork"

try:
    import keyring
except Exception:  # pragma: no cover - optional at runtime
    keyring = None

try:
    import win32crypt
except Exception:  # pragma: no cover - non-windows
    win32crypt = None


def _is_windows() -> bool:
    return os.name == "nt"


def appdir() -> Path:
    if _is_windows():
        base = Path.home() / "AppData" / "Local" / APPNAME
    elif sys_platform() == "darwin":
        base = Path.home() / "Library" / "Application Support" / APPNAME
    else:
        base = Path.home() / ".config" / APPNAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def sys_platform() -> str:
    try:
        import sys

        return str(sys.platform)
    except Exception:
        return ""


def cfgpath() -> Path:
    return appdir() / "config.json"


def _secret_service_name() -> str:
    return APPNAME


def _secret_ref() -> dict:
    return {"stored": True}


def _is_secret_ref(v) -> bool:
    return isinstance(v, dict) and bool(v.get("stored"))


def _is_legacy_dpapi_blob(v) -> bool:
    return isinstance(v, dict) and isinstance(v.get("enc"), str)


def _sanitize_cfg(cfg: dict) -> dict:
    # Keep only metadata for secrets. Never persist plaintext values here.
    out = dict(cfg or {})
    for k, v in list(out.items()):
        if _is_legacy_dpapi_blob(v):
            # Keep encrypted DPAPI blob (legacy/fallback).
            out[k] = {"enc": str(v.get("enc"))}
        elif _is_secret_ref(v):
            out[k] = _secret_ref()
    return out


def loadconfig() -> dict:
    p = cfgpath()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return _sanitize_cfg(data if isinstance(data, dict) else {})


def saveconfig(cfg: dict) -> None:
    safe_cfg = _sanitize_cfg(cfg)
    cfgpath().write_text(json.dumps(safe_cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def dpapi_encrypt(plaintext: str) -> str:
    if win32crypt is None:
        raise RuntimeError("DPAPI indisponible")
    blob = plaintext.encode("utf-8")
    encrypted = win32crypt.CryptProtectData(blob, None, None, None, None, 0)
    return base64.b64encode(encrypted).decode("ascii")


def dpapi_decrypt(ciphertext_b64: str) -> str:
    if win32crypt is None:
        raise RuntimeError("DPAPI indisponible")
    encrypted = base64.b64decode(ciphertext_b64.encode("ascii"))
    decrypted = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1]
    return decrypted.decode("utf-8")


def _set_os_secret(key: str, value: str) -> bool:
    if keyring is None:
        return False
    try:
        keyring.set_password(_secret_service_name(), str(key), str(value))
        return True
    except Exception:
        return False


def _get_os_secret(key: str):
    if keyring is None:
        return None
    try:
        return keyring.get_password(_secret_service_name(), str(key))
    except Exception:
        return None


def _delete_os_secret(key: str) -> None:
    if keyring is None:
        return
    try:
        keyring.delete_password(_secret_service_name(), str(key))
    except Exception:
        pass


def setsecret(cfg: dict, key: str, value: str) -> None:
    v = str(value or "").strip()
    if not v:
        _delete_os_secret(key)
        cfg.pop(key, None)
        return

    if _set_os_secret(key, v):
        cfg[key] = _secret_ref()
        return

    # Last-resort fallback for Windows if keyring backend is unavailable.
    if _is_windows() and win32crypt is not None:
        cfg[key] = {"enc": dpapi_encrypt(v)}
        return

    raise RuntimeError("Stockage sécurisé indisponible (keyring manquant)")


def getsecret(cfg: dict, key: str):
    # Primary backend: OS keychain/credential manager via keyring.
    v = _get_os_secret(key)
    if v:
        return v

    # Legacy fallback: DPAPI blob in config (Windows old format).
    obj = (cfg or {}).get(key)
    if _is_legacy_dpapi_blob(obj) and win32crypt is not None:
        try:
            plain = dpapi_decrypt(obj["enc"])
            # Migrate to OS secret store when possible.
            _set_os_secret(key, plain)
            cfg[key] = _secret_ref()
            return plain
        except Exception:
            return None
    return None


load_config = loadconfig
save_config = saveconfig
set_secret = setsecret
get_secret = getsecret
