#ifndef FUSION_PASSTHROUGH_CORE_H
#define FUSION_PASSTHROUGH_CORE_H
#include "bridge_types.h"
#ifdef __cplusplus
extern "C" {
#endif
enum { BRIDGE_POLL_MAX_EVENTS = 16, BRIDGE_AUX_QUEUE_CAPACITY = 8 };
enum { BRIDGE_PERIOD_US = 1000, BRIDGE_COALESCE_US = 100, BRIDGE_SUBMIT_MARGIN_US = 100 };
#ifndef FUSION_BRIDGE_AUX_SIMULATION
#define FUSION_BRIDGE_AUX_SIMULATION 0
#endif
#ifndef FUSION_J4_AUX
#define FUSION_J4_AUX 0
#endif
#ifndef FUSION_TRAJECTORY
#define FUSION_TRAJECTORY 0
#endif
typedef struct {
    uint32_t physical_epoch, target_generation, queued, high_water, overflow;
    uint32_t discarded, auxiliary_dropped, snapshot_busy, degraded;
    uint32_t submitted, completed, in_flight;
    int64_t dx, dy, wheel, pan;
    uint16_t buttons;
} bridge_status_t;
void bridge_init(void); // Core0, before launching Core1; not a runtime recovery helper
void bridge_poll(uint64_t now_us, uint32_t max_events); // monotonic clock; bounded, never sleeps
bool bridge_prepare(mouse_report_t *report); // never debits movement; false during one in-flight report
bool bridge_submitted(void); // call only after successful USB submission
bool bridge_completed(void); // call only after exact full-report completion
void bridge_get_report(mouse_report_t *report); // current buttons, zero relative increments
// Core0-only API ingress. No buttons/wheel; return the corresponding J4 result.
uint8_t bridge_ai_offer(int16_t dx,int16_t dy,uint64_t received_us,uint64_t expires_us);
void bridge_ai_clear(void);
// Dedicated AI gate: physical epoch, target generation, left press count, overflow.
bool bridge_ai_gate_status(uint32_t gate[4],uint16_t *buttons);
uint8_t bridge_ai_offer_gated(int16_t dx,int16_t dy,uint64_t received_us,uint64_t expires_us,const uint32_t gate[4]);
uint8_t bridge_ai_offer_smooth(int16_t dx,int16_t dy,uint64_t received_us,uint64_t expires_us,const uint32_t gate[4],uint16_t duration_ms);
void bridge_ai_smooth_cancel(void);
void bridge_auxiliary_clear(void); // All unsent auxiliary sources; never physical or in-flight.
void bridge_bypass_set(bool);
bool bridge_auxiliary_inflight(void); // Already successfully submitted; cannot be withdrawn.
uint8_t bridge_trajectory_offer(int16_t dx,int16_t dy,uint64_t received_us,uint64_t expires_us);
void bridge_trajectory_clear(void);
// Core0 completion observer reports only the preset contribution actually sent.
void bridge_trajectory_observer(void (*callback)(void*,int16_t,int16_t,uint32_t),void *context);
bool bridge_trajectory_busy(void);
void bridge_ai_set_ready(bool ready); // J1 configured/active, independent of J2
bridge_status_t bridge_snapshot(void);
typedef struct {
    uint64_t received_us;
    uint32_t physical_epoch, first_sequence, last_sequence, neutral;
} bridge_trace_context_t;
bool bridge_prepared_trace(bridge_trace_context_t *context); // read-only diagnostic metadata
#ifdef __cplusplus
}
#endif
#endif
