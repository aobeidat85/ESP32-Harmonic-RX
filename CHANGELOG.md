# Changelog

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
