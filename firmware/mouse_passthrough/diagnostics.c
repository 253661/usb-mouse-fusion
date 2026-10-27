#include <stdio.h>
#include <string.h>
#include "tusb.h"
#include "hardware/clocks.h"
#include "board_io.h"
#include "host_port.h"
#include "hid_output.h"
#include "passthrough_core.h"
#include "project_config.h"
#include "diagnostics.h"
#include "bringup_control.h"
static bool clock_ok;
void diagnostics_set_clock_status(bool ok) { clock_ok=ok; }
#if FUSION_DIAGNOSTIC_CDC
static char input[64], reply[2048];
static unsigned input_size, reply_size, reply_sent;
static uint32_t diagnostic_reply_drop, input_overflow, reply_truncated;
static bool discarding_line;
static bool prefer_output;
static const char *const j2_profile=FUSION_CONTROLLED_J2 ? "CONTROLLED" : "LOCKED_OFF";
static void reply_length(int used) {
    if (used>=(int)sizeof(reply)) ++reply_truncated;
    reply_size=used<=0 ? 0u : used>=(int)sizeof(reply) ? (unsigned)sizeof(reply)-1u : (unsigned)used;
    reply_sent=0;
}
static void respond(const char *text) {
    if (reply_sent<reply_size) { ++diagnostic_reply_drop; return; }
    int used=snprintf(reply,sizeof(reply),"%s\r\n",text);
    reply_length(used);
}
static bool respond_output(void) {
    hid_output_trace_t record;
    if (!hid_output_pop_trace(&record)) return false;
    static const char *const names[]={"INVALID","SUBMITTED","COMPLETED","FAILED","CLEAR"};
    int used=snprintf(reply,sizeof(reply),
        "%s epoch=%lu seq=%lu physical_epoch=%lu first=%lu last=%lu rx_us=%llu time_us=%llu neutral=%lu buttons=%04x dx=%d dy=%d wheel=%d pan=%d\r\n",
        names[record.kind<=OUTPUT_TRACE_CLEAR ? record.kind : 0],(unsigned long)record.output_epoch,
        (unsigned long)record.sequence,(unsigned long)record.physical_epoch,(unsigned long)record.first_sequence,
        (unsigned long)record.last_sequence,(unsigned long long)record.received_us,(unsigned long long)record.time_us,
        (unsigned long)record.neutral,(unsigned)record.report.buttons,record.report.x,record.report.y,record.report.wheel,record.report.pan);
    reply_length(used); return true;
}
static void command(uint64_t now_us) {
    input[input_size]=0;
    if (strcmp(input,"STATUS")==0) {
        if (reply_sent<reply_size) { ++diagnostic_reply_drop; return; }
        host_port_status_t host;
        bool coherent=host_port_snapshot(&host);
        board_io_status_t board=board_io_snapshot();
        hid_output_status_t out=hid_output_snapshot();
        bridge_status_t bridge=bridge_snapshot();
        int used=snprintf(reply,sizeof(reply),
            "STATUS task=J2_PREP mode=RAM j2=%s time_us=%llu usb=1209:0001 private_test=1 cdc=1\r\n"
            "CLOCK ok=%u sys=%lu usb=%lu GPIO EN=%lu OE_N=%lu en_sense=%lu oe_sense=%lu FAULT_N=%lu TARGET_VBUS=%lu\r\n"
            "HOST phase=%s error=%lu initialized=%lu init_complete=%lu frame_gate=%lu alarm_core=%lu snapshot_coherent=%u epoch=%lu interfaces=%lu frames=%lu dp_pad=%08lx dm_pad=%08lx\r\n"
            "COUNTERS raw_rx=%lu normalized_unavailable=%lu control_ok=%lu control_fail=%lu queue_fail=%lu detach=%lu log_drop=%lu output_log_drop=%lu reply_drop=%lu input_overflow=%lu reply_truncated=%lu\r\n"
            "PARSER valid=%lu ignored=%lu invalid=%lu stale=%lu supported_interfaces=%lu selected_instance=%lu partial=%lu mouse_apps=%lu rejected_mouse_itfs=%lu unknown_itfs=%lu\r\n"
            "OUTPUT epoch=%lu submitted=%lu completed=%lu failed=%lu in_flight=%lu rejected=%lu mismatched=%lu\r\n"
            "BRIDGE epoch=%lu target_gen=%lu queued=%lu high_water=%lu overflow=%lu discarded=%lu forward_drop=%lu snapshot_busy=%lu degraded=%lu buttons=%04x physical_remaining=%lld,%lld,%lld,%lld auxiliary_drop=%lu\r\n"
            "CLEANUP status=%lu reason=%lu rearm=SOFTWARE_TICKETS\r\n"
            "CONTROL mode=%lu default=OFF power=DATA_ISOLATED arm=EXPLICIT recover=ONCE_TO_OFF\r\n"
            "CAPABILITY parser=FIXED_LAYOUT_SUBSET physical_forwarding=FIFO_LEDGER j2=%s AI=NOT_IMPLEMENTED trajectory=NOT_IMPLEMENTED hardware=NOT_TESTED\r\n",
            j2_profile,(unsigned long long)now_us,clock_ok,(unsigned long)clock_get_hz(clk_sys),(unsigned long)clock_get_hz(clk_usb),
            (unsigned long)board.en_latch,(unsigned long)board.oe_n_latch,(unsigned long)board.en_sense,
            (unsigned long)board.oe_n_sense,(unsigned long)board.fault_n,(unsigned long)board.target_vbus,
            host_port_phase_name(host.phase),(unsigned long)host.error,(unsigned long)host.initialized,
            (unsigned long)host.initialization_complete,
            (unsigned long)host.frame_gate,(unsigned long)host.alarm_core,coherent,(unsigned long)host.epoch,
            (unsigned long)host.interfaces,(unsigned long)host.frames,(unsigned long)host.dp_pad,(unsigned long)host.dm_pad,
            (unsigned long)host.raw_rx,(unsigned long)host.normalization_unavailable,(unsigned long)host.control_ok,
            (unsigned long)host.control_fail,(unsigned long)host.queue_fail,(unsigned long)host.detach_count,
            (unsigned long)host_port_log_dropped(),(unsigned long)hid_output_trace_dropped(),(unsigned long)diagnostic_reply_drop,(unsigned long)input_overflow,(unsigned long)reply_truncated,
            (unsigned long)host.normalized_rx,(unsigned long)host.ignored_rx,(unsigned long)host.invalid_rx,(unsigned long)host.stale_rx,
            (unsigned long)host.supported_interfaces,(unsigned long)host.selected_instance,(unsigned long)host.partial,
            (unsigned long)host.mouse_applications,(unsigned long)host.rejected_mouse_interfaces,(unsigned long)host.unknown_interfaces,
            (unsigned long)out.epoch,(unsigned long)out.submitted,(unsigned long)out.completed,(unsigned long)out.failed,(unsigned long)out.in_flight,
            (unsigned long)out.rejected,(unsigned long)out.mismatched,
            (unsigned long)bridge.physical_epoch,(unsigned long)bridge.target_generation,(unsigned long)bridge.queued,(unsigned long)bridge.high_water,
            (unsigned long)bridge.overflow,(unsigned long)bridge.discarded,(unsigned long)host.forward_drop,(unsigned long)bridge.snapshot_busy,
            (unsigned long)bridge.degraded,(unsigned)bridge.buttons,
            (long long)bridge.dx,(long long)bridge.dy,(long long)bridge.wheel,(long long)bridge.pan,(unsigned long)bridge.auxiliary_dropped,
            (unsigned long)host.cleanup_status,(unsigned long)host.cleanup_reason,(unsigned long)fusion_bringup_mode(),j2_profile);
        reply_length(used);
    } else if (strcmp(input,"HOST POWER")==0 || strcmp(input,"HOST ARM")==0 ||
               strcmp(input,"HOST RECOVER")==0 || strcmp(input,"HOST OFF")==0) {
        const bringup_command_t action=strcmp(input,"HOST OFF")==0 ? BRINGUP_OFF :
            strcmp(input,"HOST POWER")==0 ? BRINGUP_POWER : strcmp(input,"HOST ARM")==0 ? BRINGUP_ARM : BRINGUP_RECOVER;
        // OFF executes even under diagnostic backpressure; enabling commands do not.
        if(action!=BRINGUP_OFF && reply_sent<reply_size) { ++diagnostic_reply_drop; return; }
        const bringup_result_t result=fusion_bringup_execute(action,now_us);
        char response[160];
        (void)snprintf(response,sizeof(response),"HOST %s result=%s mode=%lu; inspect STATUS; hardware NOT_TESTED",
            input+5,bringup_result_name(result),(unsigned long)fusion_bringup_mode());
        respond(response);
    } else if (strcmp(input,"DESC")==0) {
        host_port_request_dump(); respond("DESC requested; NOT_AVAILABLE until real capture; bounded raw/layout/capability chunks");
    } else if (strcmp(input,"TRACE ON")==0) {
        host_port_set_trace(true); respond("TRACE ON (bounded raw diagnostic log only)");
        hid_output_set_trace(true);
    } else if (strcmp(input,"TRACE OFF")==0) {
        host_port_set_trace(false); respond("TRACE OFF");
        hid_output_set_trace(false);
    } else if (input_size) respond("UNKNOWN_COMMAND");
}
#endif
void diagnostics_tick(uint64_t now_us) {
#if FUSION_DIAGNOSTIC_CDC
    // DTR gates only diagnostics. No Host, HID or physical input service is inside this branch.
    if (!tud_cdc_connected()) {
        input_size=0; discarding_line=false; reply_size=reply_sent=0;
        return;
    }
    for (unsigned budget=0;budget<32 && tud_cdc_available();++budget) {
        char ch=(char)tud_cdc_read_char();
        if (ch=='\r' || ch=='\n') {
            if (!discarding_line && input_size) command(now_us);
            input_size=0; discarding_line=false;
        } else if (!discarding_line) {
            if (input_size+1<sizeof(input)) input[input_size++]=ch;
            else { ++input_overflow; input_size=0; discarding_line=true; }
        }
    }
    if (reply_sent>=reply_size) {
        log_record_t record;
        bool output=prefer_output && respond_output();
        if (!output && host_port_pop_log(&record)) respond(record.text);
        else if (!output) (void)respond_output();
        prefer_output=!prefer_output;
    }
    unsigned capacity=tud_cdc_write_available();
    unsigned amount=reply_size-reply_sent;
    if (amount>64) amount=64;
    if (amount>capacity) amount=capacity;
    if (amount) reply_sent+=tud_cdc_write(reply+reply_sent,amount);
    (void)tud_cdc_write_flush(); // TinyUSB queues data; never wait for PC consumption
#else
    (void)now_us; (void)clock_ok;
#endif
}
