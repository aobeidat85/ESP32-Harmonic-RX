# Third-party notices

## ESPARGOS / ESP-SDR

The original-ESP32 RF capture register sequence, SRAM aperture reservation approach, receive preparation, and tuning approach were adapted from:

- Repository: https://github.com/ESPARGOS/esp-sdr
- Pinned source revision: `9b61e7465bee6da15209c5c09308043d08c46a1d`
- Relevant source: `main/targets/esp32/receiver.c`, `main/targets/esp32/sram_guard.ld`, and common receive-control headers.
- Upstream license: GNU GPL version 3. The full GPLv3 text is included in this repository's LICENSE.

The library refactors that experimental capture path into an Arduino API and adds timestamped two-tone observations. It does not claim to be a drop-in ESP-SDR firmware or to support the full upstream project.

## rtl_433 / VEVOR decoder

VEVOR payload layout, field conversions, additive checksum, and paired-counter interpretation follow `src/devices/vevor_7in1.c` in:

- https://github.com/merbanan/rtl_433
- Copyright (C) 2024 Bruno OCTAU (ProfBoc75), based on the Emax protocol.
- That source is GPL-2.0-or-later. The combined project is distributed under GPL-3.0-only, using GPLv3 for the compatible derived implementation.

The original C decoder is not vendored. The host implementation and its signal-clock recovery are Python code included here. Refer to upstream for the original source and authors' notices.

## Espressif and Arduino

Builds depend on Arduino-ESP32 2.0.17 and its ESP-IDF/PHY components, obtained through PlatformIO's pinned Espressif platform. Those components retain their own licenses and are not vendored into this source repository. Their private ABI is a compatibility constraint, not a supported Espressif public radio API.

## Product names and screenshots

VEVOR, ESP32, Espressif, Arduino, PlatformIO, and GitHub are names of their respective owners. The Amazon product link identifies the user's example station. This is an independent project without manufacturer endorsement.

Dashboard screenshots were captured from the project's local UI while displaying actual experimental measurements. Recorded RF fixtures and example readings are included for reproducibility; they are labeled separately from live reception.
