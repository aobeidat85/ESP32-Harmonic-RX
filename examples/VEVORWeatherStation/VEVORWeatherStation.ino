// SPDX-License-Identifier: GPL-3.0-only
// USB capture bridge for the VEVOR YT60234 example. Packet decoding runs on the PC.
#include <Esp32HarmonicRx.h>
#include "rom/crc.h"
#include "BurstGate.h"
#include "esp_system.h"

using namespace esp32_harmonic_rx;
Receiver receiver;
Observation observations[Receiver::MaxObservations];

void setup() {
  Serial.begin(1000000);
  Serial.setTimeout(100);
  Config config;  // Measured experiment profile, not universal VEVOR frequencies.
  if (!receiver.begin(config)) {
    Serial.printf("ERR init %s\n", Receiver::errorMessage(receiver.lastError()));
    return;
  }
  Serial.printf("VEVOR_FSK_READY N=%u ROWS=%u heap=%u\n",
                unsigned(Receiver::SamplesPerObservation), unsigned(Receiver::MaxObservations), ESP.getFreeHeap());
}

void loop() {
  if (!Serial.available()) { delay(1); return; }
  String command = Serial.readStringUntil('\n');
  command.trim();
  unsigned value, tone0, tone1;
  if (command == "INFO") {
    if (!receiver.ready()) {
      Serial.printf("ERR receiver %s\n", Receiver::errorMessage(receiver.lastError()));
      return;
    }
    Serial.printf("VEVOR_FSK 1 N=%u max_rows=%u freq=%u gain=%u heap=%u monitor=1 reset_reason=%u\n",
                  unsigned(Receiver::SamplesPerObservation), unsigned(Receiver::MaxObservations),
                  receiver.config().tuningMHz, receiver.config().gainIndex, ESP.getFreeHeap(), unsigned(esp_reset_reason()));
  } else if ((sscanf(command.c_str(), "CAP %u", &value) == 1 ||
              sscanf(command.c_str(), "MON %u", &value) == 1) && value >= 10 && value <= 150) {
    const bool monitor = command.startsWith("MON ");
    CaptureResult result = receiver.capture(observations, Receiver::MaxObservations, value);
    if (result.error != Error::None || result.truncated) {
      Serial.printf("ERR capture %s truncated=%u\n", Receiver::errorMessage(result.error), result.truncated);
      return;
    }
    const uint32_t measured = micros();
    if (monitor) {
      const BurstEvidence evidence = burstEvidence(observations, result.count);
      if (evidence.edges < 30 || evidence.coherence < 0.20f) {
        Serial.printf("IDLE %u %u %u %u %u %u %.4f\n", unsigned(result.count),
                      result.elapsedUs, result.failedSnapshots, receiver.config().gainIndex,
                      unsigned(micros() - measured), evidence.edges, evidence.coherence);
        return;
      }
    }
    const size_t bytes = result.count * sizeof(Observation);
    const uint32_t crc = crc32_le(0, reinterpret_cast<const uint8_t *>(observations), bytes);
    if (monitor) {
      Serial.printf("FSK %u %08x %u %u %u %u\n", unsigned(result.count), crc,
                    result.elapsedUs, result.failedSnapshots, receiver.config().gainIndex,
                    unsigned(micros() - measured));
    } else {
      Serial.printf("FSK %u %08x %u %u %u\n", unsigned(result.count), crc,
                    result.elapsedUs, result.failedSnapshots, receiver.config().gainIndex);
    }
    Serial.write(reinterpret_cast<const uint8_t *>(observations), bytes);
    Serial.flush();
  } else if (sscanf(command.c_str(), "FREQ %u", &value) == 1 && value >= 2400 && value <= 2800) {
    Serial.println(receiver.setTuningMHz(value) ? "OK" : "ERR tuning");
  } else if (sscanf(command.c_str(), "GAIN %u", &value) == 1 && value <= 81) {
    Serial.println(receiver.setGainIndex(value) ? "OK" : "ERR gain");
  } else if (sscanf(command.c_str(), "TONES %u %u", &tone0, &tone1) == 2) {
    Serial.println(receiver.setToneOffsets(tone0, tone1) ? "OK" : "ERR tones");
  } else {
    Serial.println("ERR command");
  }
}

