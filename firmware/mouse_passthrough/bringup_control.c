#include <string.h>
#include "bringup_control.h"
#include "project_config.h"
enum { POWER_ONLY_SETTLE_US=100000 };
void bringup_control_init(bringup_control_t *c,bool controlled) {
    memset(c,0,sizeof(*c)); c->controlled=controlled;
}
void bringup_control_target_reset(bringup_control_t *c,uint64_t now) {
    c->mode=BRINGUP_MODE_OFF; lifecycle_target_reset(&c->lifecycle,now);
}
bringup_result_t bringup_control_request(bringup_control_t *c,bringup_command_t command,bringup_inputs_t in,uint64_t now) {
    if(command==BRINGUP_OFF) { bringup_control_target_reset(c,now); return BRINGUP_OK; }
    if(!c->controlled || in.host.locked) return BRINGUP_LOCKED;
    if(!in.clock_ok || !in.host_initialized || !in.host_coherent || !in.host.target ||
       !in.host.fault_n || !in.host.quiesced) return BRINGUP_INTERLOCK;
    if(command==BRINGUP_RECOVER) {
        c->lifecycle.recovery_eligible=true;
        if(!lifecycle_request_recovery(&c->lifecycle)) return BRINGUP_RECOVERY_UNAVAILABLE;
        c->mode=BRINGUP_MODE_OFF; return BRINGUP_OK; // recovery never grants power/data permission
    }
    if(c->lifecycle.fault_latched || c->lifecycle.phase==LIFE_FAULT_OFF) return BRINGUP_FAULT_LATCHED;
    if(c->lifecycle.phase!=LIFE_WAIT_TARGET) return BRINGUP_INTERLOCK;
    if(command==BRINGUP_POWER) {
        if(c->mode==BRINGUP_MODE_ARMED) return BRINGUP_INTERLOCK;
        if(c->mode!=BRINGUP_MODE_POWER) c->power_started_us=now;
        c->mode=BRINGUP_MODE_POWER; return BRINGUP_OK;
    }
    if(command==BRINGUP_ARM) {
        if(c->mode!=BRINGUP_MODE_POWER) return BRINGUP_POWER_REQUIRED;
        if(now-c->power_started_us<POWER_ONLY_SETTLE_US) return BRINGUP_SETTLE_REQUIRED;
        c->mode=BRINGUP_MODE_ARMED; return BRINGUP_OK;
    }
    return BRINGUP_INTERLOCK;
}
lifecycle_actions_t bringup_control_step(bringup_control_t *c,bringup_inputs_t in,uint64_t now) {
    if(!in.clock_ok) in.host.target=false;
#if FUSION_STANDALONE_FLASH
    // Reuse the same interlocks, nonblocking settle, cleanup ticket and rearm ACK.
    // Fault-off is deliberately excluded: electrical faults need a cold restart.
    if(c->lifecycle.phase==LIFE_WAIT_TARGET) {
        if(c->mode==BRINGUP_MODE_OFF) (void)bringup_control_request(c,BRINGUP_POWER,in,now);
        else if(c->mode==BRINGUP_MODE_POWER) (void)bringup_control_request(c,BRINGUP_ARM,in,now);
    }
#endif
    if(!in.host.target || !in.host.fault_n || in.host.locked) c->mode=BRINGUP_MODE_OFF;
    // Keep the existing one-shot recovery eligible while FAULT_OFF. It returns to
    // WAIT_TARGET with mode OFF; every following step remains manually inhibited.
    in.host.locked=in.host.locked || !c->controlled ||
        (c->mode!=BRINGUP_MODE_ARMED && c->lifecycle.phase!=LIFE_FAULT_OFF);
    lifecycle_actions_t out=lifecycle_step(&c->lifecycle,in.host,now);
    if(c->lifecycle.phase==LIFE_FAULT_OFF) c->mode=BRINGUP_MODE_OFF;
    if(c->controlled && c->mode==BRINGUP_MODE_POWER && c->lifecycle.phase==LIFE_WAIT_TARGET &&
       !c->lifecycle.fault_latched && in.clock_ok && in.host_initialized && in.host.target &&
       in.host.fault_n && in.host.quiesced) out.en=true;
    return out;
}
lifecycle_actions_t bringup_control_gpio_result(bringup_control_t *c,bringup_inputs_t in,board_io_apply_result_t result,uint64_t now) {
    if(result==BOARD_IO_APPLIED) return (lifecycle_actions_t){.oe_n=true};
    if(result & BOARD_IO_REJECTED_FAULT) in.host.fault_n=false;
    if(result & BOARD_IO_REJECTED_TARGET) in.host.target=false;
    if(result & BOARD_IO_REJECTED_LOCKED) in.host.locked=true;
    c->mode=BRINGUP_MODE_OFF;
    lifecycle_actions_t closing=bringup_control_step(c,in,now);
    closing.en=false; closing.oe_n=true; closing.frame=false; closing.rearm=false;
    closing.quiesce=true; closing.reset_target=true;
    return closing;
}
const char *bringup_result_name(bringup_result_t result) {
    static const char *const names[]={"OK","J2_LOCKED","INTERLOCK","FAULT_LATCHED","POWER_REQUIRED","SETTLE_REQUIRED","RECOVERY_UNAVAILABLE"};
    return (unsigned)result<sizeof(names)/sizeof(names[0]) ? names[result] : "UNKNOWN";
}
