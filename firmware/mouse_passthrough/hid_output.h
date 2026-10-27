#ifndef FUSION_HID_OUTPUT_H
#define FUSION_HID_OUTPUT_H
#include <stdint.h>
#include "bridge_types.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { uint32_t epoch, submitted, completed, failed, in_flight, rejected, mismatched; } hid_output_status_t;
void hid_output_target_reset(void);
void hid_output_tick(void);
void hid_output_request_recovery(void); // Core0 application: retire Host session after unknown output result
hid_output_status_t hid_output_snapshot(void);
enum { OUTPUT_TRACE_SUBMITTED=1, OUTPUT_TRACE_COMPLETED, OUTPUT_TRACE_FAILED, OUTPUT_TRACE_CLEAR };
typedef struct {
    uint64_t time_us, received_us;
    uint32_t kind, output_epoch, sequence, physical_epoch, first_sequence, last_sequence, neutral;
    mouse_report_t report;
} hid_output_trace_t;
// Core0 only: fixed queue; no formatting, waiting or physical backpressure.
void hid_output_set_trace(bool enabled);
bool hid_output_pop_trace(hid_output_trace_t *record);
uint32_t hid_output_trace_dropped(void);
#ifdef __cplusplus
}
#endif
#endif
