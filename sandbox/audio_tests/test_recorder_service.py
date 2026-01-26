from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from config.secure_store import load_config
from services.recorder_service import RecorderService

cfg = load_config()
out_id = cfg["participants_output_device_id"]
mic_id = cfg["micro_device_id"]

rec = RecorderService(
    participants_output_device_id=out_id,
    mic_device_id=mic_id,
    mp3_bitrate_kbps=256,
    participants_label="Audio des participants",
    my_audio_label="Mon audio"
)

print("START (10s) -> fais jouer YouTube + parle au micro")
rec.start()
time.sleep(10)
rec.stop()
print("STOP. Va dans Documents\\MeetingTranslator\\<date> et écoute les MP3.")
