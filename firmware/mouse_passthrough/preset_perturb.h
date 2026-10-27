#ifndef FUSION_PRESET_PERTURB_H
#define FUSION_PRESET_PERTURB_H
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { PRESET_PERTURB_BYTES=16,PRESET_PERTURB_SCHEMA=1 };
enum { PRESET_PERTURB_LINEAR,PRESET_PERTURB_SMOOTH,PRESET_PERTURB_SMOOTHER };
typedef struct {
    bool enabled,fixed_seed_enabled;
    uint8_t smoothness; // 0 linear, 1 cubic smoothstep, 2 quintic smootherstep.
    uint16_t amplitude_x,amplitude_y,period_ms,max_deviation;
    uint32_t seed;
} preset_perturb_config_t;
preset_perturb_config_t preset_perturb_default(void);
bool preset_perturb_valid(const preset_perturb_config_t*);
bool preset_perturb_decode(preset_perturb_config_t*,const uint8_t*);
void preset_perturb_encode(uint8_t*,const preset_perturb_config_t*);
uint32_t preset_perturb_seed(const preset_perturb_config_t*,uint32_t*,uint64_t);
// Cumulative bounded position offset, not independent white-noise deltas.
// abs(x)+abs(y)<=max_deviation conservatively bounds Euclidean deviation.
void preset_perturb_offset(const preset_perturb_config_t*,uint32_t,uint32_t,uint32_t,int16_t*,int16_t*);
// Adds offset(t)-offset(t-1) to just the current sample. Never catches up skipped ticks.
bool preset_perturb_delta(const preset_perturb_config_t*,uint32_t,uint32_t,uint32_t,int16_t*,int16_t*);
#ifdef __cplusplus
}
#endif
#endif
