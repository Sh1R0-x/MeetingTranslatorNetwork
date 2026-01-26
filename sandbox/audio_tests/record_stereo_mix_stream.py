import queue
import time
import numpy as np
import sounddevice as sd
from scipy.io import wavfile

DEVICE_ID = 16          # Mixage stéréo
CHANNELS = 2
DURATION_SEC = 10

def main():
    dev = sd.query_devices(DEVICE_ID, "input")
    sr = int(dev["default_samplerate"])
    print(f"Device: {dev['name']}")
    print(f"Using samplerate: {sr} Hz")

    q = queue.Queue()
    frames_total = int(DURATION_SEC * sr)
    frames_got = 0

    # blocksize plus grand = plus stable (moins d'overflows)
    blocksize = 2048

    def callback(indata, frames, time_info, status):
        if status:
            # Si tu vois overflow ici, c'est une cause directe de crackling
            print(status)
        q.put(indata.copy())

    with sd.InputStream(
        device=DEVICE_ID,
        channels=CHANNELS,
        samplerate=sr,
        blocksize=blocksize,
        latency="high",   # recommandé pour stabilité [docs sounddevice]
        dtype="float32",
        callback=callback,
    ):
        print("Recording...")
        chunks = []
        while frames_got < frames_total:
            data = q.get()
            chunks.append(data)
            frames_got += len(data)

    audio = np.concatenate(chunks, axis=0)[:frames_total]

    audio_i16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    wavfile.write("stereo_mix_stream.wav", sr, audio_i16)
    print("Saved: stereo_mix_stream.wav")

if __name__ == "__main__":
    main()
