# Changelog

## 0.2.0 - Onboard VEVOR decoding

- Move normal packet clock recovery, local refinement, repeated-copy combining, sync-template fitting, validation, and weather extraction onto the ESP32.
- Send compact CRC-framed weather reports and aggregate diagnostics instead of raw RF arrays; retain explicit CAP/MON debugging and recorded-capture replay.
- Use bounded, segmented integer-access scratch storage in the ESP32's 32-bit heap, preserving the reserved RF aperture and linker checks.
- Reduce measured board replay decoding from about 12-14 seconds to 0.57-0.76 seconds using a physical coarse/fine clock search and single-precision compensated accumulation.
- Add weather-report client/CLI/dashboard support, decode-time and USB-byte metrics, native C++ fixture checks in CI, and actual-board replay tooling.
- All 11 real saved captures match the reference on the board; fresh live station packets also decoded onboard. Reception remains experimental and intermittent.
- Pace weather-report UART writes after observing a short report. CRC checks and bounded dashboard reconnects remain required.

## 0.1.5 — Capture diagnostics and adaptive burst timing

- Persist every capture outcome and candidate-level validation checks in a diagnostic journal.
- Retain raw failed transfers and expose failure history, raw downloads, and journal export in Receiver details.
- Track measured burst-period drift, excluding same-burst repeats and other station IDs; preserve estimates across short restarts.
- Continue recovery for the preferred station when a mismatching-ID candidate passes the weak additive checksum.
- Add a compile-time 192-sample experiment option, while keeping the measured 128-sample default.
- Add diagnostic persistence, raw-retention, timing-drift, station-filter, and download-path checks.

## 0.1.4 — Sync-template phase recovery

- Add a fallback that refines phase against the known sync waveform before decoding payload bits.
- Keep the physical symbol period, bounded phase search, sync-error limit, and all payload checks.
- Downweight measurements within the nominal 4 µs half-snapshot distance of symbol boundaries.
- Preserve earlier decoding paths; label new recoveries as sync or sync_repetition.
- Add measured regression and fresh paired holdout/control fixtures.

## 0.1.3 — Slower USB transfer and measured repetition combining

- Negotiate 500,000-baud transfers after the 1,000,000-baud startup handshake; retain legacy firmware compatibility.
- Account for the negotiated rate in capture timestamp estimates and show it in the dashboard.
- Combine measured symbol evidence from closely matching, correctly spaced copies only when individual decoding fails; all payload checks remain required.
- Add a real failed-copy regression fixture and baud-negotiation tests.

## 0.1.2 — USB recovery and activity-gated acquisition

- Reset/reconnect after unknown USB framing or serial I/O failure, with bounded retries.
- Add negotiated MON acquisition: quiet windows return short metadata instead of full measurements.
- Bypass the gate for predicted bursts and periodic unfiltered scans.
- Pin the normal ESP32 LO divider, report reset reason, and add transport/monitor recovery tests.

## 0.1.1 — local clock recovery

- Refine the symbol clock in a fixed interval around detected syncs, selecting by transition coherence without checksum-guided timing search.
- Add a fresh measured capture that fails whole-window timing and passes local timing without bit repair.
- Report the recovered packet’s local clock period in the dashboard.
- Preserve measured burst timing for up to five minutes across dashboard restarts, with stale/wrong-port rejection.

## 0.1.0 — experimental

- Original-ESP32 Arduino capture library with memory-layout and radio-ownership checks.
- Pinned VEVOR YT60234 USB-bridge example.
- Installable Python transport, signal-clock recovery, weather parser, and bounded CLI.
- Local weather dashboard, packet inspection, and CSV export.
- Recorded RF packet and negative-control fixtures.
- Build/test workflow, setup instructions, validation record, and real UI screenshots.
