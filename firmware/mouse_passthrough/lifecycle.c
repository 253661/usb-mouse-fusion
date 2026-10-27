#include <string.h>
#include "lifecycle.h"
enum { SETTLE_US=100000, CLEANUP_US=100000, ENUM_US=5000000 };
uint32_t const fusion_lifecycle_contract[]={0x4c494634,SETTLE_US,CLEANUP_US,ENUM_US,1};
void lifecycle_init(lifecycle_t *s) { memset(s,0,sizeof(*s)); }
void lifecycle_target_reset(lifecycle_t *s,uint64_t now) {
    if (s->phase!=LIFE_FAULT_OFF) { s->phase=LIFE_QUIESCING; s->started_us=now; }
}
bool lifecycle_request_recovery(lifecycle_t *s) {
    if (s->phase!=LIFE_FAULT_OFF || !s->recovery_eligible || s->recovery_used || s->recovery_requested) return false;
    s->recovery_requested=true; return true;
}
static void quiesce(lifecycle_t *s, lifecycle_actions_t *out, uint64_t now) {
    s->phase=LIFE_QUIESCING; s->started_us=now; out->quiesce=true;
}
lifecycle_actions_t lifecycle_step(lifecycle_t *s,lifecycle_inputs_t in,uint64_t now) {
    lifecycle_actions_t out={.oe_n=true};
    s->recovery_eligible=in.target && in.fault_n && in.quiesced && !in.locked;
    s->fault_latched|=!in.fault_n; // faults during cleanup remain latched after the input clears
    out.reset_target=s->last_target && !in.target; s->last_target=in.target;
    const bool powered=s->phase==LIFE_REARMING || s->phase==LIFE_POWER_SETTLE || s->phase==LIFE_ENUMERATING || s->phase==LIFE_READY;
    if (s->phase!=LIFE_QUIESCING && s->phase!=LIFE_FAULT_OFF &&
        (!in.fault_n || out.reset_target || (powered && (in.locked || !in.target)))) {
        s->fault_latched|=!in.fault_n; quiesce(s,&out,now); return out;
    }
    if (s->phase==LIFE_QUIESCING) {
        if (now-s->started_us>=CLEANUP_US) { s->fault_latched=true; s->phase=LIFE_FAULT_OFF; }
        else if (in.quiesced) s->phase=s->fault_latched || s->attempts>=2 ? LIFE_FAULT_OFF : LIFE_WAIT_TARGET;
        return out;
    }
    if (s->phase==LIFE_FAULT_OFF) {
        if (s->recovery_requested && !s->recovery_used && in.quiesced && in.target && in.fault_n && !in.locked) {
            s->recovery_used=true; s->recovery_requested=false; s->fault_latched=false;
            s->attempts=0; s->phase=LIFE_WAIT_TARGET;
        }
        return out;
    }
    if (!in.target || in.locked) return out;
    switch (s->phase) {
    case LIFE_WAIT_TARGET:
        if (!in.quiesced) return out;
        s->phase=LIFE_REARMING; s->started_us=now; out.rearm=true; return out;
    case LIFE_REARMING:
        if (now-s->started_us>=CLEANUP_US) { s->fault_latched=true; quiesce(s,&out,now); return out; }
        if (!in.armed) return out;
        s->phase=LIFE_POWER_SETTLE; s->started_us=now; ++s->attempts; out.en=true; return out;
    case LIFE_POWER_SETTLE:
        out.en=true;
        if (now-s->started_us<SETTLE_US) return out;
        s->phase=LIFE_ENUMERATING; s->started_us=now;
        out.oe_n=false; out.frame=true; return out;
    case LIFE_ENUMERATING:
        // Waiting for a plug is idle service, not a failed enumeration attempt.
        // Start the bounded deadline when Host leaves the no-device phase.
        if (in.host_waiting) s->started_us=now;
        if (in.host_failed || now-s->started_us>=ENUM_US) { quiesce(s,&out,now); return out; }
        if (in.host_ready) { s->phase=LIFE_READY; s->attempts=0; }
        out.en=true; out.oe_n=false; out.frame=true; return out;
    case LIFE_READY:
        if (in.host_failed) { s->fault_latched=true; quiesce(s,&out,now); return out; }
        out.en=true; out.oe_n=false; out.frame=true; return out;
    default: return out;
    }
}
