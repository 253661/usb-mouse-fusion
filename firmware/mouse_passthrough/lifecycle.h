#ifndef FUSION_LIFECYCLE_H
#define FUSION_LIFECYCLE_H
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef enum { LIFE_WAIT_TARGET, LIFE_POWER_SETTLE, LIFE_ENUMERATING, LIFE_READY,
               LIFE_QUIESCING, LIFE_FAULT_OFF, LIFE_REARMING } lifecycle_phase_t;
typedef struct {
    bool target, fault_n, quiesced, host_ready, host_failed, locked, armed;
    bool host_waiting; // Controller is running, but no physical device is attached.
} lifecycle_inputs_t;
// Requested actions, not GPIO writes. The locked image only applies safe/off actions.
typedef struct { bool en, oe_n, frame, quiesce, rearm, reset_target; } lifecycle_actions_t;
typedef struct {
    lifecycle_phase_t phase;
    uint64_t started_us;
    uint32_t attempts;
    bool fault_latched, recovery_used, recovery_requested, recovery_eligible, last_target;
} lifecycle_t;
void lifecycle_init(lifecycle_t *state);
void lifecycle_target_reset(lifecycle_t *state,uint64_t now_us); // even reset+configure in a single service iteration
lifecycle_actions_t lifecycle_step(lifecycle_t *state, lifecycle_inputs_t inputs, uint64_t now_us);
bool lifecycle_request_recovery(lifecycle_t *state); // once, explicit; never bypasses safety/lock
#ifdef __cplusplus
}
#endif
#endif
