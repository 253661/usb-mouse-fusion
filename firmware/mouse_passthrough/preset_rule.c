#include <string.h>
#include "preset_rule.h"
static uint32_t r32(const uint8_t*p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static void w32(uint8_t*p,uint32_t v){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i));}
preset_rule_t preset_rule_default(void){
    preset_rule_t r={0};r.repeat_count=1;r.duration_ms=1000;r.return_ms=250;r.transition_ms=250;
    r.return_ease=PRESET_EASE_SMOOTH;r.transition_action=1;r.transition_ease=PRESET_EASE_SMOOTH;return r;
}
bool preset_rule_valid(const preset_rule_t*r){
    return r&&r->run_mode<=PRESET_RUN_HOLD&&r->stop_action<=1&&r->loop_join<=1&&r->return_ease<=3&&
        r->repeat_count>=1&&r->repeat_count<=1000000&&r->duration_ms>=1&&r->duration_ms<=86400000&&
        r->return_ms>=1&&r->return_ms<=60000&&r->transition_ms<=60000&&r->transition_action<=1&&r->transition_ease<=3&&
        r->origin_x>=-1000000000&&r->origin_x<=1000000000&&r->origin_y>=-1000000000&&r->origin_y<=1000000000;
}
void preset_rule_encode(uint8_t*p,const preset_rule_t*r){
    p[0]=r->run_mode;p[1]=r->stop_action;p[2]=r->loop_join;p[3]=r->return_ease;
    w32(p+4,r->repeat_count);w32(p+8,r->duration_ms);p[12]=(uint8_t)r->return_ms;p[13]=(uint8_t)(r->return_ms>>8);
    p[14]=(uint8_t)r->transition_ms;p[15]=(uint8_t)(r->transition_ms>>8);p[16]=r->transition_action;p[17]=r->transition_ease;p[18]=1;p[19]=0;
    w32(p+20,(uint32_t)r->origin_x);w32(p+24,(uint32_t)r->origin_y);
}
bool preset_rule_decode(preset_rule_t*r,const uint8_t*p){
    if(p[18]!=1||p[19])return false;
    *r=(preset_rule_t){.run_mode=p[0],.stop_action=p[1],.loop_join=p[2],.return_ease=p[3],.repeat_count=r32(p+4),.duration_ms=r32(p+8),
        .return_ms=(uint16_t)(p[12]|((uint16_t)p[13]<<8)),.transition_ms=(uint16_t)(p[14]|((uint16_t)p[15]<<8)),
        .transition_action=p[16],.transition_ease=p[17],.origin_x=(int32_t)r32(p+20),.origin_y=(int32_t)r32(p+24)};
    return preset_rule_valid(r);
}
int64_t preset_eased_position(int64_t delta,uint32_t tick,uint32_t total,uint8_t ease){
    if(!tick)return 0;
    if(tick>=total)return delta;
    // Q16, bounded multiplications. The motion controller caps coordinate range.
    uint64_t t=((uint64_t)tick<<16)/total,w=t;
    if(ease==PRESET_EASE_IN)w=(t*t)>>16;
    else if(ease==PRESET_EASE_OUT){uint64_t u=65536-t;w=65536-((u*u)>>16);}
    else if(ease==PRESET_EASE_SMOOTH)w=((t*t)>>16)*(196608-2*t)>>16;
    int64_t v=delta*(int64_t)w;return v>=0?(v+32768)/65536:-((-v+32768)/65536);
}
