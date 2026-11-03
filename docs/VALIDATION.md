# Validation record

## Recorded experiment

Date: 2026-11-02. Receiver: ESP32-D0WD-V3 revision 3.1, 40 MHz crystal, original board antenna, USB-to-UART connection to a PC. The user placed the PC and receiver approximately 0.5–1 m from the outdoor VEVOR sensor. The separate GPIO-harmonic test sender was unplugged during VEVOR reception.

The final faster-sampler prototype collected five predicted 150 ms burst windows and matching between-burst controls. Four burst windows yielded accepted VEVOR payloads. None of those between-burst controls yielded an accepted payload. The accepted station ID was `14AB`; temperature was 19.8°C and humidity 82%. The user independently reported console temperature 67.8°F and humidity 82%; the captured temperature is 67.64°F, a 0.16°F difference before display rounding.

The package includes a real accepted capture and a real control in `tests/fixtures/`. Their provenance and expected payload are recorded in `provenance.json`. They contain RF tone measurements, not generated weather samples. Historical example readings are labeled Saved in the UI.

## Acceptance checks

- A complete USB frame with matching CRC32.
- Valid finite tone powers and increasing observation timestamps.
- Symbol period/phase estimated from transitions, independent of checksum search.
- At most two errors in the 40-bit VEVOR sync pattern.
- Exact `AA00` payload header.
- Additive checksum across the first 19 payload bytes.
- The paired counter relationship, including byte wrap.
- Optional station-ID filtering.

The original single-copy path performs no payload bit correction. Version 0.1.3 adds measured repeated-copy combining, described below; it never flips bits to force a checksum. A checksum match alone is not proof of an error-free payload: compensating errors can leave an additive checksum unchanged. The repeated ID and fields and the console comparison strengthen this particular experiment.

## Software checks

The checked-in tests decode the real packet fixture, reject the between-burst control, reject corrupted payload checksums/counters, reject invalid observations, distinguish a complete CRC-bad serial frame from a short read, exercise live/saved provenance, CSV export, duplicate suppression, and start/stop/error handling. The PlatformIO build enforces SRAM linker boundaries.

CI covers build and host behavior. Physical receiver tests are performed separately and documented in the release notes; they are not simulated by GitHub Actions.

During packaging, the refactored library was flashed to the same board. A 10 ms pilot returned 284 distinct timestamped observations at about 35 µs spacing, without reported capture failures. Three predicted 150 ms windows also completed without capture failures, but did not yield a newly accepted weather payload. A subsequent short retest of the original prototype also did not decode fresh weather data under those conditions. The recorded four-of-five result therefore belongs to the earlier prototype capture session, not a range or success-rate guarantee for the published library.

## Limits

- Requested tuning is not a calibrated frequency or PLL-lock readback.
- The apparent third-harmonic path remains a hypothesis. Direct 915 MHz reception was not demonstrated.
- Reception is intermittent and the tested placement was close to the sensor. No range guarantee is made.
- The firmware uses private PHY functions, shared-memory ownership controls, and a pinned SDK. Other ESP32 families or Arduino core versions are not supported by this backend.
- Measurements have gaps. The sampler does not provide continuous I/Q or decoded audio.
- Wind, rain, light, UV, and battery fields have not been independently checked against the console.
- Wall-USB and shared-USB experiments do not fully eliminate all conducted or nonlinear coupling paths. A calibrated RF source/receiver and stronger electrical isolation would improve verification.

## Local-clock regression fix

A later troubleshooting run found brief spectral tone pairs at the original offsets, repeating about 20 seconds apart. Spectrum-assisted timing captured fresh VEVOR syncs, but whole-window timing still left payload bit errors. A fixed sync-centered local clock recovered the new `vevor-local-clock.npz` fixture: station `14AB`, 18.7°C, 85% RH, with header, checksum, and counter all valid. No payload bits were repaired. The software suite now includes this measured regression alongside the original packet and negative control.

With the corrected decoder running in the live dashboard, two further fresh packets were accepted at 22:38:09 and 22:38:49 America/New_York: 18.5°C and 18.6°C, both 85% RH. These were newly captured measurements, not replayed fixtures. The troubleshooting run used spectrum-assisted burst timing to seed initial acquisition; it does not establish automatic cold-start acquisition reliability. Reception still misses bursts.

A third live reading followed at 22:40:29 America/New_York: 18.5°C, 86% RH. The two earlier live captures also decode under whole-window timing when correctly aligned; therefore the live recovery reflects spectrum-assisted acquisition as well as the decoder improvement. The separate local-clock fixture specifically demonstrates the decoder fix. Fourteen host/dashboard tests pass, including measured regression and restart-timing expiry checks.

## 0.1.2 USB recovery and monitor experiment

The dashboard later stopped on a malformed USB capture header; a separate gain test also encountered a serial timeout. These are transport failures, not evidence of weak RF. Tests now exercise closing the corrupted connection and reconnecting before receiving a real recorded packet.

A 65.16-second on-board MON run completed 410 quiet windows with no payload transfers or CRC errors. The sum of capture-window durations was 61.50 seconds, or 94.39% of wall time. This is **window coverage**, not continuous I/Q coverage: the 128-sample snapshots still have processing gaps within each window. No weather payload was accepted during that run, so the coverage result does not establish improved RF range or packet success rate. Periodic unfiltered scans and all predicted bursts bypass the gate.

## 0.1.3 transfer and repeated-copy tests

A six-burst test at the original USB rate encountered two lost-framing events on predicted bursts and one CRC-bad control frame. With negotiated 500,000-baud transfer, a separate stress run completed 20 full 150 ms captures in 24.45 seconds with zero CRC/framing failures. This is a short bench result, not a guarantee against future USB faults.

The decoder was then tested on four new predicted burst windows and four between-burst controls. All four bursts yielded accepted 14AB payloads; none of the controls did, and none of the eight transfers reported CRC/framing loss. Two burst payloads required measured repetition combining; the other two used local/whole-window timing. The combining rules were fixed before collecting these four windows. `vevor-repetitions-holdout.npz` and its between-burst control preserve one such fresh regression pair.

The preexisting `vevor-repetitions.npz` fixture contains two independently invalid copies. Their measured soft symbol evidence recovers the expected 18.7°C / 85% RH payload when combined at the observed repeat spacing. All original and new payload checks remain required. The later live dashboard also received a new packet on the slower link, but still missed some bursts: this is an improvement in a short experiment, not proof of reliable range or a controlled long-term reception-rate comparison. Twenty-one host/dashboard tests pass.

## 0.1.4 paired sync-timing evaluation

The sync-template rules were fixed using an earlier rejected capture, then evaluated on four new predicted burst recordings and four between-burst controls. Both decoders were applied to each identical recording: the previous 0.1.3 paths accepted 3 of 4 bursts; adding sync-template recovery accepted 4 of 4. Both rejected all four controls. The extra accepted payload is preserved in `vevor-sync-holdout.npz` with its control; it reports 14AB, 17.4°C, 90% RH and passes all payload checks. The added recovery used the sync fit on one copy, not a checksum-selected bit edit.

The earlier `vevor-sync-timing.npz` regression requires sync refinement followed by measured repetition combining. Twenty-three host/dashboard tests pass. These paired tests demonstrate additional recoverable data in these captures, not a statistically established long-term success rate or range guarantee.
