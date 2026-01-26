import numpy as np
import sounddevice as sd
from scipy.io import wavfile

DEVICE_ID = 6           # Microphone (Logitech G733 Gaming Headset) (in=2)
SAMPLE_RATE = 48000
CHANNELS = 1            # on force 1 channel, même si le device annonce 2
DURATION_SEC = 10

def main():
    print("Recording Mic...")
    sd.default.device = (DEVICE_ID, None)
    sd.default.samplerate = SAMPLE_RATE
    sd.default.channels = CHANNELS

    audio = sd.rec(int(DURATION_SEC * SAMPLE_RATE), dtype="float32")
    sd.wait()

    audio_i16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    out = "mic_test.wav"
    wavfile.write(out, SAMPLE_RATE, audio_i16)
    print(f"Saved: {out}")

if __name__ == "__main__":
    main()
