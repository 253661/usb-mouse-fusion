#include <string.h>
#include "tusb.h"
#include "hid_output.h"
#include "mouse_report.h"
#include "passthrough_core.h"
#include "project_config.h"
#if FUSION_TRAJECTORY
#include "j4_preset.h"
static mouse_report_t preset_pending_report;
#endif
#if FUSION_DIAGNOSTIC_CDC
#include "pico/time.h"
#endif
static hid_output_status_t state;
static uint32_t pending_epoch;
static uint8_t pending[MOUSE_REPORT_BYTES];
#if FUSION_DIAGNOSTIC_CDC
enum { OUTPUT_TRACE_CAPACITY=32 };
static bool trace_enabled;
static uint32_t trace_write, trace_read, trace_drop;
static hid_output_trace_t trace_records[OUTPUT_TRACE_CAPACITY];
static bridge_trace_context_t pending_context;
static mouse_report_t pending_report;
static void trace(uint32_t kind) {
    if (!trace_enabled) return;
    if (trace_write-trace_read>=OUTPUT_TRACE_CAPACITY) { ++trace_drop; return; }
    hid_output_trace_t record={.kind=kind,.output_epoch=state.epoch,.sequence=state.submitted,
        .time_us=time_us_64(),.received_us=pending_context.received_us,
        .physical_epoch=pending_context.physical_epoch,.first_sequence=pending_context.first_sequence,
        .last_sequence=pending_context.last_sequence,.neutral=pending_context.neutral,.report=pending_report};
    trace_records[trace_write++ & (OUTPUT_TRACE_CAPACITY-1)]=record;
}
#else
static void trace(uint32_t kind) { (void)kind; }
#endif
void hid_output_set_trace(bool enabled) {
#if FUSION_DIAGNOSTIC_CDC
    trace_enabled=enabled;
#else
    (void)enabled;
#endif
}
bool hid_output_pop_trace(hid_output_trace_t *record) {
#if FUSION_DIAGNOSTIC_CDC
    if (!record || trace_read==trace_write) return false;
    *record=trace_records[trace_read++ & (OUTPUT_TRACE_CAPACITY-1)]; return true;
#else
    (void)record; return false;
#endif
}
uint32_t hid_output_trace_dropped(void) {
#if FUSION_DIAGNOSTIC_CDC
    return trace_drop;
#else
    return 0;
#endif
}
static void retire_failed_session(void) {
    hid_output_target_reset();
    hid_output_request_recovery(); // producer generation must also be rebuilt; never replay unknown motion
}
void hid_output_target_reset(void) {
    if (++state.epoch==0) ++state.epoch;
    state.in_flight=0; pending_epoch=0;
    memset(pending,0,sizeof(pending));
#if FUSION_DIAGNOSTIC_CDC
    pending_context=(bridge_trace_context_t){.neutral=1}; pending_report=(mouse_report_t){0};
#endif
    trace(OUTPUT_TRACE_CLEAR);
    target_session_reset(); // no cached buttons or unknown movement survive a target session
}
void hid_output_tick(void) {
    // Core0 has just run bounded bridge_poll, independently of CDC/DTR.
    if (!state.in_flight && tud_mounted() && !tud_suspended() && tud_hid_ready()) {
        mouse_report_t report;
        if (!bridge_prepare(&report)) return;
        mouse_report_encode(&report,pending);
#if FUSION_DIAGNOSTIC_CDC
        if (trace_enabled) { (void)bridge_prepared_trace(&pending_context); pending_report=report; }
#endif
        if (tud_hid_report(0,pending,sizeof(pending))) {
#if FUSION_TRAJECTORY
            preset_pending_report=report;
#endif
            (void)bridge_submitted(); pending_epoch=state.epoch;
            state.in_flight=1; ++state.submitted;
            trace(OUTPUT_TRACE_SUBMITTED);
        } else ++state.rejected; // submission false: no in-flight report, no debit
    }
}
hid_output_status_t hid_output_snapshot(void) { return state; }
void tud_hid_report_complete_cb(uint8_t instance,const uint8_t *report,uint16_t length) {
    if (instance!=0 || !state.in_flight || pending_epoch!=state.epoch) return;
    if (length!=sizeof(pending) || !report || memcmp(report,pending,sizeof(pending))!=0) { ++state.mismatched; return; }
    if (bridge_completed()) {
#if FUSION_TRAJECTORY
        preset_runtime_output(j4_preset_runtime(),&preset_pending_report);
#endif
        ++state.completed; state.in_flight=0; trace(OUTPUT_TRACE_COMPLETED); }
    else { ++state.mismatched; retire_failed_session(); }
}
void tud_hid_report_failed_cb(uint8_t instance,hid_report_type_t type,const uint8_t *report,uint16_t length) {
    (void)report; (void)length;
    if (instance==0 && type==HID_REPORT_TYPE_INPUT && state.in_flight && pending_epoch==state.epoch) {
        ++state.failed;
        trace(OUTPUT_TRACE_FAILED);
        // The result of a submitted transfer is unknown. Discard that output
        // session and release; do not replay possibly delivered increments.
        retire_failed_session();
    }
}
uint16_t tud_hid_get_report_cb(uint8_t instance,uint8_t report_id,hid_report_type_t type,uint8_t *buffer,uint16_t length) {
    if (instance!=0 || report_id!=0 || type!=HID_REPORT_TYPE_INPUT || !buffer || length<MOUSE_REPORT_BYTES) return 0;
    mouse_report_t current; bridge_get_report(&current); mouse_report_encode(&current,buffer);
    return MOUSE_REPORT_BYTES;
}
void tud_hid_set_report_cb(uint8_t instance,uint8_t report_id,hid_report_type_t type,const uint8_t *buffer,uint16_t length) {
    (void)instance; (void)report_id; (void)type; (void)buffer; (void)length;
}
