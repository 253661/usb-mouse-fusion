#include <string.h>
#include "preset_trigger.h"
#include "trajectory.h"
void preset_trigger_init(preset_trigger_t*t){memset(t,0,sizeof *t);}
void preset_trigger_decode(preset_trigger_record_t*r,const uint8_t*p){
    r->mask=p[0];r->gesture=p[1];r->action=p[2];r->target=p[3];r->behavior=p[4];r->policy=p[5];
    r->window_ms=(uint16_t)(p[6]|((uint16_t)p[7]<<8));r->hold_ms=(uint16_t)(p[8]|((uint16_t)p[9]<<8));r->debounce_ms=p[10];r->enabled=p[11];
}
void preset_trigger_encode(uint8_t*p,const preset_trigger_record_t*r){
    p[0]=r->mask;p[1]=r->gesture;p[2]=r->action;p[3]=r->target;p[4]=r->behavior;p[5]=r->policy;
    p[6]=(uint8_t)r->window_ms;p[7]=(uint8_t)(r->window_ms>>8);p[8]=(uint8_t)r->hold_ms;p[9]=(uint8_t)(r->hold_ms>>8);p[10]=r->debounce_ms;p[11]=r->enabled;
}
uint8_t preset_trigger_configure(preset_trigger_t*t,const preset_trigger_record_t*r,unsigned n){
    if(n>PRESET_TRIGGER_MAX)return 2;
    for(unsigned i=0;i<n;i++){
        const preset_trigger_record_t*b=&r[i];
        if(b->policy)return 11; // suppression has no implementation
        if(!b->mask||b->mask>127||(b->mask&96)==96||b->gesture>2||b->action>7||b->target>=PRESET_MODE_COUNT||b->behavior>2||b->enabled>1||
           b->window_ms<10||b->window_ms>2000||b->hold_ms<50||b->hold_ms>5000||b->debounce_ms<1||b->debounce_ms>50||
           b->debounce_ms>b->window_ms||b->debounce_ms>=b->hold_ms||
           (b->behavior==1&&(b->gesture==0||(b->action!=3&&b->action!=6&&b->action!=7)))||
           (b->behavior==2&&b->action!=3)||(b->action==5&&b->behavior)||
           ((b->mask&96)&&(b->gesture==1||b->behavior)))return 2;
        for(unsigned j=0;j<i;j++)if(b->enabled&&r[j].enabled&&b->mask==r[j].mask&&(b->gesture==r[j].gesture||(b->mask&96))){
            bool scoped=b->action==7&&r[j].action==7&&b->target!=r[j].target;
            bool shared=(b->action==7&&r[j].action==6)||(b->action==6&&r[j].action==7);
            if(!scoped&&!shared)return 2;
        }
    }
    preset_trigger_init(t);if(n)memcpy(t->records,r,n*sizeof *r);t->count=(uint8_t)n;return 0;
}
static void reset_state(preset_trigger_t*t){
    for(unsigned i=0;i<t->count;i++)if(t->states[i].active)t->release_pending|=(uint16_t)(1u<<i);
    memset(t->states,0,sizeof t->states);memset(t->down_at,0,sizeof t->down_at);memset(t->up_at,0,sizeof t->up_at);
    t->raw=t->pressed=t->pending=t->consumed=t->wheel_count=0;
}
static void releases(preset_trigger_t*t,preset_trigger_callback_t cb,void*ctx){
    for(unsigned i=0;i<t->count;i++)if(t->release_pending&(1u<<i)){if(cb)cb(ctx,i,&t->records[i],true);}
    t->release_pending=0;
}
void preset_trigger_reset(preset_trigger_t*t,preset_trigger_callback_t cb,void*ctx){reset_state(t);releases(t,cb,ctx);}
void preset_trigger_observe(preset_trigger_t*t,uint8_t mask,uint32_t epoch,uint64_t now){
    if(epoch!=t->epoch){reset_state(t);t->epoch=epoch;}
    mask&=31;uint8_t changed=mask^t->raw;t->raw=mask;
    for(unsigned k=0;k<5;k++)if(changed&(1u<<k)){
        unsigned debounce=1;for(unsigned i=0;i<t->count;i++)if(t->records[i].enabled&&(t->records[i].mask&(1u<<k))&&t->records[i].debounce_ms>debounce)debounce=t->records[i].debounce_ms;
        t->edges[k]=now;t->due[k]=now+debounce*1000;t->pending|=(uint8_t)(1u<<k);
    }
}
void preset_trigger_set_scope(preset_trigger_t*t,uint8_t selected,bool enabled){t->scope_set=true;t->selected=selected;t->scope_enabled=enabled;}
static bool allowed(const preset_trigger_t*t,const preset_trigger_record_t*b){
    if(b->action==7)return t->scope_set&&t->scope_enabled&&t->selected==b->target;
    if(b->action!=6||!t->scope_set)return true;
    if(!t->scope_enabled)return false;
    for(unsigned i=0;i<t->count;i++)if(t->records[i].enabled&&t->records[i].action==7&&t->records[i].target==t->selected)return false;
    return true;
}
void preset_trigger_wheel(preset_trigger_t*t,int32_t wheel,uint32_t epoch,uint64_t now){
    if(epoch!=t->epoch){reset_state(t);t->epoch=epoch;}
    if(!wheel||t->wheel_count==PRESET_TRIGGER_WHEEL_QUEUE)return;
    preset_trigger_wheel_t*w=&t->wheels[t->wheel_count++];w->at=now;w->mask=wheel>0?32:64;w->buttons=t->raw;
    // Capture stability at the event, before a queued release changes raw state.
    w->stable_buttons=t->pressed;
    for(unsigned k=0;k<5;k++)if((t->pending&(1u<<k))&&now>=t->due[k]){
        if(t->raw&(1u<<k))w->stable_buttons|=(uint8_t)(1u<<k);
        else w->stable_buttons&=(uint8_t)~(1u<<k);
    }
}
static void wheel_tick(preset_trigger_t*t,preset_trigger_callback_t cb,void*ctx){
    uint8_t mode_keys=0;
    for(unsigned i=0;i<t->count;i++)if(t->records[i].enabled&&t->records[i].action==5)mode_keys|=t->records[i].mask&31;
    for(unsigned n=0;n<t->wheel_count;n++){
        const preset_trigger_wheel_t*w=&t->wheels[n];bool consumed=false;
        for(unsigned pass=0;pass<3;pass++)for(unsigned i=0;i<t->count;i++){
            const preset_trigger_record_t*b=&t->records[i];uint8_t keys=b->mask&31;
            unsigned priority=b->action==5?0:b->gesture==0?2:1;
            if(!b->enabled||priority!=pass||!(b->mask&w->mask)||!allowed(t,b)||consumed)continue;
            if(b->action==5){
                if((w->buttons&mode_keys)!=keys||(w->stable_buttons&mode_keys)!=keys)continue;
            }else if(keys!=w->buttons||keys!=t->pressed||keys!=t->raw)continue;
            bool timely=true;
            bool modifier=b->action==5&&(keys==8||keys==16);
            if(!modifier)for(unsigned k=0;k<5;k++)if(keys&(1u<<k))if(t->down_at[k]>w->at||w->at-t->down_at[k]>(uint64_t)b->window_ms*1000)timely=false;
            if(timely){t->consumed|=keys;if(cb)cb(ctx,i,b,false);consumed=true;}
        }
    }
    t->wheel_count=0;
}
void preset_trigger_tick(preset_trigger_t*t,uint64_t now,preset_trigger_callback_t cb,void*ctx){
    releases(t,cb,ctx);
    for(unsigned k=0;k<5;k++)if((t->pending&(1u<<k))&&now>=t->due[k]){
        uint8_t bit=(uint8_t)(1u<<k);t->pending&=(uint8_t)~bit;
        if(t->raw&bit){
            // Side-button consumption belongs to one press, even with left held.
            if((bit&24)&&!(t->pressed&bit))t->consumed&=(uint8_t)~bit;
            t->pressed|=bit;t->down_at[k]=t->edges[k];
        }else{t->pressed&=(uint8_t)~bit;t->up_at[k]=t->edges[k];}
    }
    // Hold/chord first: consumption prevents a later click firing on release.
    uint8_t mode_keys=0;
    for(unsigned i=0;i<t->count;i++)if(t->records[i].enabled&&t->records[i].action==5)mode_keys|=t->records[i].mask;
    // Confirm a mature RIGHT release before any later wheel gesture. Its side
    // click is already consumed; side-only releases must wait until wheel_tick.
    for(unsigned i=0;i<t->count;i++){
        const preset_trigger_record_t*b=&t->records[i];preset_trigger_state_t*s=&t->states[i];
        if(!b->enabled||b->action!=5||b->gesture!=0||(b->mask!=10&&b->mask!=18))continue;
        uint8_t modifier=b->mask&24;unsigned k=modifier==8?3:4;
        bool extra=(t->pressed&mode_keys&~b->mask)!=0;
        bool exact=(t->pressed&b->mask)==b->mask&&!extra;
        if(s->state==1){
            if(extra){s->state=3;}
            else if(!(t->pressed&2)){
                if(!(t->raw&2)&&t->up_at[1]>=s->start&&t->up_at[1]-s->start<(uint64_t)b->hold_ms*1000){if(cb)cb(ctx,i,b,false);}
                s->state=0;
            }
        }else if(!(t->pressed&2)){s->state=0;}
        else if(!s->state&&exact){
            if((t->raw&mode_keys)==b->mask&&t->down_at[k]+(uint64_t)b->debounce_ms*1000<=t->down_at[1]){
                s->state=1;s->start=t->down_at[1];t->consumed|=modifier;
            }else s->state=3;
        }
    }
    // Consume queued wheel combinations before confirming a side-button release.
    wheel_tick(t,cb,ctx);
    for(unsigned pass=0;pass<3;pass++)for(unsigned i=0;i<t->count;i++){
        const preset_trigger_record_t*b=&t->records[i];preset_trigger_state_t*s=&t->states[i];
        unsigned priority=b->action==5?0:b->gesture==0?2:1;
        if(!b->enabled||priority!=pass||(b->mask&96))continue;
        bool extra=b->action==5?(t->pressed&mode_keys&~b->mask)!=0:(t->pressed&~b->mask)!=0;
        bool exact=b->action==5?(t->pressed&b->mask)==b->mask&&!extra:t->pressed==b->mask;
        bool any=(t->pressed&b->mask)!=0;
        if(b->action==5&&b->gesture==0&&(b->mask==10||b->mask==18))continue;
        if(!allowed(t,b)){
            if(s->active){if(cb)cb(ctx,i,b,true);s->active=false;}
            s->state=any?3:0;continue;
        }
        if(s->state==2){
            if(s->active&&!exact){if(cb)cb(ctx,i,b,true);s->active=false;}
            if(!any)s->state=0;
            continue;
        }
        if(s->state==3){if(!any)s->state=0;continue;}
        if(extra&&any){s->state=3;continue;}
        if(exact){
            uint64_t first=UINT64_MAX,last=0;
            for(unsigned k=0;k<5;k++)if(b->mask&(1u<<k)){if(t->down_at[k]<first)first=t->down_at[k];if(t->down_at[k]>last)last=t->down_at[k];}
            if(last-first>(uint64_t)b->window_ms*1000){s->state=3;continue;}
            if(!s->state){s->state=1;s->start=last;}
            if((b->gesture==2||(b->gesture==1&&now-s->start>=(uint64_t)b->hold_ms*1000))&&
               (b->action==5?(t->raw&mode_keys)==b->mask:t->raw==b->mask)){
                bool emit=!(t->consumed&b->mask);if(emit){if(cb)cb(ctx,i,b,false);t->consumed|=b->mask;}
                s->state=2;s->active=emit&&b->behavior==1;
            }
        }else if(!any){
            uint64_t release=0;for(unsigned k=0;k<5;k++)if((b->mask&(1u<<k))&&t->up_at[k]>release)release=t->up_at[k];
            if(s->state==1&&b->gesture==0&&release>=s->start&&release-s->start<(uint64_t)b->hold_ms*1000&&!(t->consumed&b->mask)){
                if(cb)cb(ctx,i,b,false);
                t->consumed|=b->mask;
            }
            s->state=0;s->active=false;
        }
    }
    if(!t->pressed)t->consumed=0;
}
