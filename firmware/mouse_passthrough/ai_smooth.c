#include <string.h>
#include "ai_smooth.h"
void ai_smooth_reset(ai_smooth_t *s){memset(s,0,sizeof *s);s->last_cycle=UINT64_MAX;}
bool ai_smooth_begin(ai_smooth_t *s,int16_t x,int16_t y,uint64_t received_us,uint64_t expires_us,uint16_t duration_ms){
    if(duration_ms<20||duration_ms>40)return false;
    const uint64_t first=received_us/1000+1;
    if(first>UINT64_MAX/1000-duration_ms||(first+duration_ms)*1000>expires_us)return false;
    ai_smooth_reset(s);s->x=x;s->y=y;s->ticks=duration_ms;s->start_cycle=first;s->active=true;return true;
}
static int32_t portion(int16_t total,uint32_t index,uint32_t ticks){
    const int32_t n=(int32_t)total*(int32_t)index;
    return n<0?-((-n+(int32_t)ticks/2)/(int32_t)ticks):(n+(int32_t)ticks/2)/(int32_t)ticks;
}
bool ai_smooth_step(ai_smooth_t *s,uint64_t now_us,int16_t *x,int16_t *y){
    const uint64_t cycle=now_us/1000;
    if(!s->active||cycle<s->start_cycle||cycle==s->last_cycle)return false;
    const uint64_t index=cycle-s->start_cycle;
    if(index>=s->ticks){s->active=false;return false;}
    s->last_cycle=cycle;
    *x=(int16_t)(portion(s->x,(uint32_t)index+1,s->ticks)-portion(s->x,(uint32_t)index,s->ticks));
    *y=(int16_t)(portion(s->y,(uint32_t)index+1,s->ticks)-portion(s->y,(uint32_t)index,s->ticks));
    if(index+1==s->ticks)s->active=false;
    return true;
}
uint32_t const fusion_ai_smooth_contract[]={0x41495331,1,0x100000,0x18,0x19,34,20,40,1000,1};
