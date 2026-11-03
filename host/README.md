# ESP32-Harmonic-RX host tools

Python transport and VEVOR decoding for the Arduino USB bridge in this repository.

From the repository root:

```sh
python -m pip install -e ./host
python -m esp32_harmonic_rx --replay tests/fixtures/vevor-yt60234-packet.npz
python -m esp32_harmonic_rx --port COM10 --seconds 120
python dashboard/app.py --serial-port COM10
```

Use your board's serial port, such as `/dev/ttyUSB0` on Linux. The command-line receiver searches for packets, then follows the approximately 20-second burst interval. Initial acquisition can take several bursts. Replay output is labeled `recorded`; it does not use a USB receiver.

```python
import numpy as np
from esp32_harmonic_rx import decode_clocked, parse_payload

rows = np.load("tests/fixtures/vevor-yt60234-packet.npz")["rows"]
result, tone_ratio = decode_clocked(rows["us"].astype(float), rows["p0"], rows["p1"])
for packet in result["valid_packets"]:
    print(parse_payload(packet["payload_hex"]))
```

`ReceiverClient` speaks the included `VEVOR_FSK` bridge protocol, not the standard ESP-SDR protocol. `CaptureIntegrityError` means a complete USB frame failed its CRC; discard that frame. A short read or malformed header requires reconnecting before issuing another capture.
