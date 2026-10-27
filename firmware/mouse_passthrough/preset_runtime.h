#ifndef FUSION_PRESET_RUNTIME_H
#define FUSION_PRESET_RUNTIME_H
#include "trajectory.h"
#include "bridge_types.h"
#include "preset_trigger.h"
#include "preset_rule.h"
#include "event_bus.h"
#ifdef __cplusplus
extern "C" {
#endif
enum { PRESET_NOT_SUPPORTED=11,PRESET_BUSY=12,PRESET_BAD_CRC=13,PRESET_UNSAFE=14,PRESET_STORAGE_ERROR=15,PRESET_STALE=16,PRESET_BYPASS=17,PRESET_AUX_EPOCH=18,PRESET_MODE_OFF=19 };
typedef struct {
    uint32_t epoch,generation,entries,press_count;
    uint8_t mask,raw;
    bool active,input_valid,rearm;
} preset_bypass_t;
typedef struct {
    uint8_t slots[PRESET_MODE_COUNT][PRESET_SLOT_BYTES],upload[PRESET_SLOT_BYTES];
    uint32_t sizes[PRESET_MODE_COUNT];trajectory_view_t views[PRESET_MODE_COUNT],upload_view;
    trajectory_player_t player;
    preset_rule_t rules[PRESET_MODE_COUNT];preset_motion_t motion;
    uint64_t now_us;uint32_t target_generation,bridge_accepted,bridge_dropped;
    int64_t output_dx,output_dy;uint32_t output_reports;
    uint32_t upload_token,upload_crc,upload_expected_crc,upload_received,upload_size,validate_block,last_apply_token;
    uint8_t selected,active_slot,upload_slot,upload_state,last_error;
    bool ready;
    bool mode_enabled; // Transient schema-2 mode-toggle armed state.
    preset_trigger_t triggers;
    preset_bypass_t bypass; // Global safety override, never a trajectory slot.
    uint32_t run_serial,held_run[PRESET_TRIGGER_MAX],trigger_activated,trigger_released;
    uint32_t perturb_seed,perturb_sequence;
    uint32_t config_revision;bool maintenance_pending;
} preset_runtime_t;
void preset_config_changed(preset_runtime_t*);
void preset_runtime_init(preset_runtime_t *runtime);
uint8_t preset_apply(preset_runtime_t *runtime,uint8_t slot,const uint8_t *image,size_t size);
uint8_t preset_upload_begin(preset_runtime_t *runtime,uint8_t slot,uint32_t size,uint32_t crc,uint32_t token);
uint8_t preset_upload_chunk(preset_runtime_t *runtime,uint32_t token,uint32_t offset,const uint8_t *data,size_t size);
uint8_t preset_upload_commit(preset_runtime_t *runtime,uint32_t token);
void preset_upload_abort(preset_runtime_t *runtime);
uint8_t preset_control(preset_runtime_t *runtime,uint8_t command,uint8_t slot,uint64_t now_us);
uint8_t preset_set_rule(preset_runtime_t*,uint8_t,const preset_rule_t*);
uint8_t preset_execute(preset_runtime_t*,uint8_t action,uint8_t slot,uint16_t duration_ms,uint8_t ease,uint32_t expected_crc,uint64_t now_us);
bool preset_runtime_busy(const preset_runtime_t*);
bool preset_mode_controlled(const preset_runtime_t*);
void preset_motion_init(preset_runtime_t*);
void preset_motion_cancel(preset_runtime_t*,uint8_t,bool);
uint8_t preset_motion_start(preset_runtime_t*,uint8_t,uint64_t,bool);
void preset_motion_tick(preset_runtime_t*,uint64_t);
void preset_runtime_tick(preset_runtime_t *runtime,uint64_t now_us,bool target_ready);
void preset_runtime_output(preset_runtime_t *runtime,const mouse_report_t *report);
void preset_runtime_observe(preset_runtime_t*,uint8_t,uint32_t,uint64_t);
void preset_runtime_observe_wheel(preset_runtime_t*,uint8_t,int32_t,uint32_t,uint64_t);
void preset_runtime_physical_reset(preset_runtime_t*);
bool preset_bypass_mask_valid(uint8_t);
bool preset_bypass_inhibited(const preset_runtime_t*);
uint8_t preset_bypass_configure(preset_runtime_t*,uint8_t);
void preset_bypass_observe(preset_runtime_t*,uint8_t,uint32_t,bool);
void preset_bypass_snapshot(preset_runtime_t*,const physical_snapshot_t*,bool);
#ifdef __cplusplus
}
#endif
#endif
