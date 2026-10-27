#include <limits.h>
#include <string.h>
#include "button_state.h"
#include "event_bus.h"
#include "passthrough_core.h"
#include "ai_smooth.h"
#if FUSION_TRAJECTORY
#include "j4_preset.h"
#endif
typedef struct {
    int64_t values[4]; uint32_t generation; bool needed;
    uint64_t received_us; uint32_t first_sequence, last_sequence;
} segment_t;
typedef struct { mouse_report_t report, physical; uint32_t generation,target_generation; int16_t preset_x,preset_y; bool neutral,preset; } debit_t;
static void (*preset_completed)(void*,int16_t,int16_t,uint32_t);
static void *preset_context;
#if FUSION_BRIDGE_AUX_SIMULATION
typedef struct {
    aux_delta_t queue[BRIDGE_AUX_QUEUE_CAPACITY];
    uint32_t read, write, session; uint64_t expires_us, motion_expires_us; bool active;
} auxiliary_t;
#endif
static struct {
    mouse_button_state_t buttons;
    segment_t segments[SOURCE_COUNT];
    uint32_t epoch, barrier, overflow, clear_serial, discarded, auxiliary_dropped, snapshot_busy;
    uint32_t submitted, completed;
    uint64_t now_us, cycle, ready_at;
    bool cycle_sent, waiting, buttons_dirty;
    uint16_t last_buttons;
    bool connected, neutral, degraded, prepared, in_flight, restore;
    bool bypass;
    uint32_t restore_buttons, restore_epoch;
    debit_t candidate, pending;
#if FUSION_J4_AUX
    struct { int16_t dx,dy; uint64_t received_us,expires_us,last_cycle,motion_expires_us;
             bool pending,ready,gated,smooth_mode; uint32_t gate[4]; ai_smooth_t smooth; } api;
#endif
#if FUSION_TRAJECTORY
    struct { int16_t dx,dy; uint64_t received_us,expires_us,last_cycle,motion_expires_us; bool pending; } preset;
#endif
#if FUSION_BRIDGE_AUX_SIMULATION
    auxiliary_t auxiliary[SOURCE_COUNT-1];
#endif
} core;
uint32_t const fusion_bridge_contract[]={0x42524733,PHYSICAL_QUEUE_CAPACITY,BRIDGE_POLL_MAX_EVENTS,
    PHYSICAL_SNAPSHOT_ATTEMPTS,FUSION_BRIDGE_AUX_SIMULATION,sizeof(mouse_delta_t),sizeof(physical_record_t),sizeof(core),
    BRIDGE_PERIOD_US,BRIDGE_COALESCE_US,BRIDGE_SUBMIT_MARGIN_US};
static void clear_segment(source_id_t source) {
    segment_t *s=&core.segments[source];
    memset(s->values,0,sizeof(s->values)); ++s->generation; s->needed=false;
    s->received_us=0; s->first_sequence=s->last_sequence=0;
#if FUSION_BRIDGE_AUX_SIMULATION
    if (source!=SOURCE_PHYSICAL) core.auxiliary[source-1].motion_expires_us=0;
#endif
}
static void require_neutral(void) { core.neutral=true; ++core.clear_serial; core.prepared=false; }
static void clear_buttons(source_id_t source) {
    core.buttons.sources[source].buttons=0; core.buttons.sources[source].active=false;
}
bool bridge_ai_gate_status(uint32_t gate[4],uint16_t *buttons){
    physical_snapshot_t s;
    if(!event_bus_snapshot(&s)||!s.connected||s.target_generation!=event_bus_target_generation())return false;
    gate[0]=s.epoch;gate[1]=s.target_generation;gate[2]=s.button_presses[0];gate[3]=s.overflow;
    *buttons=(uint16_t)s.buttons;return true;
}
#if FUSION_J4_AUX
static bool ai_gate_matches(const uint32_t gate[4]){
    uint32_t current[4];uint16_t buttons=0;
    return bridge_ai_gate_status(current,&buttons)&&(buttons&1)&&memcmp(current,gate,sizeof current)==0;
}
static void ai_gate_check(void){
    if(core.api.gated&&!ai_gate_matches(core.api.gate))bridge_ai_clear();
}
#endif
static bool synchronize_physical(void) {
    physical_snapshot_t snapshot;
    if (!event_bus_snapshot(&snapshot)) {
        ++core.snapshot_busy;
#if FUSION_TRAJECTORY
        preset_bypass_observe(j4_preset_runtime(),0,core.epoch,false);
#endif
        return false;
    }
    if (snapshot.target_generation!=event_bus_target_generation()) {
#if FUSION_TRAJECTORY
        preset_bypass_observe(j4_preset_runtime(),0,core.epoch,false);
#endif
        return true;
    }
    if (snapshot.barrier!=core.barrier) {
#if FUSION_TRAJECTORY
        preset_runtime_physical_reset(j4_preset_runtime());
#endif
        clear_segment(SOURCE_PHYSICAL); clear_buttons(SOURCE_PHYSICAL); require_neutral();
        core.discarded+=event_bus_discard_before(snapshot.barrier_cutoff);
        core.epoch=snapshot.epoch; core.connected=snapshot.connected!=0; core.barrier=snapshot.barrier;
        core.restore=false;
        if (core.connected) (void)mouse_buttons_begin(&core.buttons,SOURCE_PHYSICAL,core.epoch);
    }
    if (snapshot.overflow!=core.overflow) {
#if FUSION_TRAJECTORY
        preset_runtime_physical_reset(j4_preset_runtime());
#endif
        // Overflow is a loss, not a successful load test. Discard the exact
        // published prefix, release physical ownership first, then publish the
        // known latest buttons. Events after this cutoff retain FIFO order.
        core.overflow=snapshot.overflow; core.degraded=true;
        clear_segment(SOURCE_PHYSICAL); core.buttons.sources[SOURCE_PHYSICAL].buttons=0; require_neutral();
        core.discarded+=event_bus_discard_before(snapshot.snapshot_cutoff);
        core.restore=core.connected; core.restore_buttons=snapshot.buttons; core.restore_epoch=snapshot.epoch;
    }
#if FUSION_TRAJECTORY
    preset_bypass_snapshot(j4_preset_runtime(),&snapshot,true);
#endif
    return true;
}
void bridge_init(void) {
    memset(&core,0,sizeof(core)); core.cycle=UINT64_MAX; event_bus_init(); require_neutral();
#if FUSION_J4_AUX
    core.api.last_cycle=UINT64_MAX;
#endif
#if FUSION_TRAJECTORY
    core.preset.last_cycle=UINT64_MAX;
#endif
}
void bridge_trajectory_clear(void){
#if FUSION_TRAJECTORY
    if(core.preset.pending||core.segments[SOURCE_TRAJECTORY].needed)++core.auxiliary_dropped;
    core.preset.pending=false;core.preset.motion_expires_us=0;clear_segment(SOURCE_TRAJECTORY);core.prepared=false;
#endif
}
void bridge_trajectory_observer(void (*callback)(void*,int16_t,int16_t,uint32_t),void *context){preset_completed=callback;preset_context=context;}
bool bridge_trajectory_busy(void){
#if FUSION_TRAJECTORY
    return core.preset.pending||core.segments[SOURCE_TRAJECTORY].needed||(core.in_flight&&core.pending.preset);
#else
    return false;
#endif
}
uint8_t bridge_trajectory_offer(int16_t dx,int16_t dy,uint64_t received_us,uint64_t expires_us){
#if FUSION_TRAJECTORY
    if(core.bypass)return 17;
    if(expires_us<=received_us)return 5;
    if(!core.api.ready||core.neutral)return 9;
    const uint64_t cycle=received_us/BRIDGE_PERIOD_US;
    if(core.preset.last_cycle==cycle||(core.cycle==cycle&&core.cycle_sent))return 10;
    if(core.preset.pending)++core.auxiliary_dropped;
    core.preset.dx=dx;core.preset.dy=dy;core.preset.received_us=received_us;core.preset.expires_us=expires_us;
    core.preset.last_cycle=cycle;core.preset.pending=true;core.prepared=false;return 0;
#else
    (void)dx;(void)dy;(void)received_us;(void)expires_us;return 9;
#endif
}
void bridge_ai_clear(void) {
#if FUSION_J4_AUX
    if(core.api.pending || core.segments[SOURCE_AI].needed) ++core.auxiliary_dropped;
    core.api.pending=false;core.api.motion_expires_us=0;core.api.gated=false;
    core.api.smooth_mode=false;ai_smooth_reset(&core.api.smooth);
    clear_segment(SOURCE_AI);core.prepared=false;
#endif
}
void bridge_auxiliary_clear(void){
    bridge_ai_clear();bridge_trajectory_clear();
    for(unsigned source=SOURCE_TRAJECTORY;source<SOURCE_COUNT;++source){
        if(core.buttons.sources[source].buttons)core.buttons_dirty=true;
        clear_buttons((source_id_t)source);clear_segment((source_id_t)source);
#if FUSION_BRIDGE_AUX_SIMULATION
        auxiliary_t*a=&core.auxiliary[source-1];core.auxiliary_dropped+=a->write-a->read;
        a->read=a->write;a->active=false;a->expires_us=0;
#endif
    }
    core.prepared=false;core.waiting=false;
}
void bridge_bypass_set(bool active){if(core.bypass!=active){bridge_auxiliary_clear();core.bypass=active;}}
bool bridge_auxiliary_inflight(void){return core.in_flight&&(core.pending.preset||core.pending.report.x!=core.pending.physical.x||core.pending.report.y!=core.pending.physical.y);}
void bridge_ai_set_ready(bool ready) {
#if FUSION_J4_AUX
    if(!ready && core.api.ready)bridge_ai_clear();
#if FUSION_TRAJECTORY
    if(!ready)bridge_trajectory_clear();
#endif
    core.api.ready=ready;
#else
    (void)ready;
#endif
}
uint8_t bridge_ai_offer(int16_t dx,int16_t dy,uint64_t received_us,uint64_t expires_us) {
#if FUSION_J4_AUX
    if(core.bypass)return 17;
    if(expires_us<=received_us)return 5;
    if(!core.api.ready || core.neutral)return 9;
    const uint64_t cycle=received_us/BRIDGE_PERIOD_US;
    if(core.api.last_cycle==cycle || (core.cycle==cycle && core.cycle_sent))return 10;
    bridge_ai_clear();
    core.api.dx=dx;core.api.dy=dy;core.api.received_us=received_us;core.api.expires_us=expires_us;
    core.api.last_cycle=cycle;core.api.pending=true;core.api.gated=false;core.prepared=false;return 0;
#else
    (void)dx;(void)dy;(void)received_us;(void)expires_us;return 9;
#endif
}
uint8_t bridge_ai_offer_gated(int16_t dx,int16_t dy,uint64_t received_us,uint64_t expires_us,const uint32_t gate[4]){
#if FUSION_J4_AUX
    if(!gate||!ai_gate_matches(gate))return 20;
    if(core.bypass)return 17;
    if(expires_us<=received_us)return 5;
    if(!core.api.ready||core.neutral)return 9;
    uint64_t scheduled=received_us/BRIDGE_PERIOD_US;
    if(core.cycle==scheduled&&core.cycle_sent)++scheduled;
    if(core.api.last_cycle==scheduled&&!(core.api.gated&&(core.api.pending||core.segments[SOURCE_AI].needed)))return 10;
    bridge_ai_clear(); // Replace the whole unsent sample; never sum retired holds.
    core.api.dx=dx;core.api.dy=dy;core.api.received_us=received_us;core.api.expires_us=expires_us;
    core.api.last_cycle=scheduled;core.api.pending=true;core.api.gated=true;core.prepared=false;
    memcpy(core.api.gate,gate,sizeof core.api.gate);return 0;
#else
    (void)dx;(void)dy;(void)received_us;(void)expires_us;(void)gate;return 9;
#endif
}
uint8_t bridge_ai_offer_smooth(int16_t dx,int16_t dy,uint64_t received_us,uint64_t expires_us,const uint32_t gate[4],uint16_t duration_ms){
#if FUSION_J4_AUX
    if(!gate||!ai_gate_matches(gate))return 20;
    if(core.bypass)return 17;
    if(!core.api.ready||core.neutral)return 9;
    if(duration_ms<20||duration_ms>40)return 8;
    ai_smooth_t fresh;
    if(!ai_smooth_begin(&fresh,dx,dy,received_us,expires_us,duration_ms))return 5;
    bridge_ai_clear();core.api.smooth=fresh;core.api.smooth_mode=true;core.api.gated=true;
    core.api.expires_us=expires_us;core.api.received_us=received_us;core.api.last_cycle=received_us/BRIDGE_PERIOD_US;
    memcpy(core.api.gate,gate,sizeof core.api.gate);return 0;
#else
    (void)dx;(void)dy;(void)received_us;(void)expires_us;(void)gate;(void)duration_ms;return 9;
#endif
}
void bridge_ai_smooth_cancel(void){
#if FUSION_J4_AUX
    if(core.api.smooth_mode)bridge_ai_clear();
#endif
}
bool physical_offer(const mouse_delta_t *event) { return event_bus_offer(event); }
void trigger_observe_physical(const mouse_delta_t *event) {
#if FUSION_TRAJECTORY
    preset_runtime_observe_wheel(j4_preset_runtime(),(uint8_t)core.buttons.sources[SOURCE_PHYSICAL].buttons,event->wheel,event->epoch,event->received_us);
#else
    (void)event;
#endif
}
static bool accumulate(segment_t *s,const mouse_delta_t *event) {
    const int32_t increments[]={event->dx,event->dy,event->wheel,event->pan};
    for (unsigned i=0;i<4;++i) {
        if ((increments[i]>0 && s->values[i]>INT64_MAX-increments[i]) ||
            (increments[i]<0 && s->values[i]<INT64_MIN-increments[i])) return false;
    }
    for (unsigned i=0;i<4;++i) s->values[i]+=increments[i];
    return true;
}
#if FUSION_BRIDGE_AUX_SIMULATION
static bool valid_auxiliary(source_id_t source) { return source==SOURCE_AI || source==SOURCE_TRAJECTORY; }
static bool newer(uint32_t session,uint32_t previous) {
    const uint32_t diff=session-previous; return diff!=0 && diff<UINT32_C(0x80000000);
}
bool auxiliary_offer(const aux_delta_t *event) {
    if(core.bypass)return false;
    if (!event || !valid_auxiliary(event->source) || !event->session || event->expires_us<=event->delta.received_us ||
        event->delta.wheel || event->delta.pan ||
        ((event->delta.buttons | event->delta.buttons_valid) & ~(uint32_t)MOUSE_SUPPORTED_BUTTON_MASK)) return false;
    auxiliary_t *a=&core.auxiliary[event->source-1];
    if (event->session!=a->session) {
        if (a->session && !newer(event->session,a->session)) return false;
        if (a->active) source_invalidate(event->source,a->session);
        a->session=event->session; a->active=true; a->expires_us=0;
        (void)mouse_buttons_begin(&core.buttons,event->source,a->session);
    }
    if (!a->active) return false;
    if (a->write-a->read>=BRIDGE_AUX_QUEUE_CAPACITY) { ++core.auxiliary_dropped; return false; }
    a->queue[a->write++ & (BRIDGE_AUX_QUEUE_CAPACITY-1)]=*event;
    a->expires_us=event->expires_us; return true;
}
#else
bool auxiliary_offer(const aux_delta_t *event) { (void)event; return false; } // no AI/trajectory ingress in firmware
#endif
void source_invalidate(source_id_t source,uint32_t session) {
    if (source==SOURCE_PHYSICAL) { event_bus_invalidate(session); return; } // Core1 publisher only
#if FUSION_BRIDGE_AUX_SIMULATION
    if (!valid_auxiliary(source)) return;
    auxiliary_t *a=&core.auxiliary[source-1];
    if (!a->active || a->session!=session) return;
    core.auxiliary_dropped+=a->write-a->read;
    if (core.segments[source].needed) ++core.auxiliary_dropped;
    a->active=false; a->read=a->write; a->expires_us=0;
    const bool owned_buttons=core.buttons.sources[source].buttons!=0;
    clear_buttons(source); clear_segment(source);
    if (owned_buttons) require_neutral(); else core.prepared=false;
#else
    (void)session;
#endif
}
void target_session_reset(void) {
    core.discarded+=event_bus_depth(); event_bus_target_reset();
    mouse_buttons_target_reset(&core.buttons);
    for (unsigned i=0;i<SOURCE_COUNT;++i) clear_segment((source_id_t)i);
    core.connected=false; core.epoch=0; core.restore=false; core.in_flight=false;
    core.buttons_dirty=false; core.waiting=false;
#if FUSION_J4_AUX
    core.api.pending=core.api.ready=false;core.api.motion_expires_us=0;
    core.api.smooth_mode=false;ai_smooth_reset(&core.api.smooth);
    core.api.last_cycle=UINT64_MAX;
#endif
#if FUSION_TRAJECTORY
    core.preset.pending=false;core.preset.motion_expires_us=0;core.preset.last_cycle=UINT64_MAX;
#endif
#if FUSION_BRIDGE_AUX_SIMULATION
    for (unsigned i=0;i<SOURCE_COUNT-1;++i) {
        auxiliary_t *a=&core.auxiliary[i]; a->active=false; a->read=a->write; a->expires_us=0;
    }
#endif
    require_neutral();
}
static uint32_t poll_physical(uint32_t budget) {
    uint32_t used=0;
    for (;used<budget;++used) {
        physical_record_t record;
        if (!event_bus_peek(&record)) break;
        if (!core.connected || record.delta.epoch!=core.epoch || record.target_generation!=event_bus_target_generation()) {
            (void)event_bus_pop(); ++core.discarded; continue;
        }
        mouse_button_source_t *buttons=&core.buttons.sources[SOURCE_PHYSICAL];
        const uint32_t next=(buttons->buttons & ~record.delta.buttons_valid) |
                            (record.delta.buttons & record.delta.buttons_valid);
        segment_t *s=&core.segments[SOURCE_PHYSICAL];
        if (s->needed && next!=buttons->buttons) break; // never cross a button transition
        if (!s->needed) { s->received_us=record.delta.received_us; s->first_sequence=record.delta.sequence; }
        s->last_sequence=record.delta.sequence;
        if (!accumulate(s,&record.delta)) {
            (void)event_bus_pop(); ++core.discarded; core.degraded=true;
            clear_segment(SOURCE_PHYSICAL); buttons->buttons=0; require_neutral(); break;
        }
        s->needed=s->needed || next!=buttons->buttons || record.delta.dx || record.delta.dy || record.delta.wheel || record.delta.pan;
        (void)mouse_buttons_apply(&core.buttons,SOURCE_PHYSICAL,&record.delta);
        trigger_observe_physical(&record.delta); // Core0 accepted ledger; never Core1 ingress
        (void)event_bus_pop(); // staged in Core0 int64 ledger, NEVER debited before completion
    }
    return used;
}
void bridge_poll(uint64_t now_us,uint32_t max_events) {
#if FUSION_J4_AUX
    ai_gate_check();
#endif
    core.prepared=false;
    if (now_us<core.now_us) now_us=core.now_us; // GET_REPORT cannot rewind scheduling
    core.now_us=now_us;
    const uint64_t cycle=now_us/BRIDGE_PERIOD_US;
    if (cycle!=core.cycle) {
        core.cycle=cycle; core.cycle_sent=false;
        for (unsigned source=SOURCE_TRAJECTORY;source<SOURCE_COUNT;++source) {
#if FUSION_J4_AUX
            // An unsent whole gated sample can cross exactly one boundary.
            // USB-submitted contributions were already cleared; never replay them.
            if(source==SOURCE_AI&&core.api.gated&&!core.api.smooth_mode&&core.segments[source].needed&&
               now_us<core.api.expires_us&&cycle==core.api.received_us/BRIDGE_PERIOD_US+1){
                core.api.pending=true;core.api.last_cycle=cycle;
                clear_segment(SOURCE_AI);continue;
            }
#endif
            if (core.segments[source].needed) ++core.auxiliary_dropped;
            clear_segment((source_id_t)source); // no auxiliary remainder crosses a period
        }
    }
    if (!synchronize_physical()) return;
#if FUSION_BRIDGE_AUX_SIMULATION
    for (unsigned source=SOURCE_TRAJECTORY;source<SOURCE_COUNT;++source) {
        auxiliary_t *a=&core.auxiliary[source-1];
        if (a->active && a->expires_us && now_us>=a->expires_us) source_invalidate((source_id_t)source,a->session);
    }
#endif
    if (!core.neutral && !core.in_flight && core.restore) {
        core.restore=false;
        if (core.connected && core.restore_epoch==core.epoch) {
            core.buttons.sources[SOURCE_PHYSICAL].buttons=core.restore_buttons;
            core.segments[SOURCE_PHYSICAL].needed=true;
        }
    }
    const uint32_t budget=max_events>BRIDGE_POLL_MAX_EVENTS ? BRIDGE_POLL_MAX_EVENTS : max_events;
    if (!core.neutral && !core.in_flight) (void)poll_physical(budget);
#if FUSION_J4_AUX
    if(core.api.smooth_mode){
        if(now_us>=core.api.expires_us)bridge_ai_clear();
        else{
            int16_t x=0,y=0;
            if(ai_smooth_step(&core.api.smooth,now_us,&x,&y)){
                core.api.last_cycle=cycle;
                if(!core.neutral&&!core.cycle_sent){
                    mouse_delta_t step={.dx=x,.dy=y};segment_t *s=&core.segments[SOURCE_AI];
                    if(accumulate(s,&step)){s->needed|=x||y;core.api.motion_expires_us=core.api.expires_us;}
                    else{++core.auxiliary_dropped;clear_segment(SOURCE_AI);}
                }else if(x||y)++core.auxiliary_dropped;
            }
        }
    }
    if(core.segments[SOURCE_AI].needed && core.api.motion_expires_us && now_us>=core.api.motion_expires_us){
        ++core.auxiliary_dropped;clear_segment(SOURCE_AI);core.api.motion_expires_us=0;
    }
    if(core.api.pending){
        const uint64_t received_cycle=core.api.received_us/BRIDGE_PERIOD_US;
        if(core.api.gated){
            if(cycle==received_cycle+1&&core.api.last_cycle==received_cycle)core.api.last_cycle=cycle;
            if(cycle==received_cycle&&core.cycle_sent)core.api.last_cycle=cycle+1;
        }
        const bool scheduled=core.api.gated?cycle==core.api.last_cycle:cycle==received_cycle;
        const bool stale_cycle=core.api.gated?cycle>received_cycle+1||cycle>core.api.last_cycle:cycle!=received_cycle;
        if(now_us>=core.api.expires_us || stale_cycle || (scheduled&&core.cycle_sent) || core.neutral){
            ++core.auxiliary_dropped;core.api.pending=false;
        }else if(scheduled&&core.api.received_us<=now_us){
            mouse_delta_t event={.dx=core.api.dx,.dy=core.api.dy};
            segment_t *s=&core.segments[SOURCE_AI];
            if(!accumulate(s,&event)){++core.auxiliary_dropped;clear_segment(SOURCE_AI);}
            else{s->needed|=event.dx!=0 || event.dy!=0;core.api.motion_expires_us=core.api.expires_us;}
            core.api.pending=false;
        }
    }
#endif
#if FUSION_TRAJECTORY
    if(core.segments[SOURCE_TRAJECTORY].needed&&now_us>=core.preset.motion_expires_us){
        ++core.auxiliary_dropped;clear_segment(SOURCE_TRAJECTORY);
    }
    if(core.preset.pending){
        if(now_us>=core.preset.expires_us||core.preset.received_us/BRIDGE_PERIOD_US!=cycle||
           core.cycle_sent||core.neutral||core.api.last_cycle==cycle){
            ++core.auxiliary_dropped;
        }else{
            mouse_delta_t d={.dx=core.preset.dx,.dy=core.preset.dy};segment_t *s=&core.segments[SOURCE_TRAJECTORY];
            if(!accumulate(s,&d)){++core.auxiliary_dropped;clear_segment(SOURCE_TRAJECTORY);}
            else{s->needed|=d.dx!=0||d.dy!=0;core.preset.motion_expires_us=core.preset.expires_us;}
        }
        core.preset.pending=false;
    }
    if(core.api.last_cycle==cycle&&core.segments[SOURCE_TRAJECTORY].needed){
        ++core.auxiliary_dropped;clear_segment(SOURCE_TRAJECTORY);
    }
#endif
#if FUSION_BRIDGE_AUX_SIMULATION
    for (unsigned source=SOURCE_TRAJECTORY;source<SOURCE_COUNT;++source) {
        auxiliary_t *a=&core.auxiliary[source-1]; segment_t *s=&core.segments[source];
        if (s->needed && a->motion_expires_us && now_us>=a->motion_expires_us) {
            ++core.auxiliary_dropped; clear_segment((source_id_t)source);
        }
        for (unsigned n=0;a->active && a->read!=a->write && n<BRIDGE_AUX_QUEUE_CAPACITY;++n) {
            const aux_delta_t e=a->queue[a->read & (BRIDGE_AUX_QUEUE_CAPACITY-1)];
            if (e.delta.received_us>now_us) break;
            if (now_us>=e.expires_us || e.delta.received_us/BRIDGE_PERIOD_US!=cycle || core.cycle_sent) {
                ++a->read; ++core.auxiliary_dropped; continue;
            }
            if (core.neutral) break;
            ++a->read;
            mouse_delta_t d=e.delta; d.epoch=e.session;
            const uint32_t before=core.buttons.sources[source].buttons;
            if (!accumulate(s,&d)) { ++core.auxiliary_dropped; clear_segment((source_id_t)source); continue; }
            (void)mouse_buttons_apply(&core.buttons,(source_id_t)source,&d);
            core.buttons_dirty|=before!=core.buttons.sources[source].buttons;
            s->needed|=d.dx!=0 || d.dy!=0;
            if ((d.dx || d.dy) && (!a->motion_expires_us || e.expires_us<a->motion_expires_us)) a->motion_expires_us=e.expires_us;
        }
    }
#endif
    bool motion=false;
    for (unsigned source=0;source<SOURCE_COUNT;++source)
        for (unsigned axis=0;axis<4;++axis) motion|=core.segments[source].values[axis]!=0;
    if (motion && !core.waiting) {
        const uint64_t period_start=now_us-now_us%BRIDGE_PERIOD_US;
        const uint64_t offset=BRIDGE_PERIOD_US-BRIDGE_SUBMIT_MARGIN_US;
        const uint64_t latest=period_start>UINT64_MAX-offset ? UINT64_MAX : period_start+offset;
        core.ready_at=now_us>latest ? now_us : (latest-now_us<BRIDGE_COALESCE_US ? latest : now_us+BRIDGE_COALESCE_US);
        core.waiting=true;
    }
}
static int32_t slice(int64_t value,int32_t minimum,int32_t maximum) {
    return value<minimum ? minimum : value>maximum ? maximum : (int32_t)value;
}
bool bridge_prepare(mouse_report_t *report) {
    core.prepared=false;
#if FUSION_J4_AUX
    ai_gate_check();
    // The raw publisher can be ahead of the physical FIFO: never combine gated
    // motion with an older released-button report while draining that FIFO.
    if(core.api.gated&&!(core.buttons.sources[SOURCE_PHYSICAL].buttons&1))bridge_ai_clear();
#endif
    if (!report || core.in_flight || core.cycle_sent) return false;
    physical_snapshot_t snapshot;
    if (!event_bus_snapshot(&snapshot)) { ++core.snapshot_busy; return false; }
#if FUSION_TRAJECTORY
    preset_bypass_snapshot(j4_preset_runtime(),&snapshot,true);
#endif
    // A publisher changed after poll: defer to the next bounded poll rather
    // than send a candidate computed before an invalidation/overflow barrier.
    if (snapshot.target_generation==event_bus_target_generation() &&
        (snapshot.barrier!=core.barrier || snapshot.overflow!=core.overflow)) return false;
    debit_t candidate={.report={.buttons=mouse_buttons_value(&core.buttons)}};
    if (core.neutral) { candidate.neutral=true; candidate.generation=core.clear_serial; }
    else {
        const segment_t *s=&core.segments[SOURCE_PHYSICAL]; candidate.generation=s->generation;
        if (!s->needed && event_bus_depth()) return false; // first stage pending physical buttons
        candidate.physical.x=(int16_t)slice(s->values[0],INT16_MIN,INT16_MAX);
        candidate.physical.y=(int16_t)slice(s->values[1],INT16_MIN,INT16_MAX);
        candidate.physical.wheel=(int8_t)slice(s->values[2],INT8_MIN,INT8_MAX);
        candidate.physical.pan=(int8_t)slice(s->values[3],INT8_MIN,INT8_MAX);
        candidate.report.x=candidate.physical.x; candidate.report.y=candidate.physical.y;
        candidate.report.wheel=candidate.physical.wheel; candidate.report.pan=candidate.physical.pan;
        bool needed=s->needed || core.buttons_dirty;
        int64_t auxiliary_xy[2]={0,0};
        bool auxiliary_valid=true, auxiliary_needed=false;
        for (unsigned source=SOURCE_TRAJECTORY;source<SOURCE_COUNT;++source) {
            const segment_t *aux=&core.segments[source];
            if (!aux->needed) continue;
            auxiliary_needed=true;
            for (unsigned axis=0;axis<2 && auxiliary_valid;++axis) {
                const int64_t value=aux->values[axis];
                if ((value>0 && auxiliary_xy[axis]>INT64_MAX-value) ||
                    (value<0 && auxiliary_xy[axis]<INT64_MIN-value)) auxiliary_valid=false;
                else auxiliary_xy[axis]+=value;
            }
        }
        // The combined auxiliary X/Y is one optional addition. Any overflow
        // drops the whole addition while preserving physical motion/buttons.
        if (!auxiliary_valid || auxiliary_xy[0]<INT16_MIN-candidate.report.x || auxiliary_xy[0]>INT16_MAX-candidate.report.x ||
            auxiliary_xy[1]<INT16_MIN-candidate.report.y || auxiliary_xy[1]>INT16_MAX-candidate.report.y) {
            for (unsigned source=SOURCE_TRAJECTORY;source<SOURCE_COUNT;++source) {
                if (core.segments[source].needed) ++core.auxiliary_dropped;
                clear_segment((source_id_t)source);
            }
        } else {
            candidate.report.x=(int16_t)(candidate.report.x+auxiliary_xy[0]);
            candidate.report.y=(int16_t)(candidate.report.y+auxiliary_xy[1]); needed|=auxiliary_needed;
#if FUSION_TRAJECTORY
            candidate.preset=core.segments[SOURCE_TRAJECTORY].needed;
            candidate.preset_x=(int16_t)core.segments[SOURCE_TRAJECTORY].values[0];
            candidate.preset_y=(int16_t)core.segments[SOURCE_TRAJECTORY].values[1];
            candidate.target_generation=event_bus_target_generation();
#endif
        }
        if (!needed) return false;
        // Button transitions/clears never wait for auxiliary motion.
        const bool button_transition=candidate.report.buttons!=core.last_buttons;
        if (!button_transition && !core.buttons_dirty && core.waiting && core.now_us<core.ready_at) return false;
    }
    core.candidate=candidate; core.prepared=true; *report=candidate.report; return true;
}
bool bridge_submitted(void) {
    if (!core.prepared || core.in_flight) return false;
    core.pending=core.candidate; core.prepared=false; core.in_flight=true; ++core.submitted;
    core.cycle_sent=true; core.waiting=false; core.buttons_dirty=false; core.last_buttons=core.pending.report.buttons;
    for (unsigned source=SOURCE_TRAJECTORY;source<SOURCE_COUNT;++source) clear_segment((source_id_t)source);
    return true;
}
bool bridge_completed(void) {
    if (!core.in_flight) return false;
    core.in_flight=false; ++core.completed;
    const debit_t *p=&core.pending;
    if (p->neutral) { if (p->generation==core.clear_serial) core.neutral=false; }
    else {
        segment_t *s=&core.segments[SOURCE_PHYSICAL];
        if (s->generation==p->generation) {
            s->values[0]-=p->physical.x; s->values[1]-=p->physical.y;
            s->values[2]-=p->physical.wheel; s->values[3]-=p->physical.pan;
            if (!s->values[0] && !s->values[1] && !s->values[2] && !s->values[3]) s->needed=false;
        }
    }
    if(p->preset&&preset_completed)preset_completed(preset_context,p->preset_x,p->preset_y,p->target_generation);
    return true;
}
void bridge_get_report(mouse_report_t *report) {
    (void)synchronize_physical(); // refresh invalidation, without changing the report-cycle clock
    if (report) *report=(mouse_report_t){.buttons=mouse_buttons_value(&core.buttons)};
}
bool bridge_prepared_trace(bridge_trace_context_t *context) {
    if (!context || !core.prepared) return false;
    const segment_t *s=&core.segments[SOURCE_PHYSICAL];
    *context=(bridge_trace_context_t){.physical_epoch=core.epoch,.neutral=core.candidate.neutral};
    if (!core.candidate.neutral && s->needed) {
        context->received_us=s->received_us; context->first_sequence=s->first_sequence;
        context->last_sequence=s->last_sequence;
    }
    return true;
}
bridge_status_t bridge_snapshot(void) {
    const segment_t *s=&core.segments[SOURCE_PHYSICAL];
    return (bridge_status_t){.physical_epoch=core.epoch,.target_generation=event_bus_target_generation(),
        .queued=event_bus_depth(),.high_water=event_bus_high_water(),.overflow=core.overflow,
        .discarded=core.discarded,.auxiliary_dropped=core.auxiliary_dropped,.snapshot_busy=core.snapshot_busy,.degraded=core.degraded,
        .submitted=core.submitted,.completed=core.completed,.in_flight=core.in_flight,
        .dx=s->values[0],.dy=s->values[1],.wheel=s->values[2],.pan=s->values[3],.buttons=mouse_buttons_value(&core.buttons)};
}
