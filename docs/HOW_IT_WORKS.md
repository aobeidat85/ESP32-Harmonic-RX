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
6. Repeat for up to 150 ms, then decode the buffered measurements on the ESP32.
7. Send a CRC-framed weather report and diagnostic summary over USB; retain RF arrays on the board.

Each observation integrates approximately 8 µs of nominal 16 MS/s data. Processing creates gaps between observations; observed spacing was typically 35–36 µs in the tested build. The algorithm preserves timestamps rather than pretending the samples are continuous.

## Onboard decoding and recorded-capture reference

The C++ onboard decoder estimates a quiet tone-power baseline, forms a log ratio between the tone energies, and identifies strong frequency transitions. It estimates symbol period and phase from those transitions **before consulting the payload checksum**. Observations are grouped into symbol cells. The Python implementation remains a reference for saved captures and explicit raw diagnostics. The onboard clock search uses a coarse grid followed by local peak refinement to reduce runtime.

After detecting a sync, the decoder also estimates a local clock in one fixed 40 ms interval: 16 ms before the sync and 24 ms after it. The local timing is eligible only when transition coherence is at least 0.3 and improves on the whole-window estimate. This selection uses physical transitions, without consulting checksum results or modifying payload bits. It avoids letting noise and small timing drift across the full 150 ms window corrupt a short packet.

The VEVOR decoder permits at most two errors in the 40-bit sync pattern. It then requires the exact `AA00` payload header, 21 payload bytes, a matching additive checksum over the first 19 bytes, and `payload[20] == (payload[18] + 1) mod 256`. At least 264 decoded bits must remain from the sync position. It never flips payload bits to satisfy the checksum. If independent copies fail, a conditional fixed repetition-combining step uses their measured soft symbol evidence.

Searching timing parameters until a checksum passes can produce misleading alternatives. An earlier exploratory decoder showed this problem, so the published decoder recovers timing independently. Even then, an additive checksum is weak: consistent sensor IDs, repeated fields, console comparison, and negative controls remain useful checks.

## The dashboard

The local Python server owns the serial connection. Its worker requests onboard capture/decoding and receives weather reports. It searches for a burst, then schedules captures near the next expected 20-second interval. Failed payload checks do not update the weather cards. A fully consumed serial frame with a CRC error is discarded; an uncertain serial boundary triggers a bounded reconnect. Legacy firmware still uses the PC RF decoder.

Accepted readings are appended to a local JSONL file. The browser polls the server, displays recorded and live readings separately, plots weather history, and exports the most recent 300 displayed readings as CSV. New onboard captures have no transferred signal trace or raw RF download. No cloud service is involved.

## What to reproduce next

Useful independent replications would measure the actual carrier and PLL behavior, repeat on other original-ESP32 boards, measure range, and compare all weather fields against the console. A battery-powered RF-isolation test would provide stronger evidence about the coupling path than the existing wall-USB comparison. These are future validations, not results claimed by this repository.

## Repeated-copy recovery (0.1.3)

The measured YT60234 captures contain copies spaced approximately 379 recovered symbol periods apart. When independent decoding produces no accepted packet, the decoder can group copies whose transition coherence is at least 0.3, spacing is within two symbols of that measured interval, and hard payload decisions differ in at most 12 of 168 bits. Every payload symbol must have observations. These criteria are evaluated before the combined checksum.

The decoder adds measured log tone-ratio evidence at corresponding symbol positions, makes one deterministic payload decision, and applies the exact header, additive checksum, and paired-counter checks. It does not enumerate bit flips, choose timing using checksums, or combine readings from separate 20-second bursts. A combined reading records `recovery_method=repetition` and its `repetition_count`. This is a profile-specific recovery method and an additive checksum still cannot rule out all compensating errors.

## Sync-template phase recovery (0.1.4)

If whole-window, local-clock, and ordinary repeated-copy decoding all fail, the decoder can refine the phase using only the known 40-bit sync waveform. The transition-derived period stays fixed. The phase fit is bounded to ±0.35 symbol periods, uses the interior of the sync to avoid endpoint effects, and models mixing over a nominal 8 µs snapshot. It selects a single phase from template correlation, before consulting any payload checks. The minimum normalized correlation is 0.65.

Payload observations within 4 µs of a symbol boundary receive lower weight. All 40 sync cells and 168 payload cells must have observations, and the refined hard sync must still have at most two errors. The same header/checksum/counter checks then apply; failed independent copies may use the existing timing/agreement-gated evidence combiner. New readings report `recovery_method=sync` or `sync_repetition`. Existing successful decoding paths take precedence. This is model-based phase estimation, not a search for a passing payload checksum.
