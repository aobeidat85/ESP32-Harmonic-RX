// SPDX-License-Identifier: GPL-3.0-only
// Bounded-memory YT60234 decoder. Timing is selected from RF transitions/sync,
// never by searching for a passing checksum. No payload bit correction.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <new>
#ifdef ARDUINO_ARCH_ESP32
#include "esp_heap_caps.h"
#endif

namespace esp32_harmonic_rx {
#ifdef _MSC_VER
#define VEVOR_NOINLINE __declspec(noinline)
#else
#define VEVOR_NOINLINE __attribute__((noinline))
#endif
struct VevorPacket {
  uint8_t payload[21];
  float syncUs, periodUs;
  unsigned preambleErrors, repetitions;
  const char *method;
};
struct VevorResult {
  VevorPacket packets[8];
  unsigned count=0, transitions=0, candidates=0, headerFailures=0,
    checksumFailures=0, counterFailures=0, nearestErrors=40;
  float periodUs=0, coherence=0;
  bool invalid=false;
};

template<class Row> class VevorDecoder {
  static constexpr unsigned MaxRows=6144, MaxCells=1800, MaxCopies=8;
  // The original ESP32 has spare 32-bit-only IRAM heap, but little byte RAM.
  // Store float bit patterns there through integer loads/stores: the FPU cannot
  // directly access IRAM. Small chunks avoid needing a contiguous 24 KB block.
  struct Buffer {
    static VEVOR_NOINLINE uint32_t load(volatile uint32_t *p) {return *p;}
    static VEVOR_NOINLINE void store(volatile uint32_t *p,uint32_t v) {*p=v;}
    struct Value {
      uint32_t *p;
      operator float() const {uint32_t bits=load(p);float v;std::memcpy(&v,&bits,4);return v;}
      Value &operator=(float v) {uint32_t bits;std::memcpy(&bits,&v,4);store(p,bits);return *this;}
    };
    uint32_t *chunks[12]={};
    void allocate() {for(auto &p:chunks) {
#ifdef ARDUINO_ARCH_ESP32
      p=static_cast<uint32_t*>(heap_caps_malloc(512*sizeof(uint32_t),MALLOC_CAP_EXEC|MALLOC_CAP_32BIT));
#else
      p=new(std::nothrow) uint32_t[512];
#endif
    }}
    ~Buffer() {for(auto p:chunks) {
#ifdef ARDUINO_ARCH_ESP32
      heap_caps_free(p);
#else
      delete[] p;
#endif
    }}
    bool ready() const {for(auto p:chunks) if(!p) return false;return true;}
    Value operator[](unsigned i) {return {chunks[i/512]+i%512};}
  } work,ratio;
  float *cells=nullptr;
  struct Copy {
    float sync, period, coherence, soft[168];
    unsigned begin, end, errors;
    bool inverted;
  };
  Copy *copies=nullptr;
  unsigned copyCount=0;
  const Row *rows=nullptr;
  uint16_t station=0;
  bool filter=false;
  float halfSnapshot=4;
  VevorResult result;
  static int pattern(unsigned i) {
    static const uint8_t bytes[5]={0xaa,0xaa,0xca,0xca,0x54};
    return (bytes[i/8] & (0x80>>(i%8))) ? 1 : -1;
  }
  float select(unsigned n,unsigned k) {
    int left=0,right=int(n)-1;
    while(left<right) {
      float pivot=work[(left+right)/2];int i=left,j=right;
      while(i<=j) {
        while(work[i]<pivot) ++i;
        while(work[j]>pivot) --j;
        if(i<=j) {float a=work[i],b=work[j];work[i]=b;work[j]=a;++i;--j;}
      }
      if(int(k)<=j) right=j;else if(int(k)>=i) left=i;else break;
    }
    return work[k];
  }
  float percentile(unsigned n, float q) {
    float at=(n-1)*q; unsigned lo=unsigned(at), hi=std::min(lo+1,n-1);
    float a=select(n,lo),b=select(n,hi);return a+(b-a)*(at-lo);
  }
  bool matching() const {
    for (unsigned i=0;i<result.count;++i)
      if (!filter || ((result.packets[i].payload[2]<<8)|result.packets[i].payload[3])==station) return true;
    return false;
  }
  void check(const float *soft, float sync, float period, unsigned errors,
             unsigned repetitions, const char *method) {
    uint8_t b[21]={};
    for(unsigned i=0;i<168;++i) if(soft[i]>0) b[i/8]|=0x80>>(i%8);
    unsigned sum=0; for(unsigned i=0;i<19;++i) sum+=b[i];
    bool header=b[0]==0xaa && b[1]==0;
    bool checksum=(sum&255)==b[19], counter=b[20]==uint8_t(b[18]+1);
    ++result.candidates; result.headerFailures+=!header;
    result.checksumFailures+=!checksum; result.counterFailures+=!counter;
    if(!header || !checksum || !counter) return;
    // Keep other station candidates from consuming all output slots.
    if(filter && ((b[2]<<8)|b[3])!=station) return;
    for(unsigned i=0;i<result.count;++i)
      if(!std::memcmp(result.packets[i].payload,b,21) && std::fabs(result.packets[i].syncUs-sync)<500) return;
    if(result.count==8) return;
    auto &p=result.packets[result.count++]; std::memcpy(p.payload,b,21);
    p.syncUs=sync; p.periodUs=period; p.preambleErrors=errors;
    p.repetitions=repetitions; p.method=method;
  }
  bool softCopy(Copy &c, bool weighted=false, bool preambleCheck=false) {
    float soft[208]={}; uint8_t seen[208]={};
    for(unsigned i=c.begin;i<c.end;++i) {
      float u=(rows[i].timeUs-c.sync)/c.period;
      int cell=int(std::floor(u)); if(cell<0 || cell>=208) continue;
      float confidence=weighted ? std::min(1.0f,std::min(u-cell,1-(u-cell))*c.period/halfSnapshot) : 1;
      soft[cell]+=ratio[i]*(c.inverted?-1:1)*confidence; seen[cell]=1;
    }
    for(unsigned i=40;i<208;++i) if(!seen[i]) return false;
    if(preambleCheck) {
      unsigned errors=0;
      for(unsigned i=0;i<40;++i) { if(!seen[i]) return false; errors+=((soft[i]>0)!=(pattern(i)>0)); }
      if(errors>2) return false; c.errors=errors;
    }
    std::copy(soft+40,soft+208,c.soft); return true;
  }
  void window(unsigned begin,unsigned end,bool local=false,float nearSync=0) {
    unsigned n=end-begin; if(n<128) return;
    for(unsigned i=0;i<n;++i) work[i]=rows[begin+i].tone0Power+rows[begin+i].tone1Power;
    float quiet=percentile(n,.30f);
    float active=3*percentile(n,.1f);
    unsigned q=0;
    for(unsigned i=begin;i<end;++i) if(rows[i].tone0Power+rows[i].tone1Power<=quiet) work[q++]=rows[i].tone0Power;
    float b0=std::max(percentile(q,.5f),1e-9f); q=0;
    for(unsigned i=begin;i<end;++i) if(rows[i].tone0Power+rows[i].tone1Power<=quiet) work[q++]=rows[i].tone1Power;
    float b1=std::max(percentile(q,.5f),1e-9f);
    for(unsigned i=begin;i<end;++i)
      ratio[i]=std::max(-8.f,std::min(8.f,std::log((rows[i].tone1Power/b1+1e-6f)/(rows[i].tone0Power/b0+1e-6f))));
    unsigned edges=0; int previous=-1; float sumWeights=0;
    for(unsigned i=begin;i<end;++i) {
      float neighborhood[9];
      for(int d=-4;d<=4;++d) {
        int index=int(i)+d;
        if(index<int(begin)) index=2*int(begin)-index-1;
        if(index>=int(end)) index=2*int(end)-index-1;
        neighborhood[d+4]=rows[index].tone0Power+rows[index].tone1Power;
      }
      std::nth_element(neighborhood,neighborhood+4,neighborhood+9);
      if(neighborhood[4]<=active || std::fabs(ratio[i])<=1) continue;
      if(previous>=0) {
        float gap=rows[i].timeUs-rows[previous].timeUs;
        if(gap<145 && (ratio[i]>0)!=(ratio[previous]>0) && edges<MaxRows/2) {
          work[2*edges]=(rows[i].timeUs+rows[previous].timeUs)*.5f;
          work[2*edges+1]=std::min(std::fabs(ratio[i]),std::fabs(ratio[previous]))/gap; sumWeights+=work[2*edges+1];++edges;
        }
      }
      previous=i;
    }
    if(!local) result.transitions=edges;
    if(edges<20) return;
    double best=-1,period=0,phase=0;
    int peakIndex=0;
    // Coarse physical-clock search, then a 0.005 us refinement around its peak.
    // The 0.020 us coarse step is narrower than a full-window coherence peak.
    for(unsigned pass=0;pass<2;++pass) {
     int low=pass?std::max(0,peakIndex-4):0, high=pass?std::min(900,peakIndex+4):900;
     for(int k=low;k<=high;k+=pass?1:4) {
      double p=86+k*.005; float re=0,im=0,compRe=0,compIm=0;
      for(unsigned j=0;j<edges;++j) {
        float angle=6.28318530718f*std::fmod(float(work[2*j]),float(p))/float(p);
        float weight=work[2*j+1];
        float a=weight*std::cos(angle)-compRe,b=weight*std::sin(angle)-compIm;
        float nextRe=re+a,nextIm=im+b;compRe=(nextRe-re)-a;compIm=(nextIm-im)-b;
        re=nextRe;im=nextIm;
      }
      double coherence=std::sqrt(double(re)*re+double(im)*im)/sumWeights;
      if(coherence>best) {best=coherence;period=p;phase=std::atan2(im,re)*p/6.28318530718;peakIndex=k;}
     }
    }
    if(phase<0) phase+=period;
    if(!local) { result.periodUs=period; result.coherence=best; }
    if(local && best<std::max(.3f,result.coherence)) return;
    int first=int(std::floor((rows[begin].timeUs-phase)/period));
    int last=int(std::floor((rows[end-1].timeUs-phase)/period));
    unsigned length=last-first+1; if(length>MaxCells) return;
    std::fill(cells,cells+length,0.f);
    for(unsigned i=begin;i<end;++i) {
      int cell=int(std::floor((rows[i].timeUs-phase)/period))-first;
      if(cell>=0 && unsigned(cell)<length) cells[cell]+=ratio[i];
    }
    for(unsigned inv=0;inv<2;++inv) for(unsigned pos=0;pos+264<=length;++pos) {
      unsigned errors=0;
      for(unsigned j=0;j<40;++j) errors+=(((cells[pos+j]>0)^bool(inv))!=(pattern(j)>0));
      result.nearestErrors=std::min(result.nearestErrors,errors);
      if(errors>2) continue;
      float sync=phase+(first+int(pos))*period;
      if(local && std::fabs(sync-nearSync)>500) continue;
      float payloadSoft[168];
      for(unsigned j=0;j<168;++j) payloadSoft[j]=cells[pos+40+j]*(inv?-1:1);
      check(payloadSoft,sync,period,errors,1,local?"local":"whole");
      if(best<.3f) continue;
      int slot=-1;
      for(unsigned j=0;j<copyCount;++j) if(std::fabs(copies[j].sync-sync)<500) slot=j;
      if(slot<0 && copyCount<MaxCopies) slot=copyCount++;
      if(slot<0) continue;
      auto &c=copies[slot]; c.sync=sync; c.period=period; c.coherence=best;
      c.begin=begin;c.end=end;c.errors=errors;c.inverted=inv;
      if(!softCopy(c)) c.coherence=0;
    }
  }
  void combine(const char *method) {
    unsigned used=0;
    for(unsigned i=0;i<copyCount;++i) {
      if((used&(1<<i)) || copies[i].coherence<.3f) continue;
      float soft[168]; std::copy(copies[i].soft,copies[i].soft+168,soft);
      unsigned count=1, last=i, errors=copies[i].errors;
      for(unsigned j=i+1;j<copyCount;++j) {
        if((used&(1<<j)) || copies[j].coherence<.3f) continue;
        float spacing=(copies[j].sync-copies[last].sync)/((copies[j].period+copies[last].period)*.5f);
        unsigned disagreement=0;
        for(unsigned k=0;k<168;++k) disagreement+=((copies[j].soft[k]>0)!=(copies[last].soft[k]>0));
        if(std::fabs(spacing-379)>2 || disagreement>12) continue;
        for(unsigned k=0;k<168;++k) soft[k]+=copies[j].soft[k];
        used|=1<<j; ++count; errors=std::max(errors,copies[j].errors);last=j;
      }
      if(count>=2) check(soft,copies[i].sync,copies[i].period,errors,count,method);
    }
  }
 public:
  VevorDecoder() {
    work.allocate();ratio.allocate();
    cells=new(std::nothrow) float[MaxCells]; copies=new(std::nothrow) Copy[MaxCopies];
  }
  ~VevorDecoder() {delete[] cells;delete[] copies;}
  VevorDecoder(const VevorDecoder&)=delete;
  VevorDecoder& operator=(const VevorDecoder&)=delete;
  bool ready() const {return work.ready() && ratio.ready() && cells && copies;}
  const VevorResult &decode(const Row *input,unsigned count,int preferredId=-1,unsigned snapshot=128) {
    result=VevorResult(); copyCount=0; rows=input;
    filter=preferredId>=0;station=uint16_t(preferredId); halfSnapshot=snapshot/32.f;
    if(!ready() || !input || count<128 || count>MaxRows || (snapshot!=128 && snapshot!=192)) {result.invalid=true;return result;}
    for(unsigned i=0;i<count;++i)
      if(!std::isfinite(rows[i].tone0Power) || !std::isfinite(rows[i].tone1Power) ||
          rows[i].tone0Power<0 || rows[i].tone1Power<0 || (i && rows[i].timeUs<=rows[i-1].timeUs)) {
        result.invalid=true;return result;
      }
    if(rows[count-1].timeUs>160000) {result.invalid=true;return result;}
    window(0,count);
    unsigned initial=copyCount;
    for(unsigned k=0;k<initial;++k) {
      float sync=copies[k].sync; unsigned begin=0,end=0;
      while(begin<count && rows[begin].timeUs<sync-16000) ++begin;
      end=begin; while(end<count && rows[end].timeUs<=sync+24000) ++end;
      window(begin,end,true,sync);
    }
    // Local windows overwrite ratio. Reconstruct each copy using its own noise floor
    // before sync fitting (by rerunning that local window), without checksum searches.
    std::sort(copies,copies+copyCount,[](const Copy&a,const Copy&b){return a.sync<b.sync;});
    if(!matching()) combine("repetition");
    if(!matching()) {
      for(unsigned k=0;k<copyCount;++k) {
        auto &c=copies[k]; if(c.coherence<.3f) continue;
        // Restore this copy's normalized ratios only; window() is not called here.
        unsigned n=c.end-c.begin;
        for(unsigned i=0;i<n;++i) work[i]=rows[c.begin+i].tone0Power+rows[c.begin+i].tone1Power;
        float quiet=percentile(n,.3f); unsigned q=0;
        for(unsigned i=c.begin;i<c.end;++i) if(rows[i].tone0Power+rows[i].tone1Power<=quiet) work[q++]=rows[i].tone0Power;
        float b0=std::max(percentile(q,.5f),1e-9f); q=0;
        for(unsigned i=c.begin;i<c.end;++i) if(rows[i].tone0Power+rows[i].tone1Power<=quiet) work[q++]=rows[i].tone1Power;
        float b1=std::max(percentile(q,.5f),1e-9f);
        for(unsigned i=c.begin;i<c.end;++i) ratio[i]=std::max(-8.f,std::min(8.f,std::log((rows[i].tone1Power/b1+1e-6f)/(rows[i].tone0Power/b0+1e-6f))));
        float scores[63], peak=-1;
        for(unsigned s=0;s<63;++s) {
          float shift=(-.35f+.7f*s/62)*c.period; double dot=0,a=0,b=0; unsigned training=0;
          for(unsigned i=c.begin;i<c.end;++i) {
            if(rows[i].timeUs<c.sync+c.period || rows[i].timeUs>=c.sync+39*c.period) continue;
            float u=(rows[i].timeUs-c.sync-shift)/c.period; int index=int(std::floor(u));
            float f=u-index, h=halfSnapshot/c.period, model=pattern(index);
            if(f<h) model=(pattern(index-1)*(h-f)+pattern(index)*(h+f))/(2*h);
            if(f>1-h) model=(pattern(index)*(1+h-f)+pattern(index+1)*(f-1+h))/(2*h);
            float measured=ratio[i]*(c.inverted?-1:1);
            dot+=measured*model;a+=measured*measured;b+=model*model;++training;
          }
          scores[s]=training>=40 ? dot/std::sqrt(std::max(a*b,1e-9)) : -1;
          peak=std::max(peak,scores[s]);
        }
        if(peak<.65f) { c.coherence=0;continue; }
        float shift=0;unsigned selected=0;
        for(unsigned s=0;s<63;++s) if(scores[s]>=peak-.001f) {shift+=(-.35f+.7f*s/62)*c.period;++selected;}
        c.sync+=shift/selected;
        if(!softCopy(c,true,true)) {c.coherence=0;continue;}
        check(c.soft,c.sync,c.period,c.errors,1,"sync");
      }
      if(!matching()) combine("sync_repetition");
    }
    return result;
  }
};
} // namespace esp32_harmonic_rx
#undef VEVOR_NOINLINE
