# ESP32-Harmonic-RX

**Experimental two-tone RF capture with an original ESP32, with a VEVOR YT60234 weather-station example.**

An Arduino capture library, Python decoder, and local dashboard developed from a working bench experiment. Receiver hardware is one ESP32 development board and its USB cable; a PC performs packet decoding and displays the results.

> **RF interpretation:** VEVOR weather payloads were recovered with a requested receive setting of **2744 MHz**, near an apparent **2.745 GHz** signal. A third harmonic of the nominal 915 MHz transmitter is a plausible explanation. Absolute frequency/PLL lock and the harmonic origin have not been independently measured. **This project does not demonstrate native 915 MHz reception.**

![VEVOR weather dashboard](docs/images/dashboard-live.jpg)

[Full dashboard screenshot](docs/images/dashboard-full.jpg) · [How it works](docs/HOW_IT_WORKS.md) · [Validation and limitations](docs/VALIDATION.md)

## The idea

Instead of transferring every I/Q sample over a slow USB-to-UART bridge, the ESP32 repeatedly captures a short RF snapshot and measures the energy at two configured tone offsets. It sends those two energies with actual timestamps. The PC reconstructs the FSK symbol clock, finds the VEVOR packet, validates it, and extracts the weather fields.

The ESP32 captures ** 128 nominal 16 MS/s samples per observation** and gathers observations over a **150 ms window**. Reception has gaps; this is not continuous SDR streaming. The measured observation spacing in the original experiment was typically 35–36 µs.

```mermaid
flowchart LR
  A[VEVOR outdoor sensor<br/>Nominal 915 MHz] -. Possible harmonic path<br/>not RF-verified .-> B[Original ESP32<br/>Requested 2744 MHz]
  B --> C[128-sample snapshots<br/>Two tone-energy measurements]
  C --> D[USB serial<br/>Timestamped observations + CRC]
  D --> E[Python symbol-clock recovery]
  E --> F[Header, checksum<br/>and counter checks]
  F --> G[Weather readings<br/>Dashboard / JSON / CSV]
```

The Arduino library performs capture and tone measurement. **VEVOR packet decoding runs on the PC**, not on the ESP32. No router credentials or Internet connection are needed for reception.

## Example station

The demonstrated device is the **VEVOR YT60234 US variant, 7-in-1 outdoor weather station**:

- [User-supplied product listing — Amazon ASIN B0CHFBHK8N](https://www.amazon.com/dp/B0CHFBHK8N)
- [VEVOR protocol reference in rtl_433](https://github.com/merbanan/rtl_433/blob/master/src/devices/vevor_7in1.c)

This is one tested station/board combination. The EU868 MHz variant and other VEVOR products have not been tested with this profile. The example's sensor ID and tone offsets are measurements from one installation, not universal constants.

## What was verified

| Item | Bench result |
|---|---|
| Receiver | ESP32-D0WD-V3, revision 3.1, at 240 MHz |
| Receiver profile | Requested 2744 MHz; tone offsets 808500 Hz and 1018000 Hz; gain index 65 |
| Station ID in the recorded fixture | `14AB` |
| Final faster-sampler prototype test | Four accepted weather payloads across five predicted bursts |
| Between-burst controls | No accepted weather payloads |
| Recovered fixture temperature | 19.8°C / 67.6°F |
| Console comparison | User reported 67.8°F; the readings differ by 0.2°F |
| Recovered and console humidity | 82% |
| Tested interpretation | Correctly structured weather payloads with checksum and counter checks |
| Unverified | Absolute RF frequency, actual PLL lock, harmonic origin, direct 915 MHz reception, useful range |

Later live prototype readings changed as new packets arrived. The packaged dashboard screenshot shows its source labels, including recorded readings loaded at startup. [The earlier live prototype screenshot](docs/images/dashboard-prototype-live.jpg) shows new packets arriving. These are real UI captures, not synthetic mockups. Wind, rain, UV, light, and battery fields are decoded but were not independently compared with the console.

The simple additive checksum can miss compensating bit errors. Stable IDs, repeated fields, console comparison, and negative controls provide additional evidence; they do not make reception error-free. Up to two errors are permitted in the 40-bit sync pattern. Payload bits are not repaired or guessed.

## Supported build

| Component | Status |
|---|---|
| Original dual-core ESP32, D0WD-V3 | Bench tested |
| Arduino-ESP32 | **2.0.17 only**, pinned by the example |
| PlatformIO Espressif platform | `espressif32@6.12.0` |
| CPU and calling core | **240 MHz, Arduino loop on core 1** |
| ESP32-C3, S2, S3, C6 and other families | Not ported; compile-time guard rejects these targets |
| Python | 3.10+; NumPy, SciPy, pyserial |

The backend uses private PHY entry points and a reserved 64 KiB SRAM aperture. The example includes linker assertions; `begin()` also checks the memory layout and exclusive radio ownership. Captures block and mask the calling core's interrupts for up to 150 ms. Use the pinned example rather than adding this to a Wi-Fi networking application.

## Quick start — Windows / PowerShell

Install Git, Python, and the USB driver for your development board. From a terminal:

```powershell
git clone https://github.com/aobeidat85/ESP32-Harmonic-RX.git
cd ESP32-Harmonic-RX
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .\host platformio==6.2.0

# Replace COM10 with the receiver's actual serial port.
.\.venv\Scripts\pio.exe run -d examples/VEVORWeatherStation -t upload --upload-port COM10
.\.venv\Scripts\python.exe dashboard/app.py --serial-port COM10
```

Open **http://127.0.0.1:8765** and select **Start receiving**. Begin close to the outdoor sensor, as in the experiment. Initial acquisition can take several bursts. Keep any experimental GPIO transmitter unplugged while receiving weather data.

The UI starts with recorded example readings labeled **Saved**. Newly received packets are labeled **Live**. The Start/Stop controls acquire and release the serial port. A complete USB frame with a bad CRC is discarded and reception continues; a short read or malformed protocol frame requires reconnecting.

For Linux/macOS, activate `.venv`, use `python -m pip install -e ./host platformio==6.2.0`, and substitute a port such as `/dev/ttyUSB0`:

```sh
pio run -d examples/VEVORWeatherStation -t upload --upload-port /dev/ttyUSB0
python dashboard/app.py --serial-port /dev/ttyUSB0
```

When exactly one USB serial port is present, the dashboard can select it automatically. Otherwise provide `--serial-port`. An optional `--sensor-id 14AB` filter can restrict reception to one station. The default accepts any checksum-valid VEVOR ID.

### Try the decoder without hardware

```sh
python -m esp32_harmonic_rx --replay tests/fixtures/vevor-yt60234-packet.npz
```

This decodes a **real recorded RF capture** and prints JSON labeled `"source": "recorded"`. The included between-burst control produces no accepted weather packet.

### Command-line live reception

```sh
python -m esp32_harmonic_rx --port COM10 --seconds 120
```

The command has a bounded duration. It initially scans for a packet, then follows the measured approximately 20-second burst interval. Press Ctrl+C to release the serial port early. Do not run it while the dashboard owns that same port.

## Arduino library API

```cpp
#include <Esp32HarmonicRx.h>
using namespace esp32_harmonic_rx;

Receiver radio;
Observation observations[Receiver::MaxObservations]; // Global/static storage

void setup() {
  Serial.begin(115200);
  Config profile; // YT60234 experiment defaults; adjust only with measurements
  if (!radio.begin(profile)) {
    Serial.println(Receiver::errorMessage(radio.lastError()));
  }
}

void loop() {
  if (!radio.ready()) { delay(1000); return; }
  CaptureResult result = radio.capture(observations, Receiver::MaxObservations, 150);
  // Each row contains timeUs, tone0Power, tone1Power.
  // Inspect error, failedSnapshots and truncated before using the observations.
  Serial.printf("Captured %u timestamped observations\n", unsigned(result.count));
  delay(1000);
}
```

The complete [VEVORWeatherStation sketch](examples/VEVORWeatherStation/VEVORWeatherStation.ino) implements the USB bridge expected by the Python client. See [API and protocol](docs/API.md) for parameters, framing, and failure handling.

The repository has Arduino/PlatformIO library metadata. It has **not** been submitted to Arduino Library Manager, PlatformIO Registry, or PyPI. Clone/install from GitHub or use the repository's pinned example.

## Project layout

```text
src/                         Arduino capture library
examples/VEVORWeatherStation/ Pinned ESP32 USB-bridge example
host/esp32_harmonic_rx/       Python transport, clock recovery, and weather parser
dashboard/                   Local web UI using the host library
tests/fixtures/              Recorded RF packet and between-burst control
docs/                        Idea, API, validation, and real UI screenshots
```

## Tests

```sh
python -m unittest discover -s host/tests -v
cd dashboard
python -m unittest -v test_dashboard
cd ..
pio run -d examples/VEVORWeatherStation
```

GitHub Actions builds the example and runs host/dashboard checks. A passing software test does not establish RF tuning accuracy or range.

## Credits and license

The capture backend builds on [ESPARGOS/esp-sdr](https://github.com/ESPARGOS/esp-sdr). VEVOR field and checksum interpretation follows [rtl_433](https://github.com/merbanan/rtl_433), including Bruno OCTAU's `vevor_7in1.c` work.

This project is distributed under **GPL-3.0-only**. See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md). It is an independent experiment, not a VEVOR or Espressif product.
