import time
import wave
import pyaudiowpatch as pyaudio

DURATION = 5
CHUNK = 512
OUT = "participants_loopback.wav"

def main():
    with pyaudio.PyAudio() as p:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])

        # Trouver le device loopback qui correspond à la sortie par défaut
        loop = default_out
        if not loop.get("isLoopbackDevice", False):
            for d in p.get_loopback_device_info_generator():
                if default_out["name"] in d["name"]:
                    loop = d
                    break
            else:
                raise RuntimeError("Loopback device introuvable. Essaie: python -m pyaudiowpatch")

        channels = int(loop["maxInputChannels"]) or 2
        rate = int(loop["defaultSampleRate"])

        wf = wave.open(OUT, "wb")
        wf.setnchannels(channels)
        wf.setsampwidth(pyaudio.get_sample_size(pyaudio.paInt16))
        wf.setframerate(rate)

        def cb(in_data, frame_count, time_info, status):
            wf.writeframes(in_data)
            return (in_data, pyaudio.paContinue)

        with p.open(format=pyaudio.paInt16,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=loop["index"],
                    frames_per_buffer=CHUNK,
                    stream_callback=cb):
            time.sleep(DURATION)

        wf.close()
        print(f"Saved: {OUT} from {loop['name']}")

if __name__ == "__main__":
    main()
