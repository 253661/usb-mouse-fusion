#include <stdio.h>
#include <string.h>
#include "pico/stdlib.h"
#include "pico/time.h"
#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/pio.h"
#include "hardware/structs/padsbank0.h"
#include "hardware/regs/pads_bank0.h"
#include "tusb.h"
#include "host/hcd.h"
#include "pio_usb.h"
#include "pio_usb_ll.h"
#include "host_session.h"
#include "host_pio_state.h"
#include "host_commands.h"
#include "usb_tx.pio.h"
#include "usb_rx.pio.h"
#include "atomic_u32.h"
#include "host_port.h"
#include "hid_input.h"
#include "hid_selection.h"
#include "event_bus.h"
#include "project_config.h"
_Static_assert(FUSION_HOST_INTERFACE_MAX<=HID_SELECTION_INTERFACE_MAX,"Selection interface capacity");

typedef struct {
    bool mounted, capture_ready, receive_pending;
    uint8_t address, instance, number, subclass, protocol, endpoint, interval;
    uint16_t packet_bytes, declared, actual;
    uint32_t epoch, sequence, protocol_result, receive_epoch, host_generation;
    uint8_t descriptor[FUSION_CAPTURE_DESC_BYTES];
    hid_layout_t layout;
    hid_input_session_t session;
} interface_capture_t;
typedef enum { CAP_IDLE, CAP_DEVICE, CAP_CONFIG_HEADER, CAP_CONFIG, CAP_REPORT, CAP_PROTOCOL } capture_step_t;
static interface_capture_t interfaces[FUSION_HOST_INTERFACE_MAX];
static uint8_t device_desc[18], config_desc[FUSION_CAPTURE_DESC_BYTES];
static uint16_t config_length;
static uint8_t device_address, capture_slot;
static capture_step_t capture_step;
static bool init_attempted, control_pending, device_ready;
static uint64_t enum_deadline, control_deadline;
static uint32_t control_ticket, control_epoch, control_generation, session_generation;
static uint32_t detach_notice, lost_event_notice, dump_request, trace_enabled;
static uint32_t frame_gate; // Core1 main/IRQ only; gated by profile and latest rearm/frame ticket
static uint32_t frame_count;
static alarm_pool_t *frame_pool;
static repeating_timer_t frame_timer;
static log_queue_t logs;
static host_port_status_t status;
static uint32_t published_version, published[sizeof(host_port_status_t)/sizeof(uint32_t)];
static uint32_t dump_kind, dump_slot, dump_offset;
static host_commands_t commands;
static uint32_t quiesce_seen, rearm_seen;
static uint64_t quiesce_started;
static bool quiesce_pending, quiesce_hold;

static void log_line(const char *text) { (void)log_queue_push(&logs,text); }
static void log_interface(const char *kind, const interface_capture_t *entry) {
    char line[LOG_RECORD_BYTES];
    (void)snprintf(line,sizeof(line),
        "%s epoch=%lu addr=%u inst=%u itf=%u sub=%u proto=%u ep=%02x mps=%u poll=%u desc=%u actual=%u protocol_result=%lu",
        kind,(unsigned long)entry->epoch,entry->address,entry->instance,entry->number,
        entry->subclass,entry->protocol,entry->endpoint,entry->packet_bytes,entry->interval,
        entry->declared,entry->actual,(unsigned long)entry->protocol_result);
    log_line(line);
}
static void log_bytes(const char *kind, uint32_t slot, uint32_t sequence, const uint8_t *data,
                      uint16_t length, uint16_t offset) {
    char line[LOG_RECORD_BYTES];
    int used=snprintf(line,sizeof(line),"%s epoch=%lu slot=%lu seq=%lu len=%u off=%u data=",
                      kind,(unsigned long)status.epoch,(unsigned long)slot,(unsigned long)sequence,length,offset);
    unsigned end=offset+32u;
    if (end > length) end=length;
    for (unsigned i=offset; i<end && used+3<(int)sizeof(line); ++i)
        used+=snprintf(line+used,sizeof(line)-(size_t)used,"%02x",data[i]);
    log_line(line);
}
static void publish(void) {
    uint32_t words[sizeof(status)/sizeof(uint32_t)];
    status.frame_gate=fusion_load_u32(&frame_gate);
    status.frames=fusion_load_u32(&frame_count);
    status.dp_pad=pads_bank0_hw->io[FUSION_HOST_DP_PIN];
    status.dm_pad=pads_bank0_hw->io[FUSION_HOST_DM_PIN];
    memcpy(words,&status,sizeof(words));
    uint32_t version=fusion_load_u32(&published_version);
    // Core1 is the only writer. Rewriting identical status on every idle tick
    // leaves too little stable time for Core0's two bounded snapshot attempts.
    // All changed fields, including faults/epochs/frame counters, still publish.
    if (version && memcmp(words,published,sizeof(words))==0) return;
    fusion_store_u32(&published_version,version+1);
    for (unsigned i=0;i<sizeof(words)/sizeof(words[0]);++i) fusion_store_u32(&published[i],words[i]);
    fusion_store_u32(&published_version,version+2);
}
static void invalidate_capture(void) {
    source_invalidate(SOURCE_PHYSICAL,status.epoch); // independent mailbox, even with full FIFO
    control_pending=false;
    ++control_ticket; // late callback cannot match a new transaction
    capture_step=CAP_IDLE;
    device_ready=false;
    for (unsigned i=0;i<FUSION_HOST_INTERFACE_MAX;++i) hid_input_session_end(&interfaces[i].session);
    memset(interfaces,0,sizeof(interfaces));
    memset(device_desc,0,sizeof(device_desc));
    memset(config_desc,0,sizeof(config_desc));
    config_length=0;
    status.interfaces=0;
    status.supported_interfaces=0; status.selected_instance=HID_LAYOUT_NO_APPLICATION; status.partial=0;
    status.mouse_applications=status.rejected_mouse_interfaces=status.unknown_interfaces=0;
    status.dump_active=0;
    dump_kind=0;
}
static void log_enum_timeout(void) {
    fusion_usbh_probe_t probe;
    unsigned connected,fullspeed,suspended,dp,dm,pending=0,failed=0,total=0,actual=0;
    int slot=-1;
    const uint32_t irq=save_and_disable_interrupts();
    fusion_usbh_probe(&probe);
    connected=pio_usb_root_port[0].connected;
    fullspeed=pio_usb_root_port[0].is_fullspeed;
    suspended=pio_usb_root_port[0].suspended;
    dp=gpio_get(FUSION_HOST_DP_PIN); dm=gpio_get(FUSION_HOST_DM_PIN);
    for (unsigned i=0;i<PIO_USB_EP_POOL_CNT;++i) {
        const endpoint_t *ep=&pio_usb_ep_pool[i];
        if (ep->size && ep->dev_addr==probe.daddr && !(ep->ep_num&0x7f)) {
            slot=(int)i; pending=ep->has_transfer; failed=ep->failed_count;
            total=ep->total_len; actual=ep->actual_len; break;
        }
    }
    restore_interrupts(irq);
    char line[LOG_RECORD_BYTES];
    (void)snprintf(line,sizeof(line),
        "HOST ENUM_TRACE gen=%lu enum=%lu mounted=%lu stage=%lu next=%lu addr=%lu req=%02lx val=%04lx bytes=%lu got=%lu",
        (unsigned long)probe.generation,(unsigned long)probe.enumerating,(unsigned long)probe.configured,
        (unsigned long)probe.stage,(unsigned long)probe.next_step,(unsigned long)probe.daddr,
        (unsigned long)probe.request,(unsigned long)probe.value,(unsigned long)probe.length,(unsigned long)probe.actual_len);
    log_line(line);
    (void)snprintf(line,sizeof(line),
        "HOST PIO_TRACE conn=%u fs=%u susp=%u DP=%u DM=%u ep=%d pending=%u failed=%u got=%u total=%u",
        connected,fullspeed,suspended,dp,dm,slot,pending,failed,actual,total);
    log_line(line);
}
static void fail(host_error_t error) {
    fusion_store_u32(&frame_gate,0);
    if (error==HOST_ERROR_TIMEOUT) log_enum_timeout();
    quiesce_hold=true;
    status.error=error;
    status.phase=status.initialized ? HOST_FAULT_OFF : HOST_INIT_FAILED;
    fusion_store_u32(&commands.quiesce_ack,0);
    fusion_store_u32(&commands.rearm_ack,0);
    status.cleanup_status=1; // Core0 observes the error and publishes a fresh cleanup ticket
    invalidate_capture();
    enum_deadline=0;
    log_line("HOST FAULT_OFF; frame closed; fresh cleanup required");
}
static bool frame_callback(repeating_timer_t *timer) {
    (void)timer;
    if (fusion_load_u32(&frame_gate) && host_commands_run(&commands,rearm_seen) && !fusion_host_cancel_requested()) {
        fusion_store_u32(&frame_count,fusion_load_u32(&frame_count)+1);
        pio_usb_host_frame();
    }
    return true;
}
void host_port_prepare(void) {
    log_queue_init(&logs); status.selected_instance=HID_LAYOUT_NO_APPLICATION;
    host_commands_quiesce(&commands,HOST_QUIESCE_TARGET);
}
bool host_port_init_core1(void) {
    if (get_core_num()!=1) return false;
    if (init_attempted) return status.initialized!=0 && status.error==HOST_ERROR_NONE;
    init_attempted=true;
    fusion_store_u32(&frame_gate,0);
    status.phase=HOST_SAFE_OFF;
    if (clock_get_hz(clk_sys)!=FUSION_SYS_HZ || clock_get_hz(clk_usb)!=FUSION_USB_HZ) {
        fail(HOST_ERROR_CLOCK); publish(); return false;
    }
    // All 32 PIO0 instruction words are needed: TX=5 + RX=17 + EOP=10.
    const uint16_t placeholder[32]={0};
    const struct pio_program footprint={.instructions=placeholder,.length=32,.origin=0};
    if (pio_sm_is_claimed(pio0,FUSION_PIO_SM_TX) || pio_sm_is_claimed(pio0,FUSION_PIO_SM_RX) ||
        pio_sm_is_claimed(pio0,FUSION_PIO_SM_EOP) || dma_channel_is_claimed(FUSION_DMA_CHANNEL) ||
        !pio_can_add_program_at_offset(pio0,&footprint,0) ||
        usb_tx_dpdm_program.length+usb_nrzi_decoder_program.length+usb_edge_detector_program.length>32 ||
        hardware_alarm_is_claimed(FUSION_ALARM_NUM)) {
        fail(HOST_ERROR_RESOURCE); publish(); return false;
    }
    frame_pool=alarm_pool_create(FUSION_ALARM_NUM,2);
    if (!frame_pool || alarm_pool_core_num(frame_pool)!=1) {
        fail(HOST_ERROR_ALARM); publish(); return false;
    }
    status.alarm_core=alarm_pool_core_num(frame_pool);
    pio_usb_configuration_t cfg=PIO_USB_DEFAULT_CONFIG;
    cfg.pin_dp=FUSION_HOST_DP_PIN;
    cfg.pinout=PIO_USB_PINOUT_DPDM;
    cfg.pio_tx_num=FUSION_PIO_INDEX; cfg.pio_rx_num=FUSION_PIO_INDEX;
    cfg.sm_tx=FUSION_PIO_SM_TX; cfg.sm_rx=FUSION_PIO_SM_RX; cfg.sm_eop=FUSION_PIO_SM_EOP;
    cfg.tx_ch=FUSION_DMA_CHANNEL;
    cfg.debug_pin_rx=PIO_USB_DEBUG_PIN_NONE; cfg.debug_pin_eop=PIO_USB_DEBUG_PIN_NONE;
    cfg.skip_alarm_pool=true; cfg.alarm_pool=frame_pool;
    tuh_hid_set_default_protocol(HID_PROTOCOL_REPORT);
    if (!tuh_configure(FUSION_HOST_RHPORT,TUH_CFGID_RPI_PIO_USB_CONFIGURATION,&cfg)) {
        fail(HOST_ERROR_CONFIGURE); publish(); return false;
    }
    // Current HCD invokes pio_usb_host_init. Do not call it separately here.
    if (!tuh_init(FUSION_HOST_RHPORT)) { fail(HOST_ERROR_INIT); publish(); return false; }
    status.initialized=1;
    const uint32_t pulls=PADS_BANK0_GPIO0_PUE_BITS | PADS_BANK0_GPIO0_PDE_BITS;
    if (((pads_bank0_hw->io[FUSION_HOST_DP_PIN] | pads_bank0_hw->io[FUSION_HOST_DM_PIN]) & pulls) ||
        !pio_sm_is_claimed(pio0,FUSION_PIO_SM_TX) || !pio_sm_is_claimed(pio0,FUSION_PIO_SM_RX) ||
        !pio_sm_is_claimed(pio0,FUSION_PIO_SM_EOP) || !dma_channel_is_claimed(FUSION_DMA_CHANNEL)) {
        fail(HOST_ERROR_PULL); publish(); return false;
    }
    if (!alarm_pool_add_repeating_timer_us(frame_pool,-FUSION_HOST_FRAME_US,frame_callback,NULL,&frame_timer)) {
        fail(HOST_ERROR_ALARM); publish(); return false;
    }
    status.initialization_complete=1; // never granted by tuh_init alone or software rearm
    log_line("HOST INIT core=1 rhport=1 PIO0 SM0/1/2 DMA0 alarm=2 skip_alarm_pool=1 frame_gate=0");
    publish();
    return true;
}
static void set_step(capture_step_t step, uint8_t slot, uint64_t now) {
    capture_step=step; capture_slot=slot;
    control_pending=false; control_deadline=now+FUSION_CONTROL_TIMEOUT_US;
}
static bool parse_config(void) {
    int selected=-1;
    for (unsigned off=0;off<config_length;) {
        if (off+2>config_length) return false;
        const uint8_t *item=config_desc+off;
        const unsigned size=item[0], type=item[1];
        if (size<2 || off+size>config_length) return false;
        if (type==TUSB_DESC_INTERFACE) {
            if (size<9) return false;
            selected=-1;
            for (unsigned i=0;i<FUSION_HOST_INTERFACE_MAX;++i)
                if (interfaces[i].mounted && interfaces[i].number==item[2] && item[3]==0) selected=(int)i;
        } else if (selected>=0 && type==HID_DESC_TYPE_HID) {
            if (size<9 || 6u+3u*item[5]>size) return false;
            for (unsigned i=0;i<item[5];++i) if (item[6+3*i]==HID_DESC_TYPE_REPORT)
                interfaces[selected].declared=(uint16_t)(item[7+3*i] | (uint16_t)item[8+3*i]<<8);
        } else if (selected>=0 && type==TUSB_DESC_ENDPOINT) {
            if (size<7) return false;
            if (item[2]&0x80) {
                interfaces[selected].endpoint=item[2];
                interfaces[selected].packet_bytes=(uint16_t)(item[4] | (uint16_t)item[5]<<8);
                interfaces[selected].interval=item[6];
            }
        }
        off+=size;
    }
    for (unsigned i=0;i<FUSION_HOST_INTERFACE_MAX;++i) if (interfaces[i].mounted) {
        interface_capture_t *entry=&interfaces[i];
        if (!entry->declared || entry->declared>sizeof(entry->descriptor) ||
            !entry->endpoint || !entry->packet_bytes || entry->packet_bytes>FUSION_CAPTURE_REPORT_BYTES) return false;
        log_interface("HID_CONFIG",entry);
    }
    return true;
}
static void next_interface(uint64_t now) {
    unsigned next=capture_slot+1;
    while (next<FUSION_HOST_INTERFACE_MAX && !interfaces[next].mounted) ++next;
    if (next<FUSION_HOST_INTERFACE_MAX) set_step(CAP_REPORT,(uint8_t)next,now);
    else {
        capture_step=CAP_IDLE; enum_deadline=0;
        hid_interface_candidate_t candidates[FUSION_HOST_INTERFACE_MAX];
        for (unsigned i=0;i<FUSION_HOST_INTERFACE_MAX;++i) candidates[i]=(hid_interface_candidate_t){
            .layout=interfaces[i].capture_ready ? &interfaces[i].layout : NULL,
            .present=interfaces[i].mounted,.available=interfaces[i].session.layout!=NULL};
        const hid_selection_t selected=hid_select_interfaces(candidates,FUSION_HOST_INTERFACE_MAX);
        status.supported_interfaces=selected.supported_interfaces;
        status.selected_instance=selected.selected_instance; status.partial=selected.partial;
        status.mouse_applications=selected.mouse_applications;
        status.rejected_mouse_interfaces=selected.rejected_mouse_interfaces;
        status.unknown_interfaces=selected.unknown_interfaces;
        status.phase=status.supported_interfaces ? HOST_CAPTURE_ONLY : HOST_UNSUPPORTED;
        if (status.supported_interfaces && !event_bus_physical_begin(status.epoch)) { fail(HOST_ERROR_SESSION); return; }
        log_line("HOST capture complete; physical FIFO bound to selected epoch; J2 remains LOCKED_OFF");
    }
}
static void capture_complete(interface_capture_t *entry,uint64_t now) {
    entry->capture_ready=true;
    if (entry->layout.ready && !hid_input_session_begin(&entry->session,entry->epoch,entry->address,entry->instance,&entry->layout)) {
        fail(HOST_ERROR_DESCRIPTOR); return;
    }
    log_interface("HID_CAPTURE",entry);
    next_interface(now);
}
static void control_complete(tuh_xfer_t *xfer) {
    if (status.error!=HOST_ERROR_NONE || !control_pending ||
        (uint32_t)xfer->user_data!=control_ticket || control_epoch!=status.epoch ||
        control_generation!=fusion_usbh_generation() || fusion_host_cancel_requested()) return;
    control_pending=false;
    interface_capture_t *entry=&interfaces[capture_slot];
    char line[LOG_RECORD_BYTES];
    (void)snprintf(line,sizeof(line),"CONTROL_DONE epoch=%lu step=%u slot=%u addr=%u result=%u actual=%lu",
        (unsigned long)status.epoch,(unsigned)capture_step,capture_slot,device_address,
        (unsigned)xfer->result,(unsigned long)xfer->actual_len);
    log_line(line);
    if (xfer->result!=XFER_RESULT_SUCCESS) {
        ++status.control_fail;
        entry->protocol_result=xfer->result;
        fail(HOST_ERROR_CONTROL); return;
    }
    ++status.control_ok;
    const uint64_t now=time_us_64();
    switch (capture_step) {
    case CAP_DEVICE:
        if (xfer->actual_len!=sizeof(device_desc) || device_desc[0]!=18 || device_desc[1]!=TUSB_DESC_DEVICE) { fail(HOST_ERROR_DESCRIPTOR); return; }
        set_step(CAP_CONFIG_HEADER,0,now); break;
    case CAP_CONFIG_HEADER:
        if (xfer->actual_len!=9 || config_desc[0]!=9 || config_desc[1]!=TUSB_DESC_CONFIGURATION) { fail(HOST_ERROR_DESCRIPTOR); return; }
        config_length=(uint16_t)(config_desc[2] | (uint16_t)config_desc[3]<<8);
        if (config_length<9 || config_length>sizeof(config_desc)) { fail(HOST_ERROR_DESCRIPTOR); return; }
        set_step(CAP_CONFIG,0,now); break;
    case CAP_CONFIG: {
        if (xfer->actual_len!=config_length || !parse_config()) { fail(HOST_ERROR_DESCRIPTOR); return; }
        unsigned first=0;
        while (first<FUSION_HOST_INTERFACE_MAX && !interfaces[first].mounted) ++first;
        if (first==FUSION_HOST_INTERFACE_MAX) { status.phase=HOST_UNSUPPORTED; capture_step=CAP_IDLE; enum_deadline=0; break; }
        set_step(CAP_REPORT,(uint8_t)first,now); break;
    }
    case CAP_REPORT:
        // Unlike TinyUSB's mount callback, this length is the actual transfer length.
        if (!entry->mounted || entry->epoch!=status.epoch || xfer->actual_len!=entry->declared) { fail(HOST_ERROR_DESCRIPTOR); return; }
        entry->actual=(uint16_t)xfer->actual_len;
        const hid_layout_result_t layout_result=hid_layout_build(entry->descriptor,entry->actual,&entry->layout);
        (void)snprintf(line,sizeof(line),"LAYOUT epoch=%lu inst=%u result=%s parser_error=%ld apps=%u reports=%u fields=%u partial=%u",
            (unsigned long)entry->epoch,entry->instance,hid_layout_result_name(layout_result),
            (long)entry->layout.parser_error,entry->layout.application_count,entry->layout.report_count,
            entry->layout.field_count,entry->layout.partial);
        log_line(line);
        if (!entry->layout.ready) ++status.normalization_unavailable;
        if (entry->layout.ready && entry->subclass==HID_SUBCLASS_BOOT) set_step(CAP_PROTOCOL,capture_slot,now);
        else {
            // Non-Boot HID uses its Report descriptor. Unsupported interfaces stay raw-only.
            if (entry->layout.ready) entry->protocol_result=XFER_RESULT_SUCCESS;
            capture_complete(entry,now);
        }
        break;
    case CAP_PROTOCOL:
        if (xfer->actual_len!=0 || entry->epoch!=status.epoch || !entry->mounted) { fail(HOST_ERROR_CONTROL); return; }
        entry->protocol_result=XFER_RESULT_SUCCESS;
        log_line("HOST SET_PROTOCOL_REPORT explicit completion result=SUCCESS; fixed-layout normalization only");
        capture_complete(entry,now); break;
    default: fail(HOST_ERROR_CONTROL); break;
    }
}
static void submit_control(void) {
    interface_capture_t *entry=&interfaces[capture_slot];
    bool accepted=false;
    ++control_ticket; control_epoch=status.epoch; control_generation=fusion_usbh_generation(); control_pending=true;
    switch (capture_step) {
    case CAP_DEVICE:
        accepted=tuh_descriptor_get_device(device_address,device_desc,sizeof(device_desc),control_complete,control_ticket); break;
    case CAP_CONFIG_HEADER:
        accepted=tuh_descriptor_get_configuration(device_address,0,config_desc,9,control_complete,control_ticket); break;
    case CAP_CONFIG:
        accepted=tuh_descriptor_get_configuration(device_address,0,config_desc,config_length,control_complete,control_ticket); break;
    case CAP_REPORT:
        accepted=tuh_descriptor_get_hid_report(device_address,entry->number,HID_DESC_TYPE_REPORT,0,entry->descriptor,entry->declared,control_complete,control_ticket); break;
    case CAP_PROTOCOL: {
        tusb_control_request_t request={.bmRequestType=0x21,.bRequest=HID_REQ_CONTROL_SET_PROTOCOL,
            .wValue=tu_htole16(HID_PROTOCOL_REPORT),.wIndex=tu_htole16(entry->number),.wLength=0};
        tuh_xfer_t xfer={.daddr=device_address,.ep_addr=0,.setup=&request,.buffer=NULL,
            .complete_cb=control_complete,.user_data=control_ticket};
        accepted=tuh_control_xfer(&xfer); // callback is mandatory; never the blocking API
        break;
    }
    default: break;
    }
    if (!accepted) { control_pending=false; ++status.queue_fail; } // retry once on next tick; fixed deadline
}
static void dump_tick(void) {
    if (fusion_load_u32(&dump_request)) {
        fusion_store_u32(&dump_request,0);
        if (!device_ready || !config_length || capture_step!=CAP_IDLE ||
            (status.phase!=HOST_CAPTURE_ONLY && status.phase!=HOST_UNSUPPORTED)) {
            log_line("DESC NOT_AVAILABLE; no completed real descriptor capture"); return;
        }
        dump_kind=1; dump_slot=0; dump_offset=0; status.dump_active=1;
        log_line("DESC BEGIN; raw chunks; external hash/reassembly required; check log_drop");
    }
    if (!dump_kind) return;
    const uint8_t *bytes=NULL; uint16_t length=0;
    const char *kind="DESC_DEVICE";
    if (dump_kind==1) { bytes=device_desc; length=sizeof(device_desc); }
    else if (dump_kind==2) { kind="DESC_CONFIG"; bytes=config_desc; length=config_length; }
    else if (dump_kind==3) {
        while (dump_slot<FUSION_HOST_INTERFACE_MAX && !interfaces[dump_slot].capture_ready) ++dump_slot;
        if (dump_slot==FUSION_HOST_INTERFACE_MAX) { dump_kind=4; dump_slot=dump_offset=0; return; }
        kind="DESC_REPORT"; bytes=interfaces[dump_slot].descriptor; length=interfaces[dump_slot].actual;
    } else {
        while (dump_slot<FUSION_HOST_INTERFACE_MAX && !interfaces[dump_slot].capture_ready) ++dump_slot;
        if (dump_slot==FUSION_HOST_INTERFACE_MAX) { dump_kind=0; status.dump_active=0; log_line("DESC END; capability=fixed-layout subset; check log_drop; hardware NOT_TESTED"); return; }
        const hid_layout_t *layout=&interfaces[dump_slot].layout;
        char line[LOG_RECORD_BYTES];
        if (!dump_offset) {
            (void)snprintf(line,sizeof(line),"CAPABILITY slot=%lu result=%s parsed=%u apps=%u selected_app=%u reports=%u fields=%u partial=%u selected_input=%u",
                (unsigned long)dump_slot,hid_layout_result_name(layout->result),layout->parsed,layout->application_count,
                layout->selected_application,layout->report_count,layout->field_count,layout->partial,
                dump_slot==status.selected_instance);
        } else if (dump_offset<=layout->application_count) {
            const hid_application_t *app=&layout->applications[dump_offset-1];
            (void)snprintf(line,sizeof(line),"APPLICATION slot=%lu app=%lu collection=%u result=%s buttons=%04lx x=%u y=%u wheel=%u pan=%u",
                (unsigned long)dump_slot,(unsigned long)(dump_offset-1),app->collection_number,
                hid_layout_result_name(app->result),(unsigned long)app->buttons,app->has_x,app->has_y,app->has_wheel,app->has_pan);
        } else if (dump_offset<=layout->application_count+layout->report_count) {
            const hid_report_layout_t *r=&layout->reports[dump_offset-layout->application_count-1];
            (void)snprintf(line,sizeof(line),"REPORT slot=%lu id=%u input_bits=%lu output_bits=%lu feature_bits=%lu",
                (unsigned long)dump_slot,r->id,(unsigned long)r->input_bits,(unsigned long)r->output_bits,(unsigned long)r->feature_bits);
        } else {
            const hid_field_t *f=&layout->fields[dump_offset-layout->application_count-layout->report_count-1];
            (void)snprintf(line,sizeof(line),"FIELD slot=%lu app=%u id=%u type=INPUT page=%04x usage=%04x offset=%u bits=%u count=%u flags=%04x logical_min=%ld logical_max_raw=%08lx",
                (unsigned long)dump_slot,f->application,layout->reports[f->report_index].id,f->usage_page,f->usage,
                f->bit_offset,f->bits,f->report_count,f->flags,(long)f->logical_min,(unsigned long)f->logical_max);
        }
        log_line(line);
        const uint32_t records=(uint32_t)layout->application_count+layout->report_count+layout->field_count;
        if (++dump_offset>records) { ++dump_slot; dump_offset=0; }
        return; // At most one capability/field per tick; no callback lifetime pointers escape.
    }
    log_bytes(kind,dump_slot,0,bytes,length,(uint16_t)dump_offset); // at most 32 raw bytes per tick
    dump_offset+=32;
    if (dump_offset>=length) {
        dump_offset=0;
        if (dump_kind<3) ++dump_kind; else ++dump_slot;
    }
}
bool fusion_host_cancel_requested(void) {
    return quiesce_hold || fusion_load_u32(&commands.request)!=quiesce_seen || fusion_load_u32(&lost_event_notice)!=0;
}
uint32_t fusion_host_time_ms(void) { return (uint32_t)(time_us_64()/1000); }
void fusion_host_sleep_1ms(void) { sleep_us(1000); } // Core1; IRQs and Core0 USB stay live
void fusion_host_event_lost(void) { fusion_store_u32(&lost_event_notice,1); }
static uint32_t take_notice(uint32_t *notice) {
    const uint32_t irq=save_and_disable_interrupts();
    const uint32_t value=fusion_load_u32(notice); fusion_store_u32(notice,0);
    restore_interrupts(irq); return value;
}
void fusion_host_connection_event(bool attached) {
    invalidate_capture(); device_address=0;
    if (++status.epoch==0) ++status.epoch;
    (void)take_notice(&detach_notice);
    if (attached) {
        session_generation=fusion_usbh_generation();
        status.phase=HOST_ENUMERATING; enum_deadline=time_us_64()+FUSION_ENUM_TIMEOUT_US;
    } else {
        ++status.detach_count; enum_deadline=0;
        if (status.error==HOST_ERROR_NONE) status.phase=HOST_NO_MOUSE;
    }
}
static bool clear_pio_session(void) {
    pio_port_t *pp=&pio_port[0];
    // No DMA abort wait. An earlier timer IRQ has returned on this same core.
    if (dma_channel_is_busy(pp->tx_ch)) return false;
    if (!host_pio_clear_session(&pio_usb_root_port[0],pio_usb_ep_pool,pio_usb_device)) return false;
    pio_sm_set_enabled(pp->pio_usb_tx,pp->sm_tx,false);
    pio_sm_set_enabled(pp->pio_usb_rx,pp->sm_rx,false);
    pio_sm_set_enabled(pp->pio_usb_rx,pp->sm_eop,false);
    pio_sm_clear_fifos(pp->pio_usb_tx,pp->sm_tx); pio_sm_restart(pp->pio_usb_tx,pp->sm_tx);
    pio_sm_exec(pp->pio_usb_tx,pp->sm_tx,pp->tx_reset_instr);
    pio_sm_set_consecutive_pindirs(pp->pio_usb_tx,pp->sm_tx,FUSION_HOST_DP_PIN,2,false);
    pio_sm_clear_fifos(pp->pio_usb_rx,pp->sm_rx); pio_sm_restart(pp->pio_usb_rx,pp->sm_rx);
    pio_sm_exec(pp->pio_usb_rx,pp->sm_rx,pp->rx_reset_instr);
    pio_sm_exec(pp->pio_usb_rx,pp->sm_rx,pp->rx_reset_instr2);
    pio_sm_clear_fifos(pp->pio_usb_rx,pp->sm_eop); pio_sm_restart(pp->pio_usb_rx,pp->sm_eop);
    pio_sm_exec(pp->pio_usb_rx,pp->sm_eop,pio_encode_jmp(pp->offset_eop));
    pp->pio_usb_tx->irq=IRQ_TX_ALL_MASK; pp->pio_usb_rx->irq=IRQ_RX_ALL_MASK;
    // Cancellation can leave a port halfway through the 50ms reset.
    for (unsigned pin=FUSION_HOST_DP_PIN;pin<=FUSION_HOST_DM_PIN;++pin) {
        gpio_set_oeover(pin,GPIO_OVERRIDE_NORMAL); gpio_set_outover(pin,GPIO_OVERRIDE_NORMAL);
    }
    pp->need_pre=false; memset(pp->usb_rx_buffer,0,sizeof(pp->usb_rx_buffer));
    // pp->low_speed/program/clock metadata stays consistent. Next frame configures the actual new speed.
    return true;
}
static bool quiesce_tick(uint64_t now_us) {
    const uint32_t request=fusion_load_u32(&commands.request);
    if (request && request!=quiesce_seen) {
        quiesce_seen=request; quiesce_started=now_us;
        quiesce_hold=true;
        status.cleanup_reason=request&7u;
        // Main and the frame timer are both Core1-owned. Once this critical
        // section runs, an earlier frame IRQ has returned and no new frame starts.
        const uint32_t irq=save_and_disable_interrupts();
        fusion_store_u32(&frame_gate,0);
        restore_interrupts(irq);
        invalidate_capture(); enum_deadline=0;
        if (++status.epoch==0) ++status.epoch;
        quiesce_pending=true; status.cleanup_status=1;
        status.phase=HOST_SAFE_OFF;
    }
    if (quiesce_pending && status.initialized && clear_pio_session() && fusion_usbh_quiesce(FUSION_HOST_RHPORT)) {
        quiesce_pending=false; device_address=0;
        (void)take_notice(&detach_notice); (void)take_notice(&lost_event_notice);
        status.cleanup_status=2;
        (void)host_commands_ack_quiesce(&commands,quiesce_seen);
        log_line("HOST cleanup complete: PIO root/endpoints, USBH address0/control/class/queue cleared; claims retained");
    }
    if (quiesce_pending && now_us-quiesce_started>=100000) {
        quiesce_pending=false; status.cleanup_status=3; status.error=HOST_ERROR_CLEANUP_UNVERIFIED;
        status.phase=HOST_FAULT_OFF;
        log_line("HOST cleanup deadline: SAFE_OFF; no ACK; resources/init not repeated");
    }
    const uint32_t rearm=fusion_load_u32(&commands.rearm_request);
    if (quiesce_hold && !quiesce_pending && status.cleanup_status==2 && rearm && rearm==quiesce_seen && rearm!=rearm_seen &&
        fusion_load_u32(&commands.request)==rearm && fusion_usbh_rearm(FUSION_HOST_RHPORT)) {
        rearm_seen=rearm; status.error=HOST_ERROR_NONE; status.phase=HOST_NO_MOUSE;
        // Frame remains gated. These SMs do not send packets until frame/transfer submission.
        pio_sm_set_enabled(pio_port[0].pio_usb_tx,pio_port[0].sm_tx,true);
        pio_sm_set_enabled(pio_port[0].pio_usb_rx,pio_port[0].sm_eop,true);
        if (host_commands_ack_rearm(&commands,rearm)) quiesce_hold=false;
    }
    const bool run=!FUSION_TASK1_J2_LOCKED && !quiesce_hold && host_commands_run(&commands,rearm_seen);
    fusion_store_u32(&frame_gate,run ? 1u : 0u);
    return quiesce_hold;
}
void host_port_tick(uint64_t now_us) {
    if (get_core_num()!=1) return;
    if (quiesce_tick(now_us)) { publish(); return; }
    if (!status.initialized) return;
    if (take_notice(&lost_event_notice)) { ++status.queue_fail; fail(HOST_ERROR_EVENT_QUEUE); publish(); return; }
    if (take_notice(&detach_notice)) { invalidate_capture(); enum_deadline=0; publish(); }
    if ((enum_deadline && now_us>=enum_deadline) || (capture_step!=CAP_IDLE && now_us>=control_deadline)) {
        fail(HOST_ERROR_TIMEOUT); publish(); return; // never pump stale attach after closing the frame gate
    }
    tuh_task_ext(0,false); // Core1 only; never waits for the queue
    if (status.error!=HOST_ERROR_NONE || fusion_host_cancel_requested()) { publish(); return; }
    if (capture_step!=CAP_IDLE && !control_pending) submit_control();
    for (unsigned i=0;i<FUSION_HOST_INTERFACE_MAX;++i) {
        interface_capture_t *entry=&interfaces[i];
        if (entry->mounted && entry->capture_ready && !entry->receive_pending) {
            entry->receive_epoch=entry->epoch;
            entry->receive_pending=tuh_hid_receive_report(entry->address,entry->instance);
            if (!entry->receive_pending) ++status.queue_fail; // no callback spin loop
        }
    }
    dump_tick(); publish();
}
void tuh_event_hook_cb(uint8_t rhport, uint32_t eventid, bool in_isr) {
    (void)in_isr;
    if (rhport==FUSION_HOST_RHPORT && eventid==HCD_EVENT_DEVICE_REMOVE) fusion_store_u32(&detach_notice,1);
}
void tuh_hid_mount_cb(uint8_t address, uint8_t instance, const uint8_t *report, uint16_t length) {
    (void)report; (void)length; // the vendor callback length is not proof of actual bytes
    if (status.error!=HOST_ERROR_NONE || fusion_host_cancel_requested() || session_generation!=fusion_usbh_generation()) return;
    if (instance>=FUSION_HOST_INTERFACE_MAX) { fail(HOST_ERROR_DESCRIPTOR); return; }
    tuh_itf_info_t info;
    if (!tuh_hid_itf_get_info(address,instance,&info)) { fail(HOST_ERROR_DESCRIPTOR); return; }
    interface_capture_t *entry=&interfaces[instance];
    // Layout is large: clear in place instead of constructing it on the 4KiB Core1 stack.
    memset(entry,0,sizeof(*entry));
    entry->mounted=true; entry->address=address; entry->instance=instance;
    entry->number=info.desc.bInterfaceNumber; entry->subclass=info.desc.bInterfaceSubClass;
    entry->protocol=info.desc.bInterfaceProtocol; entry->epoch=status.epoch;
    entry->protocol_result=XFER_RESULT_INVALID;
    entry->host_generation=session_generation;
    ++status.interfaces;
    log_interface("HID_MOUNT",entry);
}
void tuh_mount_cb(uint8_t address) {
    if (status.error!=HOST_ERROR_NONE || fusion_host_cancel_requested() || session_generation!=fusion_usbh_generation()) return;
    device_address=address; device_ready=true;
    set_step(CAP_DEVICE,0,time_us_64()); // after TinyUSB finishes all class configuration
}
void tuh_hid_umount_cb(uint8_t address, uint8_t instance) {
    if (instance<FUSION_HOST_INTERFACE_MAX && interfaces[instance].address==address) {
        if (instance==status.selected_instance) source_invalidate(SOURCE_PHYSICAL,status.epoch);
        interfaces[instance].mounted=false;
        interfaces[instance].receive_pending=false;
        hid_input_session_end(&interfaces[instance].session);
    }
}
void tuh_umount_cb(uint8_t address) {
    (void)address;
    invalidate_capture(); enum_deadline=0; device_address=0;
    if (++status.epoch==0) ++status.epoch;
    if (status.error==HOST_ERROR_NONE) status.phase=HOST_NO_MOUSE;
    log_line("HOST DETACH layout/session and physical mailbox invalidated; ordinary attach reuses controller");
}
void tuh_hid_report_received_cb(uint8_t address, uint8_t instance, const uint8_t *report, uint16_t length) {
    if (status.error!=HOST_ERROR_NONE || fusion_host_cancel_requested() || instance>=FUSION_HOST_INTERFACE_MAX) return;
    interface_capture_t *entry=&interfaces[instance];
    if (!entry->mounted || !entry->capture_ready || entry->address!=address || entry->epoch!=status.epoch ||
        !entry->receive_pending || entry->receive_epoch!=status.epoch ||
        entry->host_generation!=fusion_usbh_generation()) { ++status.stale_rx; return; }
    entry->receive_pending=false;
    if (!report || !length || length>FUSION_CAPTURE_REPORT_BYTES) { ++status.invalid_rx; return; }
    ++status.raw_rx; ++entry->sequence;
    mouse_delta_t normalized={.sequence=entry->sequence,.received_us=time_us_64()};
    const hid_input_result_t result=instance==status.selected_instance ?
        hid_input_accept_report(&entry->session,status.epoch,report,length,&normalized) : HID_INPUT_IGNORED;
    if (result==HID_INPUT_VALID) { ++status.normalized_rx; if (!physical_offer(&normalized)) ++status.forward_drop; }
    else if (result==HID_INPUT_IGNORED) ++status.ignored_rx;
    else if (result==HID_INPUT_STALE) ++status.stale_rx;
    else ++status.invalid_rx;
    if (fusion_load_u32(&trace_enabled)) {
        log_bytes("RX_RAW",instance,entry->sequence,report,length,0);
        if (length>32) log_bytes("RX_RAW",instance,entry->sequence,report,length,32);
        if (result==HID_INPUT_VALID) {
            char line[LOG_RECORD_BYTES];
            (void)snprintf(line,sizeof(line),"NORMALIZED epoch=%lu slot=%u seq=%lu received_us=%llu app=%u buttons=%04lx valid=%04lx dx=%ld dy=%ld wheel=%ld pan=%ld",
                (unsigned long)normalized.epoch,instance,(unsigned long)normalized.sequence,
                (unsigned long long)normalized.received_us,entry->layout.selected_application,
                (unsigned long)normalized.buttons,(unsigned long)normalized.buttons_valid,(long)normalized.dx,
                (long)normalized.dy,(long)normalized.wheel,(long)normalized.pan);
            log_line(line);
        }
    }
    // IN resubmission happens once on the next Core1 tick, not in a callback wait loop.
}
bool host_port_snapshot(host_port_status_t *out) {
    uint32_t words[sizeof(status)/sizeof(uint32_t)];
    bool coherent=false;
    for (unsigned attempt=0;attempt<2;++attempt) {
        uint32_t before=fusion_load_u32(&published_version);
        for (unsigned i=0;i<sizeof(words)/sizeof(words[0]);++i) words[i]=fusion_load_u32(&published[i]);
        uint32_t after=fusion_load_u32(&published_version);
        if (!(before&1) && before==after) { coherent=true; break; }
    }
    memcpy(out,words,sizeof(words)); return coherent;
}
bool host_port_pop_log(log_record_t *record) { return log_queue_pop(&logs,record); }
uint32_t host_port_log_dropped(void) { return log_queue_dropped(&logs); }
void host_port_request_dump(void) { fusion_store_u32(&dump_request,1); }
void host_port_set_trace(bool enabled) { fusion_store_u32(&trace_enabled,enabled ? 1 : 0); }
void host_port_request_quiesce(host_quiesce_reason_t reason) {
    if (reason<HOST_QUIESCE_TARGET || reason>HOST_QUIESCE_USER) return;
    host_commands_quiesce(&commands,(uint32_t)reason);
}
bool host_port_quiesced(void) {
    return host_commands_quiesced(&commands);
}
bool host_port_request_rearm(void) {
    return host_commands_rearm(&commands);
}
bool host_port_armed(void) { return host_commands_armed(&commands); }
void host_port_set_frames(bool run) { host_commands_frames(&commands,run && !FUSION_TASK1_J2_LOCKED); }
const char *host_port_phase_name(uint32_t phase) {
    static const char *const names[]={"SAFE_OFF","INIT_FAILED","ENUMERATING","CAPTURE_ONLY","NO_MOUSE","UNSUPPORTED","FAULT_OFF"};
    return phase<sizeof(names)/sizeof(names[0]) ? names[phase] : "UNKNOWN";
}
