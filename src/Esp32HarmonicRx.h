// SPDX-License-Identifier: GPL-3.0-only
#pragma once

#include <Arduino.h>

#if !defined(CONFIG_IDF_TARGET_ESP32)
#error "ESP32-Harmonic-RX currently supports the original dual-core ESP32 only."
#endif

namespace esp32_harmonic_rx {

struct Config {
  uint16_t tuningMHz = 2744;       // A tuning attempt, not a PLL-lock measurement.
  uint8_t gainIndex = 65;          // PHY table index; not calibrated dB.
  float tone0Hz = 808500.0f;       // Positive physical offset from requested LO.
  float tone1Hz = 1018000.0f;
};

struct Observation {
  uint32_t timeUs;
  float tone0Power;
  float tone1Power;
};
static_assert(sizeof(Observation) == 12, "Observation wire layout must be 12 bytes");

enum class Error : uint8_t {
  None, NotReady, InvalidConfig, UnsupportedCpu, UnsupportedCore,
  UnsafeMemoryLayout, RadioInUse, WifiInitializationFailed,
  InvalidBuffer, CaptureFailed, MemoryGuard
};

struct CaptureResult {
  size_t count = 0;
  uint32_t elapsedUs = 0;
  uint32_t failedSnapshots = 0;
  bool truncated = false;
  Error error = Error::None;
};

class Receiver {
 public:
  Receiver() = default;
  ~Receiver() { end(); }
  Receiver(const Receiver &) = delete;
  Receiver &operator=(const Receiver &) = delete;
  static constexpr size_t SamplesPerObservation = 128;
  static constexpr size_t MaxObservations = 6144;
  static constexpr uint16_t MaxWindowMs = 150;
  static constexpr uint32_t NominalSampleRate = 16000000;

  bool begin(const Config &config = Config());
  void end();
  bool ready() const { return ready_; }
  Error lastError() const { return error_; }
  const Config &config() const { return config_; }
  bool setTuningMHz(uint16_t value);
  bool setGainIndex(uint8_t value);
  bool setToneOffsets(float tone0Hz, float tone1Hz);

  // Blocks for up to 150 ms and masks this core's interrupts during acquisition.
  // Call from the Arduino loop on core1, with a global/static output buffer.
  // Observations are timestamped snapshots, not a continuous I/Q stream.
  CaptureResult capture(Observation *output, size_t capacity, uint16_t windowMs = 150);
  static const char *errorMessage(Error error);

 private:
  Config config_;
  bool ready_ = false;
  Error error_ = Error::None;
  int16_t cosine_[2][SamplesPerObservation];
  int16_t sine_[2][SamplesPerObservation];
  void coefficients();
  void prepare();
  bool fail(Error error) { error_ = error; return false; }
};

}  // namespace esp32_harmonic_rx

