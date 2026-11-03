// SPDX-License-Identifier: GPL-3.0-only
// Cheap physical FSK gate for this example's approximately 88.5 us symbols.
// This does not decode or validate a weather packet. CAP bypasses the gate.
#pragma once
#include <algorithm>
#include <cmath>

struct BurstEvidence { unsigned edges; float coherence; };

static BurstEvidence burstEvidence(const esp32_harmonic_rx::Observation *rows, size_t count) {
  // A sparse noise-floor estimate avoids another full-window SRAM allocation.
  float power[128];
  static float cosines[256], sines[256];
  static bool initialized = false;
  if (!initialized) {
    for (unsigned k = 0; k < 256; ++k) {
      cosines[k] = cosf(2 * M_PI * k / 256);
      sines[k] = sinf(2 * M_PI * k / 256);
    }
    initialized = true;
  }
  if (count < 128) return {0, 0};
  for (size_t k = 0; k < 128; ++k) {
    const auto &row = rows[k * (count - 1) / 127];
    power[k] = row.tone0Power + row.tone1Power;
  }
  const size_t tenth = 12, third = 38;
  std::nth_element(power, power + third, power + 128);
  const float quietLimit = power[third];
  std::nth_element(power, power + tenth, power + third);
  const float activeLimit = 3 * power[tenth];
  float b0 = 0, b1 = 0; unsigned quiet = 0;
  for (size_t k = 0; k < count; ++k) {
    if (rows[k].tone0Power + rows[k].tone1Power <= quietLimit) {
      b0 += rows[k].tone0Power; b1 += rows[k].tone1Power; ++quiet;
    }
  }
  b0 = fmaxf(b0 / fmaxf(quiet, 1), 1e-9f);
  b1 = fmaxf(b1 / fmaxf(quiet, 1), 1e-9f);
  bool previous = false, havePrevious = false;
  uint32_t previousTime = 0; unsigned edges = 0;
  float real = 0, imag = 0;
  for (size_t k = 0; k < count; ++k) {
    const float a = rows[k].tone0Power / b0, b = rows[k].tone1Power / b1;
    if (rows[k].tone0Power + rows[k].tone1Power <= activeLimit ||
        (a <= 2.718f * b && b <= 2.718f * a)) continue;
    const bool sign = b > a;
    if (havePrevious && rows[k].timeUs - previousTime < 145 && sign != previous) {
      const unsigned phase = ((rows[k].timeUs + previousTime) * 256u / 177u) & 255u;
      real += cosines[phase]; imag += sines[phase]; ++edges;
    }
    previous = sign; previousTime = rows[k].timeUs; havePrevious = true;
  }
  return {edges, edges ? sqrtf(real * real + imag * imag) / edges : 0};
}
