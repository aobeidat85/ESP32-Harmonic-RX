// SPDX-License-Identifier: GPL-3.0-only
// Capture registers and reserved aperture adapted from ESPARGOS/esp-sdr,
// commit 9b61e7465bee6da15209c5c09308043d08c46a1d. See THIRD_PARTY_NOTICES.md.
#include "Esp32HarmonicRx.h"
#include <math.h>
#include "esp_arduino_version.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "heap_memory_layout.h"
#include "soc/dport_access.h"
#include "soc/dport_reg.h"
#include "soc/rtc.h"
#include "soc/soc.h"

#if ESP_ARDUINO_VERSION_MAJOR != 2 || ESP_ARDUINO_VERSION_MINOR != 0 || ESP_ARDUINO_VERSION_PATCH != 17
#error "This private-PHY backend is qualified only with Arduino-ESP32 2.0.17. Use the pinned PlatformIO example."
#endif

SOC_RESERVE_MEMORY_REGION(0x3ffe8000, 0x3fff8000, harmonic_rx_dump);

extern "C" {
void set_chanfreq(unsigned, unsigned);
void rom_set_rf_freq_offset(unsigned, unsigned, int);
unsigned ram_chip_i2c_readReg(unsigned, unsigned, unsigned);
void ram_chip_i2c_writeReg(unsigned, unsigned, unsigned, unsigned);
void rom_pbus_workmode(void);
void rom_pbus_xpd_rx_on(int);
void rom_pbus_xpd_tx_off(void);
void rom_set_rxclk_en(int);
extern char _iram_end, _data_end, _bss_end;
}

namespace esp32_harmonic_rx {
namespace {
constexpr uint32_t DumpCtrl = 0x60033d90u;
constexpr uint32_t DumpStatus = 0x60033d94u;
constexpr uint32_t DumpBytes = 0x60033dc4u;
constexpr uint32_t RxGain = 0x3ff5c02cu;
constexpr uint32_t Sentinel = 0xa55aa55au;
constexpr uintptr_t ApertureStart = 0x3ffe8000u;
constexpr uintptr_t ApertureEnd = 0x3fff8000u;
volatile uint32_t *const samples = reinterpret_cast<volatile uint32_t *>(ApertureStart);
portMUX_TYPE captureMux = portMUX_INITIALIZER_UNLOCKED;
Receiver *radioOwner = nullptr;
inline uint32_t cycles() { uint32_t value; asm volatile("rsr.ccount %0" : "=a"(value)); return value; }
bool validTones(float a, float b) {
  return isfinite(a) && isfinite(b) && a > 0 && b > 0 && a < 7000000 && b < 7000000 && a != b;
}
}  // namespace

bool Receiver::begin(const Config &config) {
  if (ready_ || radioOwner) return fail(Error::RadioInUse);
  if (config.tuningMHz < 2400 || config.tuningMHz > 2800 || config.gainIndex > 81 ||
      !validTones(config.tone0Hz, config.tone1Hz)) return fail(Error::InvalidConfig);
  if (getCpuFrequencyMhz() != 240) return fail(Error::UnsupportedCpu);
  if (xPortGetCoreID() != 1) return fail(Error::UnsupportedCore);
  if (reinterpret_cast<uintptr_t>(&_iram_end) > 0x400a8000u ||
      reinterpret_cast<uintptr_t>(&_data_end) > ApertureStart ||
      reinterpret_cast<uintptr_t>(&_bss_end) > ApertureStart) return fail(Error::UnsafeMemoryLayout);
  wifi_mode_t mode;
  if (esp_wifi_get_mode(&mode) == ESP_OK) return fail(Error::RadioInUse);
  esp_err_t event = esp_event_loop_create_default();
  if (event != ESP_OK && event != ESP_ERR_INVALID_STATE) return fail(Error::WifiInitializationFailed);
  wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
  if (esp_wifi_init(&init) != ESP_OK) return fail(Error::WifiInitializationFailed);
  if (esp_wifi_set_storage(WIFI_STORAGE_RAM) != ESP_OK ||
      esp_wifi_set_mode(WIFI_MODE_NULL) != ESP_OK || esp_wifi_start() != ESP_OK ||
      esp_wifi_set_ps(WIFI_PS_NONE) != ESP_OK || esp_wifi_set_promiscuous(true) != ESP_OK ||
      esp_wifi_set_channel(1, WIFI_SECOND_CHAN_NONE) != ESP_OK) {
    esp_wifi_stop(); esp_wifi_deinit();
    return fail(Error::WifiInitializationFailed);
  }
  config_ = config;
  coefficients(); prepare();
  radioOwner = this; ready_ = true; error_ = Error::None;
  return true;
}

void Receiver::end() {
  if (!ready_ || radioOwner != this) return;
  REG_WRITE(DumpCtrl, 0);
  esp_wifi_set_promiscuous(false); esp_wifi_stop(); esp_wifi_deinit();
  ready_ = false; radioOwner = nullptr;
}

bool Receiver::setTuningMHz(uint16_t value) {
  if (!ready_) return fail(Error::NotReady);
  if (value < 2400 || value > 2800) return fail(Error::InvalidConfig);
  config_.tuningMHz = value; prepare(); error_ = Error::None; return true;
}
bool Receiver::setGainIndex(uint8_t value) {
  if (!ready_) return fail(Error::NotReady);
  if (value > 81) return fail(Error::InvalidConfig);
  config_.gainIndex = value; error_ = Error::None; return true;
}
bool Receiver::setToneOffsets(float a, float b) {
  if (!ready_) return fail(Error::NotReady);
  if (!validTones(a, b)) return fail(Error::InvalidConfig);
  config_.tone0Hz = a; config_.tone1Hz = b; coefficients(); error_ = Error::None; return true;
}

void Receiver::coefficients() {
  const float tones[] = {config_.tone0Hz, config_.tone1Hz};
  for (unsigned f = 0; f < 2; ++f) for (unsigned k = 0; k < SamplesPerObservation; ++k) {
    const float window = .5f - .5f * cosf(2 * M_PI * k / (SamplesPerObservation - 1));
    const float angle = 2 * M_PI * tones[f] * k / NominalSampleRate;
    cosine_[f][k] = lroundf(4096 * window * cosf(angle));
    sine_[f][k] = lroundf(4096 * window * sinf(angle));
  }
}

void Receiver::prepare() {
  REG_WRITE(DumpCtrl, 0); set_chanfreq(2412, 0);
  const unsigned cap = ram_chip_i2c_readReg(0x62, 1, 0);
  ram_chip_i2c_writeReg(0x62, 1, 0, cap & ~0x80u);
  const unsigned xtal = rtc_clk_xtal_freq_get();
  rom_set_rf_freq_offset(xtal == 26 ? 1 : xtal == 24 ? 2 : 0, config_.tuningMHz, 0);
  rom_pbus_workmode(); rom_pbus_xpd_tx_off(); rom_pbus_xpd_rx_on(1); rom_set_rxclk_en(1);
  const uint32_t gain = REG_READ(RxGain);
  REG_WRITE(RxGain, (gain & 0x7fffff) | (config_.gainIndex << 24) | BIT(23));
  delay(3);
}

CaptureResult Receiver::capture(Observation *output, size_t capacity, uint16_t windowMs) {
  CaptureResult result;
  if (!ready_) { result.error = Error::NotReady; return result; }
  if (getCpuFrequencyMhz() != 240) { result.error = Error::UnsupportedCpu; return result; }
  if (xPortGetCoreID() != 1) { result.error = Error::UnsupportedCore; return result; }
  const uintptr_t outStart = reinterpret_cast<uintptr_t>(output);
  const uintptr_t outEnd = outStart + capacity * sizeof(Observation);
  if (!output || !capacity || capacity > MaxObservations || windowMs < 10 || windowMs > MaxWindowMs ||
      outEnd < outStart || (outStart < ApertureEnd && outEnd > ApertureStart)) {
    result.error = Error::InvalidBuffer; return result;
  }
  prepare();
  for (unsigned k = 0; k < 512; ++k) samples[k] = Sentinel;
  const uint32_t owner = DPORT_REG_READ(DPORT_IRAM_DRAM_AHB_SEL_REG);
  const uint32_t byteSelect = REG_READ(DumpBytes);
  unsigned savedFilter[2];
  for (unsigned k = 0; k < 2; ++k) {
    savedFilter[k] = ram_chip_i2c_readReg(0x67, 1, 1 + k);
    ram_chip_i2c_writeReg(0x67, 1, 1 + k, (savedFilter[k] & ~127u) | 127u);
  }
  delayMicroseconds(10);
  const uint32_t dumpOwner = (owner & ~DPORT_MAC_DUMP_MODE_M) | (3 << DPORT_MAC_DUMP_MODE_S);
  REG_WRITE(DumpBytes, 0x03020100);
  portENTER_CRITICAL(&captureMux);
  const uint32_t started = cycles();
  while (static_cast<uint32_t>(cycles() - started) < windowMs * 240000u && result.count < capacity) {
    samples[SamplesPerObservation - 1] = Sentinel;
    REG_WRITE(DumpCtrl, 0); DPORT_REG_WRITE(DPORT_IRAM_DRAM_AHB_SEL_REG, dumpOwner);
    REG_WRITE(DumpCtrl, SamplesPerObservation | BIT(16) | BIT(31));
    const uint32_t began = cycles();
    REG_SET_BIT(DumpCtrl, BIT(19)); REG_CLR_BIT(DumpCtrl, BIT(19));
    while (!(REG_READ(DumpCtrl) & BIT(18)) && static_cast<uint32_t>(cycles() - began) < 48000u) {}
    const uint32_t ended = cycles(), status = REG_READ(DumpStatus), done = REG_READ(DumpCtrl);
    REG_WRITE(DumpCtrl, 0); DPORT_REG_WRITE(DPORT_IRAM_DRAM_AHB_SEL_REG, owner);
    if (samples[SamplesPerObservation] != Sentinel) { result.error = Error::MemoryGuard; break; }
    if (!(done & BIT(18)) || (status & 0x7fff) != SamplesPerObservation || samples[SamplesPerObservation - 1] == Sentinel) {
      ++result.failedSnapshots; continue;
    }
    int32_t real[2] = {0, 0}, imag[2] = {0, 0};
    for (unsigned k = 4; k < SamplesPerObservation; ++k) {
      const uint32_t word = samples[k];
      int32_t i = word & 1023, q = (word >> 10) & 1023;
      if (i >= 512) i -= 1024;
      if (q >= 512) q -= 1024;
      for (unsigned f = 0; f < 2; ++f) {
        const int32_t c = cosine_[f][k], s = sine_[f][k];
        real[f] += i * c - q * s; imag[f] += i * s + q * c;
      }
    }
    Observation &row = output[result.count++];
    row.timeUs = ((began - started) + (ended - began) / 2) / 240;
    const float a = real[0] / 4096.0f, b = imag[0] / 4096.0f;
    const float c = real[1] / 4096.0f, d = imag[1] / 4096.0f;
    row.tone0Power = a * a + b * b; row.tone1Power = c * c + d * d;
  }
  result.elapsedUs = (cycles() - started) / 240;
  REG_WRITE(DumpCtrl, 0); DPORT_REG_WRITE(DPORT_IRAM_DRAM_AHB_SEL_REG, owner); REG_WRITE(DumpBytes, byteSelect);
  portEXIT_CRITICAL(&captureMux);
  for (unsigned k = 0; k < 2; ++k) ram_chip_i2c_writeReg(0x67, 1, 1 + k, savedFilter[k]);
  result.truncated = result.count == capacity && result.elapsedUs < windowMs * 1000u;
  if (!result.count && result.error == Error::None) result.error = Error::CaptureFailed;
  error_ = result.error;
  return result;
}

const char *Receiver::errorMessage(Error error) {
  switch (error) {
    case Error::None: return "OK";
    case Error::NotReady: return "Receiver is not initialized";
    case Error::InvalidConfig: return "Invalid tuning, gain, or tone offsets";
    case Error::UnsupportedCpu: return "CPU must run at 240 MHz";
    case Error::UnsupportedCore: return "Call the receiver from Arduino loop on core1";
    case Error::UnsafeMemoryLayout: return "RF capture aperture overlaps application memory";
    case Error::RadioInUse: return "The library requires exclusive ownership of the Wi-Fi radio";
    case Error::WifiInitializationFailed: return "Wi-Fi PHY initialization failed";
    case Error::InvalidBuffer: return "Invalid capture buffer or duration";
    case Error::CaptureFailed: return "No valid RF snapshots captured";
    case Error::MemoryGuard: return "RF capture aperture guard changed";
  }
  return "Unknown receiver error";
}
}  // namespace esp32_harmonic_rx

