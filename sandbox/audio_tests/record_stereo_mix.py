import time
import numpy as np
import sounddevice as sd
from scipy.io import wavfile

DEVICE_ID = 16          # Mixage stéréo (Realtek HD Audio Stereo input)
SAMPLE_RATE = 48000     # on force un truc standard
CHANNELS = 2
DURATION_SEC = 10

def main():
    print("Recording Stereo Mix...")
    sd.default.device = (DEVICE_ID, None)  # input device only
    sd.default.samplerate = SAMPLE_RATE
    sd.default.channels = CHANNELS

    audio = sd.rec(int(DURATION_SEC * SAMPLE_RATE), dtype="float32")
    sd.wait()

    # Convert float32 [-1, 1] -> int16
    audio_i16 = np.clip(audio, -1.0, 1.0)
    audio_i16 = (audio_i16 * 32767).astype(np.int16)

    out = "stereo_mix_test.wav"
    wavfile.write(out, SAMPLE_RATE, audio_i16)
    print(f"Saved: {out}")

if __name__ == "__main__":
    main()
