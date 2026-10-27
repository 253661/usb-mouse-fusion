#ifndef FUSION_PRESET_TRIGGER_H
#define FUSION_PRESET_TRIGGER_H
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { PRESET_TRIGGER_MAX=16,PRESET_TRIGGER_WIRE_BYTES=12,PRESET_TRIGGER_WHEEL_QUEUE=16 };
typedef struct {
    uint8_t mask,gesture,action,target,behavior,policy;
    uint16_t window_ms,hold_ms;uint8_t debounce_ms,enabled;
} preset_trigger_record_t;
typedef struct {uint64_t start;uint8_t state;bool active;} preset_trigger_state_t;
typedef struct {uint64_t at;uint8_t mask,buttons,stable_buttons;} preset_trigger_wheel_t;
typedef struct {
    preset_trigger_record_t records[PRESET_TRIGGER_MAX];preset_trigger_state_t states[PRESET_TRIGGER_MAX];
    uint64_t edges[5],due[5],down_at[5],up_at[5];uint32_t epoch;
    uint16_t release_pending;uint8_t count,raw,pressed,pending,consumed;
    preset_trigger_wheel_t wheels[PRESET_TRIGGER_WHEEL_QUEUE];uint8_t wheel_count,selected;
    bool scope_set,scope_enabled;
} preset_trigger_t;
typedef void (*preset_trigger_callback_t)(void*,unsigned,const preset_trigger_record_t*,bool);
void preset_trigger_init(preset_trigger_t*);
uint8_t preset_trigger_configure(preset_trigger_t*,const preset_trigger_record_t*,unsigned);
void preset_trigger_decode(preset_trigger_record_t*,const uint8_t*);
void preset_trigger_encode(uint8_t*,const preset_trigger_record_t*);
void preset_trigger_observe(preset_trigger_t*,uint8_t,uint32_t,uint64_t);
void preset_trigger_wheel(preset_trigger_t*,int32_t,uint32_t,uint64_t);
void preset_trigger_set_scope(preset_trigger_t*,uint8_t,bool);
void preset_trigger_tick(preset_trigger_t*,uint64_t,preset_trigger_callback_t,void*);
void preset_trigger_reset(preset_trigger_t*,preset_trigger_callback_t,void*);
#ifdef __cplusplus
}
#endif
#endif
