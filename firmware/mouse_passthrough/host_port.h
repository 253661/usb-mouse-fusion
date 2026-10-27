#ifndef FUSION_HOST_PORT_H
#define FUSION_HOST_PORT_H
#include <stdbool.h>
#include <stdint.h>
#include "log_queue.h"
typedef enum { HOST_SAFE_OFF, HOST_INIT_FAILED, HOST_ENUMERATING, HOST_CAPTURE_ONLY,
               HOST_NO_MOUSE, HOST_UNSUPPORTED, HOST_FAULT_OFF } host_phase_t;
typedef enum { HOST_ERROR_NONE, HOST_ERROR_CORE, HOST_ERROR_CLOCK, HOST_ERROR_RESOURCE,
               HOST_ERROR_ALARM, HOST_ERROR_CONFIGURE, HOST_ERROR_INIT, HOST_ERROR_PULL,
               HOST_ERROR_CONTROL, HOST_ERROR_TIMEOUT, HOST_ERROR_DESCRIPTOR, HOST_ERROR_SESSION,
               HOST_ERROR_CLEANUP_UNVERIFIED, HOST_ERROR_EVENT_QUEUE } host_error_t;
typedef enum { HOST_QUIESCE_TARGET=1, HOST_QUIESCE_FAULT=2, HOST_QUIESCE_USER=3 } host_quiesce_reason_t;
typedef struct {
    uint32_t phase, error, initialized, frame_gate, alarm_core, epoch, interfaces,
             raw_rx, normalization_unavailable, control_ok, control_fail, queue_fail,
             detach_count, frames, dp_pad, dm_pad, dump_active,
             normalized_rx, ignored_rx, invalid_rx, stale_rx, supported_interfaces, selected_instance, partial,
             mouse_applications, rejected_mouse_interfaces, unknown_interfaces, forward_drop,
             cleanup_status, cleanup_reason, initialization_complete;
} host_port_status_t;
// initialized alone means the HCD was acquired and can be cleaned up. Enabling
// requires the subsequent pad/resource checks and frame timer to have succeeded.
static inline bool host_port_initialization_complete(const host_port_status_t *s) {
    return s->initialized!=0 && s->initialization_complete!=0;
}
void host_port_prepare(void); // Core0, before launching Core1
bool host_port_init_core1(void); // Core1, at most one attempt, no application low-level init
// Core1; application controls are asynchronous. Pinned TinyUSB active enumeration
// contains reset/debounce delays; the locked image cannot enter that bus path.
void host_port_tick(uint64_t now_us);
bool host_port_snapshot(host_port_status_t *status); // Core0; at most two read attempts
bool host_port_pop_log(log_record_t *record); // Core0 only
uint32_t host_port_log_dropped(void);
void host_port_request_dump(void); // value mailbox, no wait
void host_port_set_trace(bool enabled);
void host_port_request_quiesce(host_quiesce_reason_t reason); // Core0, persistent value/ticket mailbox
bool host_port_quiesced(void); // acknowledgement for the latest ticket only
bool host_port_request_rearm(void); // software only; resources retained, frame stays off until a separate command
bool host_port_armed(void); // latest rearm ACK
void host_port_set_frames(bool run); // refuses run in the locked profile
const char *host_port_phase_name(uint32_t phase);
#endif
