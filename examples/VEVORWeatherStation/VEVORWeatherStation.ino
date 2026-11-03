// SPDX-License-Identifier: GPL-3.0-only
// YT60234 onboard decoding; normal USB replies contain weather, not RF arrays.
#include <Esp32HarmonicRx.h>
#include <VevorDecoder.h>
#include "rom/crc.h"
#include "BurstGate.h"
#include "esp_system.h"
#include "esp_heap_caps.h"

using namespace esp32_harmonic_rx;
Receiver receiver;
Observation observations[Receiver::MaxObservations];
unsigned activeBaud = 1000000;
VevorDecoder<Observation> *decoder=nullptr;
int preferredStation = -1;
constexpr size_t ReportSize=6144;
char *report=nullptr;

void weatherReport(size_t count, unsigned elapsed, unsigned failures, bool monitor=false) {
  const uint32_t began=micros();
  if(!decoder || !decoder->ready() || !report) {Serial.println("ERR decoder memory");return;}
  VevorResult quietResult;
  bool quiet=false;
  if(monitor) {
    const auto gate=burstEvidence(observations,count);
    quiet=gate.edges<30 || gate.coherence<.20f;
    quietResult.transitions=gate.edges;quietResult.coherence=gate.coherence;
  }
  const auto &r=quiet?quietResult:decoder->decode(observations,count,preferredStation,Receiver::SamplesPerObservation);
  unsigned decodeUs=micros()-began;
  size_t length=snprintf(report,ReportSize,
    "{\"version\":1,\"quiet\":%s,\"count\":%u,\"elapsed_us\":%u,\"failures\":%u,\"decode_us\":%u,\"heap\":%u,"
    "\"invalid\":%s,\"clock_period_us\":%.5f,\"clock_coherence\":%.5f,\"transitions\":%u,"
    "\"nearest_preamble_bit_errors\":%u,\"candidates\":%u,\"header_failures\":%u,"
    "\"checksum_failures\":%u,\"counter_failures\":%u,\"valid_packets\":[",
    quiet?"true":"false",unsigned(count),elapsed,failures,decodeUs,ESP.getFreeHeap(),r.invalid?"true":"false",
    r.periodUs,r.coherence,r.transitions,r.nearestErrors,r.candidates,
    r.headerFailures,r.checksumFailures,r.counterFailures);
  for(unsigned i=0;i<r.count;++i) {
    const auto &p=r.packets[i];const auto *b=p.payload;
    char hex[43];for(unsigned j=0;j<21;++j) snprintf(hex+2*j,3,"%02x",b[j]);
    auto adj=[&](unsigned j){return uint8_t(b[j]-1);};
    unsigned light=(adj(16)<<8)|adj(17);if(light&32768) light=(light&32767)*10;
    float temp=(((b[5]&15)<<8|b[6])-500)/10.f;
    int added=snprintf(report+length,ReportSize-length,
      "%s{\"payload_hex\":\"%s\",\"preamble_time_us\":%.3f,\"clock_period_us\":%.5f,"
      "\"preamble_bit_errors\":%u,\"repetition_count\":%u,\"clock_source\":\"%s\","
      "\"header_valid\":true,\"checksum_valid\":true,\"counter_valid\":true,"
      "\"weather\":{\"sensor_id\":\"%02X%02X\",\"temperature_C\":%.1f,\"temperature_F\":%.2f,"
      "\"humidity_percent\":%u,\"wind_avg_km_h\":%.2f,\"wind_gust_km_h\":%.2f,"
      "\"wind_direction_degrees\":%u,\"total_rain_mm\":%.3f,\"uv_index\":%d,"
      "\"light_lux\":%u,\"battery_ok\":%s,\"counter\":%u,\"next_counter\":%u,"
      "\"checksum_valid\":true,\"counter_valid\":true,\"payload_hex\":\"%s\"}}",
      i?",":"",hex,p.syncUs,p.periodUs,p.preambleErrors,p.repetitions,p.method,
      b[2],b[3],temp,temp*1.8f+32,b[7],((adj(8)<<8)|adj(9))/8.333f,b[10]*1.25f,
      (adj(11)<<8)|adj(12),((adj(13)<<8)|adj(14))*.233f,int(b[15]&31)-1,
      light,(b[4]&128)?"false":"true",b[18],b[20],hex);
    if(added<0 || size_t(added)>=ReportSize-length) {Serial.println("ERR report overflow");return;}
    length+=added;
  }
  if(length+3>=ReportSize) {Serial.println("ERR report overflow");return;}
  report[length++]=']';report[length++]='}';report[length]=0;
  Serial.printf("WX %u %08x\n",unsigned(length),crc32_le(0,reinterpret_cast<uint8_t*>(report),length));
  // Short paced UART writes also avoid the observed CP210x partial-frame stalls.
  Serial.flush();
  for(size_t offset=0;offset<length;offset+=64) {
    Serial.write(reinterpret_cast<uint8_t*>(report+offset),std::min(size_t(64),length-offset));
    Serial.flush();delay(2);
  }
}

void setup() {
  Serial.setRxBufferSize(2048);
  Serial.begin(1000000);
  Serial.setTimeout(100);
  Config config;  // Measured experiment profile, not universal VEVOR frequencies.
  if (!receiver.begin(config)) {
    Serial.printf("ERR init %s\n", Receiver::errorMessage(receiver.lastError()));
    return;
  }
  decoder=new(std::nothrow) VevorDecoder<Observation>;
  report=new(std::nothrow) char[ReportSize];
  Serial.printf("MEM decoder=%u size=%u free8=%u largest8=%u free32=%u largest32=%u\n",
    decoder && decoder->ready(),unsigned(sizeof(VevorDecoder<Observation>)),
    heap_caps_get_free_size(MALLOC_CAP_8BIT),heap_caps_get_largest_free_block(MALLOC_CAP_8BIT),
    heap_caps_get_free_size(MALLOC_CAP_32BIT),heap_caps_get_largest_free_block(MALLOC_CAP_32BIT));
  Serial.printf("VEVOR_FSK_READY N=%u ROWS=%u heap=%u\n",
                unsigned(Receiver::SamplesPerObservation), unsigned(Receiver::MaxObservations), ESP.getFreeHeap());
}

void loop() {
  if (!Serial.available()) { delay(1); return; }
  String command = Serial.readStringUntil('\n');
  command.trim();
  unsigned value, tone0, tone1;
  if (command == "INFO") {
    if (!receiver.ready() || !decoder || !decoder->ready() || !report) {
      Serial.printf("ERR receiver %s\n", Receiver::errorMessage(receiver.lastError()));
      return;
    }
    Serial.printf("VEVOR_FSK 1 N=%u max_rows=%u freq=%u gain=%u heap=%u monitor=1 reset_reason=%u baud_switch=1 baud=%u weather=1\n",
                  unsigned(Receiver::SamplesPerObservation), unsigned(Receiver::MaxObservations),
                  receiver.config().tuningMHz, receiver.config().gainIndex, ESP.getFreeHeap(), unsigned(esp_reset_reason()), activeBaud);
  } else if(command=="STATION ANY") {
    preferredStation=-1;Serial.println("OK");
  } else if(sscanf(command.c_str(),"STATION %x",&value)==1 && value<=65535) {
    preferredStation=value;Serial.println("OK");
  } else if((sscanf(command.c_str(),"WX %u",&value)==1 || sscanf(command.c_str(),"WM %u",&value)==1) && value>=10 && value<=150) {
    const auto r=receiver.capture(observations,Receiver::MaxObservations,value);
    if(r.error!=Error::None || r.truncated || r.failedSnapshots) {Serial.println("ERR incomplete capture");return;}
    weatherReport(r.count,r.elapsedUs,r.failedSnapshots,command.startsWith("WM "));
  } else if(command.startsWith("REPLAY ")) {
    // Explicit test-only path: feed retained measurements into the SAME decoder.
    unsigned crc;
    if(sscanf(command.c_str(),"REPLAY %u %x",&value,&crc)!=2 || value<128 || value>Receiver::MaxObservations) {
      Serial.println("ERR replay size");return;
    }
    Serial.println("READY");Serial.setTimeout(3000);
    size_t bytes=value*sizeof(Observation);
    size_t got=Serial.readBytes(reinterpret_cast<char*>(observations),bytes);Serial.setTimeout(100);
    if(got!=bytes || crc32_le(0,reinterpret_cast<uint8_t*>(observations),bytes)!=crc) {Serial.println("ERR replay CRC");return;}
    weatherReport(value,observations[value-1].timeUs,0);
  } else if (sscanf(command.c_str(), "BAUD %u", &value) == 1 && (value == 500000 || value == 1000000)) {
    // Acknowledge completely at the old speed before either side changes rate.
    Serial.println("OK");
    Serial.flush();
    Serial.updateBaudRate(value);
    activeBaud = value;
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

