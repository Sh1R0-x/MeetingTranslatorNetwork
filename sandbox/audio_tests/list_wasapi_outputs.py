from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from audio.wasapi_loopback import list_wasapi_output_devices, get_loopback_for_output

outs = list_wasapi_output_devices()
for d in outs:
    lb = get_loopback_for_output(d["index"])
    print(d["index"], d["name"], "=>", (lb["name"] if lb else "NO LOOPBACK"))
