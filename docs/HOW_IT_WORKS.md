# How the experiment works

## Start with the result, then separate the interpretation

The experiment recovered recognizable VEVOR weather payloads with a bare original ESP32 connected to a PC. Repeated accepted payloads contained the same station ID, plausible changing fields, a valid additive checksum, and the expected paired counter relationship. The user compared temperature and humidity with the station's console.

The physical RF path remains less certain. The weather transmitter's documented nominal frequency is 915 MHz. The successful receiver profile requested 2744 MHz, and the apparent signal was near 2744.914 MHz. Since three times 915 MHz is 2745 MHz, a weak third harmonic is a plausible explanation. Neither an independently calibrated spectrum measurement nor a PLL-lock measurement was available. Receiver nonlinearities or other frequency translation have not been ruled out.

**Recovering the data is evidence of reception. It is not proof that this ESP32 directly tuned to 915 MHz.**

## Why short raw captures were insufficient

The earlier raw-I/Q bridge could return about 16,380 samples at nominal 16 MS/s: roughly 1 ms of RF data. Transferring that snapshot over the 1 Mbaud UART consumed much longer than its RF duration. It could show frequency switching, but could not provide a contiguous weather packet from one burst.

The revised sampler reduces the data on the ESP32:

1. Reserve the RF dump aperture before heap initialization and check application memory boundaries.
2. Set the requested LO, manual gain, receive path, and analog filter.
3. Trigger a 128-sample RF snapshot, then return memory ownership to the CPU.
4. Correlate the complex samples with two precomputed Hann-windowed tones.
5. Store only timestamp, tone0 power, and tone1 power.
6. Repeat for up to 150 ms, then transfer the observation array with a CRC32.

Each observation integrates approximately 8 µs of nominal 16 MS/s data. Processing creates gaps between observations; observed spacing was typically 35–36 µs in the tested build. The algorithm preserves timestamps rather than pretending the samples are continuous.

## Host decoding

Python estimates a quiet tone-power baseline, forms a log ratio between the tone energies, and identifies strong frequency transitions. It estimates symbol period and phase from those transitions **before consulting the payload checksum**. Observations are grouped into symbol cells.

The VEVOR decoder permits at most two errors in the 40-bit sync pattern. It then requires the exact `AA00` payload header, 21 payload bytes, a matching additive checksum over the first 19 bytes, and `payload[20] == (payload[18] + 1) mod 256`. At least 264 decoded bits must remain from the sync position. It performs no payload bit repair.

Searching timing parameters until a checksum passes can produce misleading alternatives. An earlier exploratory decoder showed this problem, so the published decoder recovers timing independently. Even then, an additive checksum is weak: consistent sensor IDs, repeated fields, console comparison, and negative controls remain useful checks.

## The dashboard

The local Python server owns the serial connection and runs decoding in a worker thread. It searches for a burst, then schedules captures near the next expected 20-second interval. Failed payload checks do not update the weather cards. A fully consumed serial frame with a CRC error is discarded; an uncertain serial boundary ends the connection so it can be reset.

Accepted readings are appended to a local JSONL file. The browser polls the server, displays recorded and live readings separately, plots history and signal energy, and exports the most recent 300 displayed readings as CSV. No cloud service is involved.

## What to reproduce next

Useful independent replications would measure the actual carrier and PLL behavior, repeat on other original-ESP32 boards, measure range, and compare all weather fields against the console. A battery-powered RF-isolation test would provide stronger evidence about the coupling path than the existing wall-USB comparison. These are future validations, not results claimed by this repository.
