#include <string.h>
#include "preset_runtime.h"
#include "passthrough_core.h"
#include "event_bus.h"
static uint32_t r32(const uint8_t *p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
void preset_runtime_init(preset_runtime_t *p){
    memset(p,0,sizeof *p);p->active_slot=255;p->target_generation=event_bus_target_generation();
    preset_motion_init(p);
    p->bypass.generation=1;p->config_revision=1;
}
void preset_config_changed(preset_runtime_t*p){if(!++p->config_revision)++p->config_revision;}
bool preset_bypass_mask_valid(uint8_t mask){return mask<=16&&!(mask&(mask-1));}
bool preset_mode_controlled(const preset_runtime_t*p){
    for(unsigned i=0;i<p->triggers.count;i++)if(p->triggers.records[i].enabled&&p->triggers.records[i].action>=5)return true;
    return false;
}
bool preset_bypass_inhibited(const preset_runtime_t*p){return p->maintenance_pending||(p->bypass.mask&&(p->bypass.active||!p->bypass.input_valid));}
static void bypass_cancel(preset_runtime_t*p,uint8_t reason){
    p->mode_enabled=false;
    preset_motion_cancel(p,reason,false);p->motion.held=false;p->motion.target_slot=255;p->motion.next=0;
    bridge_auxiliary_clear();memset(p->held_run,0,sizeof p->held_run);
    preset_trigger_reset(&p->triggers,NULL,NULL); // No hold-release return or other automatic action.
    p->bypass.rearm=true;
    if(!++p->bypass.generation)++p->bypass.generation;
}
uint8_t preset_bypass_configure(preset_runtime_t*p,uint8_t mask){
    if(p->maintenance_pending)return PRESET_BUSY;
    if(!preset_bypass_mask_valid(mask))return 2;
    if(p->bypass.mask==mask)return 0;
    bypass_cancel(p,PRESET_REASON_BYPASS);p->bypass.mask=mask;p->bypass.raw=0;
    p->bypass.active=p->bypass.input_valid=false;p->bypass.epoch=0;p->bypass.rearm=mask!=0;
    preset_config_changed(p);bridge_bypass_set(preset_bypass_inhibited(p));return 0;
}
void preset_bypass_observe(preset_runtime_t*p,uint8_t mask,uint32_t epoch,bool valid){
    if(!p->bypass.mask)return;
    mask&=31;
    if(!valid){
        if(p->bypass.input_valid||p->bypass.active)bypass_cancel(p,PRESET_REASON_INPUT_RESET);
        p->bypass.active=p->bypass.input_valid=false;p->bypass.raw=0;p->bypass.rearm=true;
    }else{
        bool active=(mask&p->bypass.mask)!=0;
        if(epoch!=p->bypass.epoch){bypass_cancel(p,PRESET_REASON_INPUT_RESET);p->bypass.epoch=epoch;}
        if(active!=p->bypass.active){bypass_cancel(p,PRESET_REASON_BYPASS);if(active)++p->bypass.entries;}
        p->bypass.raw=mask;p->bypass.input_valid=true;p->bypass.active=active;
    }
    bridge_bypass_set(preset_bypass_inhibited(p));
}
void preset_bypass_snapshot(preset_runtime_t*p,const physical_snapshot_t*s,bool valid){
    if(!p->bypass.mask)return;
    if(valid){
        unsigned index=0;while((1u<<index)!=p->bypass.mask)++index;
        const uint32_t count=s->button_presses[index];
        // A complete raw press/release may be hidden by a newer snapshot or
        // physical button transition backlog. Cancel before any HID prepare.
        if(s->epoch==p->bypass.epoch&&count!=p->bypass.press_count&&!(s->buttons&p->bypass.mask)){
            bypass_cancel(p,PRESET_REASON_BYPASS);++p->bypass.entries;
        }
        p->bypass.press_count=count;
    }
    preset_bypass_observe(p,valid?(uint8_t)s->buttons:0,valid?s->epoch:0,
                          valid&&s->connected&&s->target_generation==event_bus_target_generation());
}
static void stop(preset_runtime_t *p){preset_motion_cancel(p,PRESET_REASON_STOPPED,false);}
static void changed(preset_runtime_t *p,uint8_t slot){
    if(preset_runtime_busy(p)&&(p->motion.slot==slot||p->motion.target_slot==slot)){
        preset_motion_cancel(p,PRESET_REASON_CHANGED,false);p->last_error=PRESET_STALE;
    }
}
static void publish(preset_runtime_t *p,uint8_t slot,const uint8_t *image,size_t size,const trajectory_view_t *view){
    changed(p,slot);
    memcpy(p->slots[slot],image,size);p->views[slot]=*view;p->views[slot].image=p->slots[slot];p->sizes[slot]=(uint32_t)size;
    preset_config_changed(p);
}
uint8_t preset_apply(preset_runtime_t *p,uint8_t slot,const uint8_t *image,size_t size){
    if(p->maintenance_pending)return PRESET_BUSY;
    if(slot>=PRESET_MODE_COUNT||size>PRESET_SLOT_BYTES)return 2;
    trajectory_view_t view;if(!trajectory_open(&view,image,size))return PRESET_BAD_CRC;
    publish(p,slot,image,size,&view);return 0;
}
uint8_t preset_upload_begin(preset_runtime_t *p,uint8_t slot,uint32_t size,uint32_t crc,uint32_t token){
    if(p->maintenance_pending)return PRESET_BUSY;
    if(slot>=PRESET_MODE_COUNT||size<41||size>PRESET_SLOT_BYTES||!token)return 2;
    if(p->upload_state==1||p->upload_state==2)return PRESET_BUSY;
    p->upload_slot=slot;p->upload_size=size;p->upload_token=token;p->upload_expected_crc=crc;
    p->upload_received=0;p->upload_crc=UINT32_MAX;p->upload_state=1;p->last_error=0;return 0;
}
uint8_t preset_upload_chunk(preset_runtime_t *p,uint32_t token,uint32_t offset,const uint8_t *data,size_t size){
    if(p->upload_state!=1||token!=p->upload_token||offset!=p->upload_received||!size||size>42||size>p->upload_size-offset)return 2;
    memcpy(p->upload+offset,data,size);
    for(size_t i=0;i<size;i++){
        p->upload_crc^=(offset+i>=28&&offset+i<32)?0:data[i];
        for(unsigned j=0;j<8;j++)p->upload_crc=(p->upload_crc>>1)^((0u-(p->upload_crc&1u))&UINT32_C(0xedb88320));
    }
    p->upload_received+=(uint32_t)size;return 0;
}
uint8_t preset_upload_commit(preset_runtime_t *p,uint32_t token){
    if(p->upload_state!=1||token!=p->upload_token||p->upload_received!=p->upload_size)return 2;
    if(~p->upload_crc!=p->upload_expected_crc||r32(p->upload+28)!=p->upload_expected_crc||!trajectory_header(&p->upload_view,p->upload,p->upload_size)){
        p->upload_state=3;p->last_error=PRESET_BAD_CRC;return PRESET_BAD_CRC;
    }
    p->validate_block=0;p->upload_state=2;return 0;
}
void preset_upload_abort(preset_runtime_t *p){p->upload_state=0;p->upload_token=0;}
static uint8_t mode_slot(const preset_runtime_t *p,uint8_t command,uint8_t slot){
    if(command==3||command==4){
        unsigned next=p->selected;
        for(unsigned n=0;n<PRESET_MODE_COUNT;n++){
            next=(next+(command==3?1:PRESET_MODE_COUNT-1))%PRESET_MODE_COUNT;
            if(p->sizes[next])return (uint8_t)next;
        }
        return 255;
    }
    return slot;
}
uint8_t preset_control(preset_runtime_t *p,uint8_t command,uint8_t slot,uint64_t now){
    if(p->maintenance_pending)return PRESET_BUSY;
    if(preset_bypass_inhibited(p)&&command!=1&&command!=5)return PRESET_BYPASS;
    if(command==1)return preset_execute(p,PRESET_EXEC_STOP,0,0,0,0,now);
    if(command==5){
        if(slot>=PRESET_MODE_COUNT)return 2;
        changed(p,slot);
        p->sizes[slot]=0;memset(&p->views[slot],0,sizeof p->views[slot]);preset_config_changed(p);
        if(p->selected==slot){
            p->selected=0;
            for(unsigned i=0;i<PRESET_MODE_COUNT;i++)if(p->sizes[i]){p->selected=(uint8_t)i;break;}
        }
        return 0;
    }
    slot=mode_slot(p,command,slot);
    if(slot>=PRESET_MODE_COUNT||!p->sizes[slot])return 9;
    if(command>4)return 2;
    if(command==0&&!p->ready)return 9;
    if(command==0)return preset_motion_start(p,slot,now,false);
    // Legacy J4 selection stops without starting the chosen trajectory.
    stop(p);p->selected=slot;
    return 0;
}
static void trigger_action(void*ctx,unsigned index,const preset_trigger_record_t*b,bool release){
    preset_runtime_t*p=ctx;
    if(release){
        ++p->trigger_released;
        if(p->held_run[index]&&p->held_run[index]==p->run_serial)(void)preset_control(p,1,0,p->now_us);
        p->held_run[index]=0;return;
    }
    ++p->trigger_activated;uint8_t result;
    if(b->action==5){
        if(!p->sizes[b->target]){p->last_error=9;return;}
        bool enable=!p->mode_enabled||p->selected!=b->target;
        stop(p);memset(p->held_run,0,sizeof p->held_run);p->motion.held=false;
        p->selected=b->target;p->mode_enabled=enable;
        preset_trigger_set_scope(&p->triggers,p->selected,p->mode_enabled);return;
    }
    if(b->action==6||b->action==7){
        if(!p->mode_enabled)return;
        if(b->action==7&&p->selected!=b->target)return;
        if(b->action==6){
            // A mode's own execute binding replaces the shared execute input.
            for(unsigned i=0;i<p->triggers.count;i++){
                const preset_trigger_record_t*own=&p->triggers.records[i];
                if(own->enabled&&own->action==7&&own->target==p->selected)return;
            }
        }
        result=preset_motion_start(p,p->selected,p->now_us,b->behavior==1);
        if(result)p->last_error=result;else if(b->behavior==1)p->held_run[index]=p->run_serial;
        return;
    }
    // APP stop bindings name a trajectory; unrelated active slots keep running.
    if(b->action==4&&p->active_slot!=b->target)return;
    if(b->action==3&&b->behavior==2&&p->player.running&&p->active_slot==b->target)result=preset_control(p,1,0,p->now_us);
    else if(b->action==3)result=preset_motion_start(p,b->target,p->now_us,b->behavior==1);
    else if(b->action<3&&preset_runtime_busy(p)){
        uint8_t slot=mode_slot(p,(uint8_t)(2+b->action),b->target);
        result=preset_execute(p,PRESET_EXEC_SWITCH,slot,0,0,0,p->now_us);
    }else {const uint8_t commands[]={2,3,4,0,1};result=preset_control(p,commands[b->action],b->target,p->now_us);}
    if(result)p->last_error=result;
    else if(b->behavior==1)p->held_run[index]=p->run_serial;
}
void preset_runtime_observe(preset_runtime_t*p,uint8_t mask,uint32_t epoch,uint64_t now){
    if(preset_mode_controlled(p)&&epoch!=p->triggers.epoch)preset_runtime_physical_reset(p);
    // Also catch a very short bypass press retained in the physical FIFO.
    if(p->bypass.mask&&(mask&p->bypass.mask)&&!p->bypass.active)bypass_cancel(p,PRESET_REASON_BYPASS);
    if(preset_bypass_inhibited(p)||p->bypass.rearm)return;
    preset_trigger_observe(&p->triggers,mask,epoch,now);
}
void preset_runtime_observe_wheel(preset_runtime_t*p,uint8_t mask,int32_t wheel,uint32_t epoch,uint64_t now){
    preset_runtime_observe(p,mask,epoch,now);
    if(!preset_bypass_inhibited(p)&&!p->bypass.rearm){
        preset_trigger_set_scope(&p->triggers,p->selected,p->mode_enabled);
        preset_trigger_wheel(&p->triggers,wheel,epoch,now);
    }
}
void preset_runtime_physical_reset(preset_runtime_t*p){
    if(preset_mode_controlled(p)){
        p->mode_enabled=false;preset_motion_cancel(p,PRESET_REASON_INPUT_RESET,false);
        memset(p->held_run,0,sizeof p->held_run);preset_trigger_reset(&p->triggers,NULL,NULL);
    }
    if(p->bypass.mask){bypass_cancel(p,PRESET_REASON_INPUT_RESET);p->bypass.active=p->bypass.input_valid=false;p->bypass.raw=0;bridge_bypass_set(true);}
    else preset_trigger_reset(&p->triggers,trigger_action,p);
}
void preset_runtime_tick(preset_runtime_t *p,uint64_t now,bool ready){
    p->now_us=now;p->ready=ready;bridge_ai_set_ready(ready);
    // Gate ordinary trigger ticks before they can select/start anything, even
    // when the accepted physical ledger is blocked behind an in-flight report.
    if(p->bypass.mask){
        physical_snapshot_t s;bool valid=event_bus_snapshot(&s);
        preset_bypass_snapshot(p,&s,valid);
    }
    const uint32_t generation=event_bus_target_generation();
    if((!ready&&p->motion.known)||generation!=p->target_generation){preset_motion_cancel(p,PRESET_REASON_TARGET_RESET,true);preset_runtime_physical_reset(p);}
    p->target_generation=generation;
    if(p->upload_state==2){
        if(!trajectory_block_valid(&p->upload_view,p->validate_block)){p->last_error=PRESET_BAD_CRC;p->upload_state=3;}
        else if(++p->validate_block==p->upload_view.blocks){
            publish(p,p->upload_slot,p->upload,p->upload_size,&p->upload_view);
            p->last_apply_token=p->upload_token;p->upload_state=0;
        }
    }
    if(p->bypass.rearm&&p->bypass.input_valid&&!p->bypass.raw&&!event_bus_depth()){
        preset_trigger_reset(&p->triggers,NULL,NULL);p->bypass.rearm=false;
    }
    if(ready&&!preset_bypass_inhibited(p)){
        if(!p->bypass.rearm){
            preset_trigger_set_scope(&p->triggers,p->selected,p->mode_enabled);
            preset_trigger_tick(&p->triggers,now,trigger_action,p);
        }
        preset_motion_tick(p,now);
    }
}
void preset_runtime_output(preset_runtime_t *p,const mouse_report_t *r){p->output_dx+=r->x;p->output_dy+=r->y;++p->output_reports;}
