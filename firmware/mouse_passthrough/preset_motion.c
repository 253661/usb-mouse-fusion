#include <limits.h>
#include "preset_runtime.h"
#include "passthrough_core.h"
#include "event_bus.h"
enum { WAIT_BEGIN,WAIT_FINISH,WAIT_STOP_RETURN,WAIT_JOIN_RETURN,WAIT_SWITCH,
       WAIT_RETURN_END,WAIT_JOIN_END,WAIT_SWITCH_END,WAIT_JOIN_CONTINUE };
static void finish(preset_runtime_t*p,uint8_t reason){
    trajectory_stop(&p->player);p->motion.phase=PRESET_PHASE_IDLE;p->motion.reason=reason;p->active_slot=255;
}
bool preset_runtime_busy(const preset_runtime_t*p){return p->motion.phase!=PRESET_PHASE_IDLE;}
void preset_motion_cancel(preset_runtime_t*p,uint8_t reason,bool invalidate){
    bridge_trajectory_clear();finish(p,reason);++p->run_serial;if(invalidate)p->motion.known=false;
}
static void completed(void*ctx,int16_t x,int16_t y,uint32_t generation){
    preset_runtime_t*p=ctx;
    if(generation!=p->target_generation||!p->motion.known)return;
    p->motion.x+=x;p->motion.y+=y;
    const int64_t limit=INT64_C(1)<<40;
    if(p->motion.x>limit||p->motion.x<-limit||p->motion.y>limit||p->motion.y<-limit){
        preset_motion_cancel(p,PRESET_REASON_RANGE,true);p->last_error=2;
    }
}
void preset_motion_init(preset_runtime_t*p){
    p->motion.slot=p->motion.target_slot=255;
    for(unsigned i=0;i<PRESET_MODE_COUNT;i++)p->rules[i]=preset_rule_default();
    bridge_trajectory_observer(completed,p);
}
uint8_t preset_set_rule(preset_runtime_t*p,uint8_t slot,const preset_rule_t*r){
    if(p->maintenance_pending)return PRESET_BUSY;
    if(slot>=PRESET_MODE_COUNT||!preset_rule_valid(r))return 2;
    if(preset_runtime_busy(p)&&(p->motion.slot==slot||p->motion.target_slot==slot))return PRESET_BUSY;
    p->rules[slot]=*r;preset_config_changed(p);return 0;
}
static void path(preset_runtime_t*p,uint64_t now){
    p->motion.phase=PRESET_PHASE_PATH;p->active_slot=p->motion.slot;
    trajectory_start(&p->player,&p->views[p->motion.slot],now,p->target_generation);
}
uint8_t preset_motion_start(preset_runtime_t*p,uint8_t slot,uint64_t now,bool held){
    if(preset_bypass_inhibited(p))return PRESET_BYPASS;
    if(preset_mode_controlled(p)&&(!p->mode_enabled||slot!=p->selected))return PRESET_MODE_OFF;
    if(slot>=PRESET_MODE_COUNT||!p->sizes[slot]||!p->ready)return 9;
    preset_motion_cancel(p,PRESET_REASON_NONE,false);
    p->selected=slot;p->active_slot=slot;p->motion.slot=slot;p->motion.target_slot=255;
    p->motion.rule=p->rules[slot];p->motion.cycles=p->motion.elapsed_ms=p->motion.return_skipped=0;
    p->motion.started_us=now;p->motion.held=held;p->motion.phase=PRESET_PHASE_WAIT;p->motion.next=WAIT_BEGIN;p->player.running=true;
    p->perturb_seed=preset_perturb_seed(&p->views[slot].perturb,&p->perturb_sequence,now);
    // No in-flight contribution may be attributed to the new point-1 anchor.
    if(!bridge_trajectory_busy()){
        p->motion.x=p->motion.rule.origin_x;p->motion.y=p->motion.rule.origin_y;p->motion.known=true;path(p,now);
    }
    return 0;
}
static void wait_return(preset_runtime_t*p,uint8_t next,uint16_t ms,uint8_t ease){
    p->motion.phase=PRESET_PHASE_WAIT;p->motion.next=next;p->motion.transition_ticks=ms;
    p->motion.ease=ease;p->player.running=true;
}
uint8_t preset_execute(preset_runtime_t*p,uint8_t action,uint8_t slot,uint16_t ms,uint8_t ease,uint32_t crc,uint64_t now){
    if(preset_bypass_inhibited(p))return action<=PRESET_EXEC_KEEP?0:PRESET_BYPASS;
    if(action>PRESET_EXEC_SWITCH||ease>3||ms>60000)return 2;
    if(preset_mode_controlled(p)&&!p->mode_enabled){
        if(action<=PRESET_EXEC_KEEP){preset_motion_cancel(p,PRESET_REASON_STOPPED,false);return 0;}
        return PRESET_MODE_OFF;
    }
    if(action==PRESET_EXEC_SWITCH){
        if(slot>=PRESET_MODE_COUNT||!p->sizes[slot]||!p->ready)return 9;
        const uint8_t *v=p->slots[slot]+28;
        uint32_t actual=(uint32_t)v[0]|((uint32_t)v[1]<<8)|((uint32_t)v[2]<<16)|((uint32_t)v[3]<<24);
        if(crc&&crc!=actual)return 16;
        if(!p->motion.known)return preset_motion_start(p,slot,now,false);
        preset_motion_cancel(p,PRESET_REASON_NONE,false);p->motion.target_slot=slot;p->active_slot=p->motion.slot;
        if(!ms){ms=p->rules[slot].transition_action?p->rules[slot].transition_ms:0;ease=p->rules[slot].transition_ease;}
        wait_return(p,WAIT_SWITCH,ms,ease);return 0;
    }
    if(action==PRESET_EXEC_STOP){
        action=p->motion.rule.stop_action==PRESET_STOP_RETURN?PRESET_EXEC_RETURN:PRESET_EXEC_KEEP;
        ms=p->motion.rule.return_ms;ease=p->motion.rule.return_ease;
    }
    if(action==PRESET_EXEC_KEEP){preset_motion_cancel(p,PRESET_REASON_STOPPED,false);return 0;}
    if(!p->motion.known||p->motion.slot>=PRESET_MODE_COUNT||!p->ready)return 9;
    preset_motion_cancel(p,PRESET_REASON_NONE,false);p->active_slot=p->motion.slot;
    if(!ms){ms=p->motion.rule.return_ms;ease=p->motion.rule.return_ease;}
    wait_return(p,WAIT_STOP_RETURN,ms,ease);return 0;
}
static bool transition(preset_runtime_t*p,uint64_t now,int64_t x,int64_t y,uint8_t end){
    preset_motion_t*m=&p->motion;m->from_x=m->x;m->from_y=m->y;m->to_x=x;m->to_y=y;
    if(!m->transition_ticks){ // immediate switch establishes a fresh relative anchor, without a jump.
        m->x=x;m->y=y;m->next=end;return false;
    }
    int64_t dx=x-m->x,dy=y-m->y;
    int64_t ax=dx<0?-dx:dx,ay=dy<0?-dy:dy;
    // 2x peak is a conservative bound for all four easing functions.
    if((ax*2+m->transition_ticks-1)/m->transition_ticks>INT16_MAX||
       (ay*2+m->transition_ticks-1)/m->transition_ticks>INT16_MAX){
        finish(p,PRESET_REASON_RANGE);p->last_error=2;return false;
    }
    m->transition_us=now+(1000-now%1000)%1000;m->transition_tick=0;m->next=end;
    m->phase=end==WAIT_SWITCH_END?PRESET_PHASE_TRANSITION:PRESET_PHASE_RETURN;return true;
}
void preset_motion_tick(preset_runtime_t*p,uint64_t now){
    preset_motion_t*m=&p->motion;if(m->phase==PRESET_PHASE_IDLE)return;
    if(now>=m->started_us)m->elapsed_ms=(uint32_t)((now-m->started_us)/1000);
    if(m->rule.run_mode==PRESET_RUN_DURATION&&m->elapsed_ms>=m->rule.duration_ms&&
       (m->phase==PRESET_PHASE_PATH||(m->phase==PRESET_PHASE_WAIT&&(m->next==WAIT_JOIN_RETURN||m->next==WAIT_JOIN_END||m->next==WAIT_JOIN_CONTINUE))||
        (m->phase==PRESET_PHASE_RETURN&&m->next==WAIT_JOIN_END))){
        (void)preset_execute(p,PRESET_EXEC_STOP,0,0,0,0,now);
        if(m->phase==PRESET_PHASE_IDLE)return;
    }
    if(m->phase==PRESET_PHASE_WAIT){
        if(bridge_trajectory_busy())return;
        if(m->next==WAIT_BEGIN){m->x=m->rule.origin_x;m->y=m->rule.origin_y;m->known=true;path(p,now);return;}
        if(m->next==WAIT_JOIN_CONTINUE){path(p,now);return;}
        if(m->next==WAIT_FINISH){finish(p,PRESET_REASON_FINISHED);return;}
        if(m->next==WAIT_STOP_RETURN||m->next==WAIT_JOIN_RETURN||m->next==WAIT_SWITCH){
            uint8_t end=m->next==WAIT_STOP_RETURN?WAIT_RETURN_END:m->next==WAIT_JOIN_RETURN?WAIT_JOIN_END:WAIT_SWITCH_END;
            const preset_rule_t *r=end==WAIT_SWITCH_END?&p->rules[m->target_slot]:&m->rule;
            if(transition(p,now,r->origin_x,r->origin_y,end))return;
            if(m->phase==PRESET_PHASE_IDLE)return;
        }
        if(m->x!=m->to_x||m->y!=m->to_y){finish(p,PRESET_REASON_INCOMPLETE);return;}
        if(m->next==WAIT_RETURN_END){finish(p,PRESET_REASON_RETURNED);return;}
        if(m->next==WAIT_JOIN_END){path(p,now);return;}
        if(m->next==WAIT_SWITCH_END){
            m->slot=m->target_slot;m->target_slot=255;p->selected=m->slot;m->rule=p->rules[m->slot];
            p->perturb_seed=preset_perturb_seed(&p->views[m->slot].perturb,&p->perturb_sequence,now);
            m->cycles=m->elapsed_ms=0;m->started_us=now;m->held=false;path(p,now);return;
        }
    }
    if(m->phase==PRESET_PHASE_RETURN||m->phase==PRESET_PHASE_TRANSITION){
        if(now<m->transition_us)return;
        uint64_t due=(now-m->transition_us)/1000;if(due<=m->transition_tick)return;
        if(due>m->transition_ticks){m->return_skipped+=m->transition_ticks-m->transition_tick;m->transition_tick=m->transition_ticks;
            m->phase=PRESET_PHASE_WAIT;return;}
        m->return_skipped+=(uint32_t)due-m->transition_tick-1;m->transition_tick=(uint32_t)due;
        int64_t dx=m->to_x-m->from_x,dy=m->to_y-m->from_y;
        int64_t x=preset_eased_position(dx,(uint32_t)due,m->transition_ticks,m->ease)-preset_eased_position(dx,(uint32_t)due-1,m->transition_ticks,m->ease);
        int64_t y=preset_eased_position(dy,(uint32_t)due,m->transition_ticks,m->ease)-preset_eased_position(dy,(uint32_t)due-1,m->transition_ticks,m->ease);
        uint8_t result=bridge_trajectory_offer((int16_t)x,(int16_t)y,now,m->transition_us+(due+1)*1000);
        if(result==0)++p->bridge_accepted;else ++p->bridge_dropped;
        if(due==m->transition_ticks)m->phase=PRESET_PHASE_WAIT;
        return;
    }
    if(m->phase==PRESET_PHASE_PATH){
        int16_t dx,dy;uint64_t expires;
        if(trajectory_step(&p->player,now,&dx,&dy,&expires)){
            // Derive just the current 1ms offset difference, including when the
            // scheduler skipped earlier samples. Actual HID completion remains
            // the sole position ledger used by returns and smooth switches.
            if(!preset_perturb_delta(&p->views[m->slot].perturb,p->perturb_seed,
                                     p->player.consumed,p->views[m->slot].samples,&dx,&dy)){
                preset_motion_cancel(p,PRESET_REASON_RANGE,false);p->last_error=2;return;
            }
            uint8_t result=bridge_trajectory_offer(dx,dy,now,expires);
            if(result==0)++p->bridge_accepted;else ++p->bridge_dropped;
        }
        if(!p->player.running){
            ++m->cycles;
            bool repeat=m->held||m->rule.run_mode==PRESET_RUN_LOOP||m->rule.run_mode==PRESET_RUN_HOLD||
                m->rule.run_mode==PRESET_RUN_DURATION||(m->rule.run_mode==PRESET_RUN_COUNT&&m->cycles<m->rule.repeat_count);
            if(repeat&&m->rule.loop_join==PRESET_JOIN_CONTINUE){wait_return(p,WAIT_JOIN_CONTINUE,0,0);return;}
            if(repeat)wait_return(p,WAIT_JOIN_RETURN,m->rule.return_ms,m->rule.return_ease);
            else if(m->rule.stop_action==PRESET_STOP_RETURN)wait_return(p,WAIT_STOP_RETURN,m->rule.return_ms,m->rule.return_ease);
            else {m->phase=PRESET_PHASE_WAIT;m->next=WAIT_FINISH;}
        }
    }
}
