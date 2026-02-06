from __future__ import annotations

try:
    import pyaudiowpatch as pyaudio
except Exception:  # pragma: no cover - non-windows environments
    pyaudio = None


def list_wasapi_output_devices():
    """Liste des sorties WASAPI (non-loopback)."""
    if pyaudio is None:
        return []
    out = []
    with pyaudio.PyAudio() as p:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        wasapi_index = wasapi["index"]
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if (
                d.get("hostApi") == wasapi_index
                and d.get("maxOutputChannels", 0) > 0
                and not d.get("isLoopbackDevice", False)
            ):
                out.append({"index": int(d["index"]), "name": d["name"]})
    return out


def get_loopback_for_output(output_index: int):
    """
    Retourne le device loopback correspondant à une sortie WASAPI.

    PyAudioWPatch duplique les loopback devices en tant que périphériques d'entrée (souvent suffixés [Loopback]). [web:36]
    Le matching par nom est la méthode utilisée dans leurs exemples. [web:417]
    """
    if pyaudio is None:
        return None
    with pyaudio.PyAudio() as p:
        out_dev = p.get_device_info_by_index(output_index)

        if out_dev.get("isLoopbackDevice", False):
            return out_dev

        for loop in p.get_loopback_device_info_generator():
            if out_dev["name"] in loop["name"]:
                return loop

    return None
