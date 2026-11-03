// SPDX-License-Identifier: GPL-3.0-only
#include <cstdio>
#include "VevorDecoder.h"
struct Row { uint32_t timeUs; float tone0Power,tone1Power; };
static Row rows[6144];
static esp32_harmonic_rx::VevorDecoder<Row> decoder;
int main(int argc,char **argv) {
  if(argc!=2) return 2;
  FILE *f=fopen(argv[1],"rb"); if(!f) return 3;
  unsigned count=fread(rows,sizeof(Row),6144,f);fclose(f);
  const auto &r=decoder.decode(rows,count,0x14ab);
  printf("%u %.4f %.4f %u %u\n",r.count,r.periodUs,r.coherence,r.transitions,r.candidates);
  for(unsigned i=0;i<r.count;++i) {
    for(auto b:r.packets[i].payload) printf("%02x",b);
    printf(" %s %.3f\n",r.packets[i].method,r.packets[i].syncUs);
  }
  return r.invalid?4:0;
}
