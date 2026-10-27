#ifndef FUSION_PRESET_RULE_H
#define FUSION_PRESET_RULE_H
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { PRESET_RULE_BYTES=28, PRESET_RULE_SCHEMA=1 };
enum { PRESET_RUN_ONCE,PRESET_RUN_LOOP,PRESET_RUN_COUNT,PRESET_RUN_DURATION,PRESET_RUN_HOLD };
enum { PRESET_STOP_KEEP,PRESET_STOP_RETURN };
enum { PRESET_JOIN_RETURN,PRESET_JOIN_CONTINUE };
enum { PRESET_EASE_LINEAR,PRESET_EASE_IN,PRESET_EASE_OUT,PRESET_EASE_SMOOTH };
enum { PRESET_PHASE_IDLE,PRESET_PHASE_PATH,PRESET_PHASE_WAIT,PRESET_PHASE_RETURN,PRESET_PHASE_TRANSITION };
enum { PRESET_REASON_NONE,PRESET_REASON_FINISHED,PRESET_REASON_STOPPED,PRESET_REASON_RETURNED,
       PRESET_REASON_INCOMPLETE,PRESET_REASON_TARGET_RESET,PRESET_REASON_RANGE,PRESET_REASON_CHANGED,
       PRESET_REASON_BYPASS,PRESET_REASON_INPUT_RESET };
enum { PRESET_EXEC_STOP,PRESET_EXEC_KEEP,PRESET_EXEC_RETURN,PRESET_EXEC_SWITCH };
typedef struct {
    uint8_t run_mode,stop_action,loop_join,return_ease;
    uint32_t repeat_count,duration_ms;
    uint16_t return_ms,transition_ms;
    uint8_t transition_action,transition_ease;
    int32_t origin_x,origin_y; // HID world counts; Y down, never screen pixels.
} preset_rule_t;
typedef struct {
    preset_rule_t rule;
    int64_t x,y,from_x,from_y,to_x,to_y;
    uint64_t started_us,transition_us;
    uint32_t cycles,elapsed_ms,transition_tick,return_skipped;
    uint16_t transition_ticks;
    uint8_t phase,reason,slot,target_slot,next,ease;
    bool known,held;
} preset_motion_t;
preset_rule_t preset_rule_default(void);
bool preset_rule_valid(const preset_rule_t*);
void preset_rule_encode(uint8_t*,const preset_rule_t*);
bool preset_rule_decode(preset_rule_t*,const uint8_t*);
int64_t preset_eased_position(int64_t delta,uint32_t tick,uint32_t total,uint8_t ease);
#ifdef __cplusplus
}
#endif
#endif
