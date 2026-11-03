# ESP32-Harmonic-RX host tools

Python transport, recorded-capture decoding, and a local dashboard for the ESP32 receiver in this repository. Current firmware decodes live VEVOR packets on the board and sends weather reports over USB. The PC RF decoder remains available for replay and legacy firmware.

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

`ReceiverClient` speaks the included `VEVOR_FSK` protocol, not the standard ESP-SDR protocol. Use `client.weather(150)` with firmware advertising `weather=1`; `client.capture(150)` explicitly requests raw RF measurements for diagnostics. `CaptureIntegrityError` means a complete USB frame failed its CRC; discard that frame. A short read or malformed header requires reconnecting before issuing another capture.

The dashboard's unit selector coordinates temperature, wind, gusts, and cumulative rain: **°F / mph / in** or **°C / km/h / mm**. Stored and exported units remain explicit. See the [dashboard guide and current screenshots](../docs/DASHBOARD.md) and [USB protocol](../docs/API.md).
