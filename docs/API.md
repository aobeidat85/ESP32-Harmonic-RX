# API and USB protocol

## Arduino capture API

Include `Esp32HarmonicRx.h` and use the `esp32_harmonic_rx` namespace.

| Member | Meaning |
|---|---|
| `Receiver::begin(Config)` | Reserve/validate receiver ownership and initialize the private PHY path; inspect `lastError()` on failure |
| `Config::tuningMHz` | Requested LO attempt; 2400–2800 accepted by this implementation; acceptance is not proof of lock |
| `Config::gainIndex` | Integer PHY table index 0–81; tested default 65, not dB |
| `Config::tone0Hz`, `tone1Hz` | Distinct positive physical offsets below 7 MHz; defaults 808500 and 1018000 Hz |
| `setTuningMHz`, `setGainIndex`, `setToneOffsets` | Update the corresponding setting after `begin()` |
| `capture(output, capacity, windowMs)` | Capture 10–150 ms into caller-owned storage; at most 6144 observations |
| `end()` | Stop/deinitialize the radio and release library ownership |

Use global/static output storage. The library rejects an output buffer that overlaps the reserved capture aperture. The board must be an original dual-core ESP32 running 240 MHz, and calls must run on Arduino core 1. The library cannot share its radio with an existing Wi-Fi application. The supported backend is pinned to Arduino-ESP32 2.0.17.

`CaptureResult` includes `count`, `elapsedUs`, `failedSnapshots`, `truncated`, and `error`. Always check them. `Observation` is 12 bytes: a 32-bit timestamp in microseconds followed by two 32-bit floating-point energies. Power is relative matched-filter energy, not RF dBm.

## Included USB bridge

The VEVOR sketch uses 1,000,000 baud. Commands are newline-terminated ASCII:

| Command | Reply / action |
|---|---|
| `INFO` | `VEVOR_FSK 1 ...` identifying the bridge and current settings |
| `GAIN 65` | Set the manual gain index |
| `FREQ 2744` | Attempt the LO setting |
| `TONES 808500 1018000` | Update the tone offsets |
| `CAP 150` | Capture then send the binary frame |

A successful capture header is:

```text
FSK <observation_count> <crc32_hex> <elapsed_us> <failed_snapshots> <gain_index>
```

The header is followed immediately by `observation_count * 12` binary bytes, with no text inserted into the payload. Each row is little-endian `uint32 timeUs, float32 tone0Power, float32 tone1Power`. The CRC covers exactly the binary payload. The Python NumPy field names are `us`, `p0`, and `p1`.

An `ERR` reply has no binary payload. A short read or malformed header means the receiver must reconnect before further commands. A complete frame that fails CRC has a known boundary and can be discarded safely.

## Python API

```python
from esp32_harmonic_rx import ReceiverClient, CaptureIntegrityError

client = ReceiverClient(port="COM10")
try:
    capture = client.capture(150)
    rows = capture["rows"]
finally:
    client.close()
```

`decode_clocked(t_us, p0, p1)` returns `(result, log_tone_ratio)`. `result['valid_packets']` contains only packets passing header, checksum, and counter checks. Timing estimates, sync errors, and unvalidated candidates are diagnostics; candidates are not accepted weather readings.

`parse_payload(hex_or_bytes)` rechecks a 21-byte payload and returns temperature, humidity, wind, rain, UV, light, battery, sensor ID, and counters. Field interpretation follows the referenced `rtl_433` VEVOR decoder.
