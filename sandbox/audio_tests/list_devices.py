import sounddevice as sd

def main():
    print("Host APIs:")
    for i, api in enumerate(sd.query_hostapis()):
        print(f"  [{i}] {api['name']}")

    print("\nDevices:")
    for i, dev in enumerate(sd.query_devices()):
        io = []
        if dev["max_input_channels"] > 0:
            io.append(f"in={dev['max_input_channels']}")
        if dev["max_output_channels"] > 0:
            io.append(f"out={dev['max_output_channels']}")
        print(f"  [{i}] {dev['name']} ({', '.join(io)})")

if __name__ == "__main__":
    main()
