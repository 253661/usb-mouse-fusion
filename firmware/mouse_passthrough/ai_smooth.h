#ifndef FUSION_AI_SMOOTH_H
#define FUSION_AI_SMOOTH_H
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    uint64_t start_cycle,last_cycle;
    int16_t x,y;
    uint16_t ticks;
    bool active;
} ai_smooth_t;
void ai_smooth_reset(ai_smooth_t *state);
bool ai_smooth_begin(ai_smooth_t *state,int16_t x,int16_t y,uint64_t received_us,uint64_t expires_us,uint16_t duration_ms);
// One scheduled 1ms slice only. Skipped indices are discarded, never caught up.
bool ai_smooth_step(ai_smooth_t *state,uint64_t now_us,int16_t *x,int16_t *y);
#ifdef __cplusplus
}
#endif
#endif
