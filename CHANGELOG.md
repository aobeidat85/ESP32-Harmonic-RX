# Changelog

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
