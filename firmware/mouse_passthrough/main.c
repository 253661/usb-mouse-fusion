#include "pico/stdlib.h"
#include "pico/multicore.h"
#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/watchdog.h"
#include "tusb.h"
#include "device/dcd.h"
#include "atomic_u32.h"
#include "board_io.h"
#include "host_port.h"
#include "hid_output.h"
#if FUSION_TRAJECTORY
#include "j4_preset.h"
#include "preset_flash_backend.h"
#endif
#include "passthrough_core.h"
#include "bringup_control.h"
#include "diagnostics.h"
#include "project_config.h"
#if FUSION_J4_LINK
#include "j4_uart.h"
#endif
static uint32_t target_reset_notice;
static bringup_control_t control;
static bool system_clock_ok;
static host_port_status_t last_host;
#if FUSION_PERSISTENCE
static preset_store_t persistent_store;
static preset_flash_backend_t boot_flash,maintenance_flash;
static preset_persistence_t persistence;
static uint32_t identified_flash_bytes;
static uint64_t save_started_us,quiesce_started_us;
uint32_t const fusion_persistence_contract[]={0x46534732,1,0x20000,0x31,0x32,1,1,0,4096};
#endif
// Values emitted by the compiler are inspected from the linked ELF, not inferred from source strings.
uint32_t const fusion_build_contract[]={
    0x4d465431,CFG_TUD_ENABLED,CFG_TUH_ENABLED,CFG_TUSB_RHPORT0_MODE,CFG_TUSB_RHPORT1_MODE,
    CFG_TUD_RPI_PIO_USB,CFG_TUH_RPI_PIO_USB,CFG_TUH_DEVICE_MAX,CFG_TUH_HUB,CFG_TUH_HID,
    CFG_TUH_ENUMERATION_BUFSIZE,CFG_TUH_HID_EPIN_BUFSIZE,CFG_TUD_CDC,CFG_TUD_HID,
    FUSION_HOST_DP_PIN,FUSION_HOST_DM_PIN,FUSION_HOST_EN_PIN,FUSION_HOST_FAULT_N_PIN,
    FUSION_TARGET_VBUS_PRESENT_PIN,FUSION_HOST_DATA_OE_N_PIN,FUSION_PIO_INDEX,
    FUSION_PIO_SM_TX,FUSION_PIO_SM_RX,FUSION_PIO_SM_EOP,FUSION_DMA_CHANNEL,1,
    FUSION_SYS_HZ,FUSION_USB_HZ,FUSION_TASK1_J2_LOCKED,8,16
};
uint32_t const fusion_bringup_contract[]={0x4a324336,FUSION_STANDALONE_FLASH ? 2 : FUSION_CONTROLLED_J2,
    BRINGUP_MODE_OFF,100000,FUSION_STANDALONE_FLASH ? 0 : 1,FUSION_STANDALONE_FLASH ? 0 : 1,1,1};
#if FUSION_STANDALONE_FLASH
uint32_t const fusion_flash_contract[]={0x46485331,1,PICO_NO_FLASH,PICO_COPY_TO_RAM,
    PICO_FLASH_SIZE_BYTES,PICO_FLASH_SPI_CLKDIV,PICO_BOOT_STAGE2_CHOOSE_GENERIC_03H,FUSION_USB_MAX_POWER_MA};
#endif
_Static_assert(FUSION_TASK1_J2_LOCKED || (FUSION_CONTROLLED_J2 && FUSION_DIAGNOSTIC_CDC) ||
    (FUSION_STANDALONE_FLASH && !FUSION_DIAGNOSTIC_CDC),"J2 requires a reviewed controlled or standalone profile");
_Static_assert(FUSION_HOST_DM_PIN==FUSION_HOST_DP_PIN+1,"Current adjacent DP/DM mapping required");
static void core1_main(void) {
    (void)host_port_init_core1();
    while (true) { host_port_tick(time_us_64()); tight_loop_contents(); }
}
void tud_event_hook_cb(uint8_t rhport,uint32_t eventid,bool in_isr) {
    (void)in_isr;
    if (rhport==FUSION_DEVICE_RHPORT && (eventid==DCD_EVENT_BUS_RESET || eventid==DCD_EVENT_UNPLUGGED))
        fusion_store_u32(&target_reset_notice,1);
}
static void target_changed(void) {
    board_io_force_safe(); host_port_request_quiesce(HOST_QUIESCE_TARGET);
    hid_output_target_reset(); bringup_control_target_reset(&control,time_us_64());
}
void hid_output_request_recovery(void) {
    // hid_output already cleared the unknown ledger. Finish the producer-side transition.
    board_io_force_safe(); host_port_request_quiesce(HOST_QUIESCE_TARGET);
    bringup_control_target_reset(&control,time_us_64());
}
void tud_mount_cb(void) { target_changed(); }
void tud_umount_cb(void) { target_changed(); }
void tud_suspend_cb(bool remote_wakeup_enabled) {
    (void)remote_wakeup_enabled;
    target_changed();
}
void tud_resume_cb(void) { target_changed(); }
static bool service_target_notice(void) {
    // Caller masks only Core0 IRQs for this bounded notice/submit section.
    if (fusion_load_u32(&target_reset_notice)) {
        fusion_store_u32(&target_reset_notice,0); target_changed(); return true;
    }
    return false;
}
static bringup_inputs_t control_inputs(void) {
    const board_io_status_t board=board_io_snapshot();
    host_port_status_t current;
    const bool coherent=host_port_snapshot(&current);
    if(coherent) last_host=current; // a busy publication does not erase known init/error state
    return (bringup_inputs_t){.host={.target=board.target_vbus && tud_mounted() && !tud_suspended(),
        .fault_n=board.fault_n!=0,.quiesced=host_port_quiesced(),.host_ready=last_host.phase==HOST_CAPTURE_ONLY,
        .host_failed=last_host.error!=HOST_ERROR_NONE,.locked=FUSION_TASK1_J2_LOCKED!=0,.armed=host_port_armed(),
        .host_waiting=last_host.phase==HOST_NO_MOUSE},
        .clock_ok=system_clock_ok,.host_initialized=host_port_initialization_complete(&last_host),.host_coherent=coherent};
}
bringup_result_t fusion_bringup_execute(bringup_command_t command,uint64_t now_us) {
    const uint32_t irq=save_and_disable_interrupts();
    (void)service_target_notice();
    const bringup_result_t result=bringup_control_request(&control,command,control_inputs(),now_us);
    if(command==BRINGUP_OFF) {
        board_io_force_safe(); host_port_set_frames(false); hid_output_target_reset();
        host_port_request_quiesce(HOST_QUIESCE_USER);
    }
    restore_interrupts(irq); return result;
}
bringup_mode_t fusion_bringup_mode(void) { return control.mode; }
#if FUSION_PERSISTENCE
static void persistence_boot(void){
    preset_store_io_t io;
    identified_flash_bytes=preset_flash_pico_capacity();
    if(!preset_flash_pico_boot_init(&boot_flash,identified_flash_bytes,&io))return;
    preset_store_init(&persistent_store,&io);
    uint8_t result=preset_store_restore_boot(&persistent_store,j4_preset_runtime());
    preset_persistence_init(&persistence,&persistent_store,j4_preset_runtime(),result);
    j4_preset_attach_persistence(&persistence);
}
static void __attribute__((noinline,noclone)) persistence_maintenance(uint64_t now){
    if(persistence.phase!=PRESET_PERSIST_PENDING)return;
    if(!save_started_us)save_started_us=now;
    bool drained=j4_uart_tx_idle();
    if(!quiesce_started_us&&drained){
        quiesce_started_us=now;(void)fusion_bringup_execute(BRINGUP_OFF,now);
    }
    const bool host_safe=quiesce_started_us&&host_port_quiesced();
    if(!host_safe&&now-save_started_us<500000)return;
    preset_save_guard_t guard={.reply_drained=drained,.host_quiesced=host_safe};
    if(host_safe&&drained){
        multicore_reset_core1();guard.core1_parked=true;
        guard.usb_off=tud_deinit(FUSION_DEVICE_RHPORT)&&!tud_inited();
        guard.dma_idle=true;
        for(unsigned i=0;i<NUM_DMA_CHANNELS;i++)if(dma_channel_is_busy(i))guard.dma_idle=false;
        preset_store_io_t io;
        if(guard.usb_off&&guard.dma_idle&&preset_flash_pico_maintenance_init(&maintenance_flash,identified_flash_bytes,&io,true)){
            persistent_store.io=io;(void)preset_persistence_execute(&persistence,&guard);
        }
    }
    // Failed shutdown or failed transaction is never retried. Reboot restores the last valid bank.
    preset_flash_pico_seal();watchdog_reboot(0,0,1);
    while(true)tight_loop_contents();
}
#endif
int main(void) {
    board_io_init_safe(); // before clocks, either USB role, or launching Core1
    bool clock_ok=set_sys_clock_khz(FUSION_SYS_HZ/1000,false) &&
        clock_get_hz(clk_sys)==FUSION_SYS_HZ && clock_get_hz(clk_usb)==FUSION_USB_HZ;
    diagnostics_set_clock_status(clock_ok);
    system_clock_ok=clock_ok;
    bridge_init(); // initialize SPSC/mailbox BEFORE launching the producer core
#if FUSION_TRAJECTORY
    j4_preset_init(); // Core0-owned; independent of J4 transport lifetime
#if FUSION_PERSISTENCE
    persistence_boot();
#endif
    // Original backend is irreversibly sealed before normal peripheral/USB/Host startup.
    preset_flash_pico_seal();
#endif
    bringup_control_init(&control,(FUSION_CONTROLLED_J2 || FUSION_STANDALONE_FLASH)!=0);
    host_port_prepare();
    hid_output_target_reset();
    // J1 is independent of Core1 success; J4 is explicit and has no reset/J2 commands.
    bool device_ok=tud_init(FUSION_DEVICE_RHPORT);
#if FUSION_J4_LINK
    if(clock_ok&&device_ok)j4_uart_init();
#endif
    if (clock_ok) multicore_launch_core1(core1_main);
    while (true) {
        if(FUSION_TASK1_J2_LOCKED || !clock_ok) board_io_force_safe();
        if (device_ok) {
            tud_task_ext(0,false);
            const uint32_t notice_irq=save_and_disable_interrupts();
            service_target_notice(); restore_interrupts(notice_irq);
            const bringup_inputs_t inputs=control_inputs();
            const lifecycle_actions_t actions=bringup_control_step(&control,inputs,time_us_64());
            if (actions.reset_target) hid_output_target_reset();
            if (actions.quiesce) {
                board_io_force_safe(); hid_output_target_reset();
                host_port_request_quiesce(inputs.host.fault_n ? HOST_QUIESCE_TARGET : HOST_QUIESCE_FAULT);
            }
            if (actions.rearm) (void)host_port_request_rearm(); // software session only
            const uint32_t apply_irq=save_and_disable_interrupts();
            const bool reset=service_target_notice(); // a new reset retires any cached enable action
            const board_io_status_t fresh=board_io_snapshot();
            const bool target_active=fresh.target_vbus && tud_mounted() && !tud_suspended();
            if(!reset && control.mode!=BRINGUP_MODE_OFF && (!clock_ok || !target_active || !fresh.fault_n)) {
                bringup_inputs_t invalid=inputs;
                invalid.clock_ok=clock_ok; invalid.host.target=target_active; invalid.host.fault_n=fresh.fault_n!=0;
                const lifecycle_actions_t closing=bringup_control_step(&control,invalid,time_us_64());
                if(closing.reset_target || closing.quiesce) {
                    hid_output_target_reset();
                    host_port_request_quiesce(fresh.fault_n ? HOST_QUIESCE_TARGET : HOST_QUIESCE_FAULT);
                }
            }
            if(reset || control.mode==BRINGUP_MODE_OFF || !clock_ok || !target_active || !fresh.fault_n) {
                board_io_force_safe(); host_port_set_frames(false);
            } else {
                const board_io_apply_result_t applied=board_io_apply(actions.en,actions.oe_n);
                if(applied!=BOARD_IO_APPLIED) {
                    // The boundary already observed unsafe input; preserve it even
                    // if the physical level recovers before the next policy sample.
                    const lifecycle_actions_t closing=bringup_control_gpio_result(&control,inputs,applied,time_us_64());
                    host_port_set_frames(closing.frame);
                    hid_output_target_reset();
                    host_port_request_quiesce(applied & BOARD_IO_REJECTED_FAULT ? HOST_QUIESCE_FAULT : HOST_QUIESCE_TARGET);
                } else host_port_set_frames(actions.frame);
            }
            restore_interrupts(apply_irq);
#if FUSION_TRAJECTORY
            j4_preset_tick(time_us_64(),fresh.target_vbus&&tud_mounted()&&!tud_suspended()&&fresh.fault_n);
#endif
            bridge_poll(time_us_64(),BRIDGE_POLL_MAX_EVENTS);
            const uint32_t submit_irq=save_and_disable_interrupts();
            service_target_notice(); // reset arriving after poll cannot submit cached motion
            hid_output_tick();
            restore_interrupts(submit_irq);
            diagnostics_tick(time_us_64());
#if FUSION_J4_LINK
            if(clock_ok){
#if FUSION_J4_AUX
                bridge_ai_set_ready(fresh.target_vbus&&tud_mounted()&&!tud_suspended());
#endif
                mouse_report_t current;bridge_get_report(&current);
                const j4_snapshot_t snapshot={
                    .flags=(fresh.target_vbus&&tud_mounted()&&!tud_suspended()?1u:0u)|
                        (fresh.en_sense?2u:0u)|(!fresh.oe_n_sense?4u:0u)|(last_host.phase==HOST_CAPTURE_ONLY?8u:0u),
                    .host_phase=(uint8_t)last_host.phase,.host_error=(uint8_t)last_host.error,.buttons=current.buttons};
                j4_uart_tick(time_us_64(),&snapshot); // HID/physical service above always runs first
#if FUSION_PERSISTENCE
                persistence_maintenance(time_us_64());
#endif
            }
#endif
        }
        tight_loop_contents();
    }
}
