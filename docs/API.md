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

The VEVOR sketch starts at 1,000,000 baud. Current host tools negotiate 500,000 baud when `INFO` advertises `baud_switch=1`; older firmware remains at its original rate. Commands are newline-terminated ASCII:

| Command | Reply / action |
|---|---|
| `INFO` | `VEVOR_FSK 1 ...` identifying the bridge and current settings |
| `BAUD 500000` | ACK at the old rate, flush, then switch UART rate; confirm with INFO at the new rate |
| `GAIN 65` | Set the manual gain index |
| `FREQ 2744` | Attempt the LO setting |
| `TONES 808500 1018000` | Update the tone offsets |
| `CAP 150` | Capture then send the binary frame, bypassing the activity gate |
| `MON 150` | Capture, then transfer measurements only if physical FSK activity passes the gate |

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

## Activity-gated acquisition (bridge 0.1.2)

`INFO` advertises `monitor=1` when `MON` is available. The gate is specific to the example's approximately 88.5 µs symbol interval. It requires at least 30 strong tone transitions and circular timing coherence of at least 0.20. It does not validate VEVOR data. `CAP` always bypasses it.

A rejected window returns only text, with no binary payload:

```text
IDLE <count> <elapsed_us> <failed_snapshots> <gain> <post_capture_us> <edges> <coherence>
```

A transferred `MON` candidate uses the normal `FSK` header with one extra final `post_capture_us` field. The host subtracts this processing delay when estimating the window start. `client.capture(150, monitor=True)` returns `idle=True` and no `rows` for an IDLE reply; callers must not treat it as weather data. The dashboard negotiates this feature, uses ordinary CAP for predicted bursts and every 32nd acquisition scan, and remains compatible with older firmware.

`ReceiverProtocolError` marks an unknown binary boundary, invalid metadata, or short read. Close/reset/reconnect before another capture. The dashboard automatically does this with bounded retries; direct API callers own recovery. Fully consumed CRC-bad frames still use `CaptureIntegrityError` when in-place INFO resynchronization succeeds.

## Transfer-rate negotiation and repeated-copy metadata (0.1.3)

`ReceiverClient(preferred_baud=500000)` is the default. Startup/reset is always at 1,000,000 baud. The client only requests BAUD when the feature is advertised, changes its serial-port rate after the old-rate ACK, and requires INFO to confirm the new rate. `preferred_baud=1000000` keeps the original transfer speed. Direct API users must close/reset on failed negotiation.

A recovered packet can include `clock_source=repetition` and `repetition_count`. Dashboard/CLI weather records expose these as `recovery_method` and `repetition_count`. Repetition combining sums measured soft evidence, then requires the unchanged header/checksum/counter checks. It is distinct from a single-copy decode and from checksum-directed bit flipping.

## Sync-template recovery metadata (0.1.4)

Packet `clock_source` / weather `recovery_method` may also be `sync` or `sync_repetition`. A single-copy sync recovery includes its normalized `sync_correlation`. The fallback holds the measured symbol period fixed and fits phase to the known sync waveform; it never selects phase using payload validity. Firmware/protocol are unchanged from 0.1.3, including the negotiated slower transfer rate.

## Persistent diagnostics and burst timing (0.1.5)

The dashboard journals all new attempts to `dashboard/data/diagnostics/captures.jsonl`. `/api/status` includes the latest 50 outcomes, latest 20 failures, and an estimated `timing` object. Receiver details show the records. `/api/diagnostics/export.jsonl` downloads the full journal; `/api/diagnostics/raw/<file>` serves only validated capture filenames inside the designated data directories.

Each record includes an outcome/reason list, profile, measured clock/sync information, available candidate checks labeled by decoder stage, and any USB error details. Header/checksum/counter failures can coexist across candidates; they are observed validation failures, not a diagnosis of weak RF. Failed transferred arrays are stored as NPZ, CRC-bad/short USB bodies as BIN when available. Only the latest 1,000 raw failures are retained; the journal persists. Quiet MON replies contain no transferred raw samples and are explicitly metadata-only. Successful arrays retain the existing capture files. Logging cannot recover past unsaved attempts.

The dashboard estimates the burst period from verified matching-station arrivals. It ignores same-burst copies and excludes intervals with large copy-hop residuals from rate learning. Repeated-copy phase changes are normalized when adjusting the capture phase. Header-only detections may bootstrap initial phase but do not steer an established clock. Only estimates within 19.9–20.1 seconds are accepted, and the learned period is saved with the existing five-minute timing cache.

`decode_clocked(..., snapshot_samples=128)` remains the default. Experimental 192-sample firmware can be built by adding `-DESP32_HARMONIC_RX_SNAPSHOT_SAMPLES=192`; pass its actual snapshot length to the host decoder. The current client includes the INFO-reported length in each capture. This adjusts the sync-template integration model, not the payload checks. The dashboard default remains 128 samples.

`decode_clocked(..., preferred_sensor_id="14AB")` prevents a valid-checksum candidate with a different decoded ID from stopping further recovery. IDs and payload bits are never relabeled. The returned valid-candidate list may still include other IDs for inspection; applications must apply their station filter. The dashboard and CLI do both. A matching-station raw regression now recovers through measured repetition combining after an earlier mismatching-ID candidate.
