#include <limits.h>
#include "preset_perturb.h"
static uint16_t r16(const uint8_t*p){return (uint16_t)(p[0]|((uint16_t)p[1]<<8));}
static uint32_t r32(const uint8_t*p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static void w16(uint8_t*p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static void w32(uint8_t*p,uint32_t v){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(i*8));}
preset_perturb_config_t preset_perturb_default(void){
    return (preset_perturb_config_t){false,false,2,3,3,250,8,0};
}
bool preset_perturb_valid(const preset_perturb_config_t*c){
    return c&&c->smoothness<=2&&c->amplitude_x<=1000&&c->amplitude_y<=1000&&
           c->period_ms>=40&&c->max_deviation<=1000;
}
bool preset_perturb_decode(preset_perturb_config_t*c,const uint8_t*p){
    if(!c||!p||p[0]!=1||p[1]>1||p[2]>1)return false;
    preset_perturb_config_t v={p[1]!=0,p[2]!=0,p[3],r16(p+4),r16(p+6),r16(p+8),r16(p+10),r32(p+12)};
    if(!preset_perturb_valid(&v))return false;
    *c=v;return true;
}
void preset_perturb_encode(uint8_t*p,const preset_perturb_config_t*c){
    p[0]=1;p[1]=(uint8_t)c->enabled;p[2]=(uint8_t)c->fixed_seed_enabled;p[3]=c->smoothness;
    w16(p+4,c->amplitude_x);w16(p+6,c->amplitude_y);w16(p+8,c->period_ms);w16(p+10,c->max_deviation);w32(p+12,c->seed);
}
static uint32_t hash32(uint32_t x){
    x^=x>>16;x*=UINT32_C(0x7feb352d);x^=x>>15;x*=UINT32_C(0x846ca68b);return x^(x>>16);
}
uint32_t preset_perturb_seed(const preset_perturb_config_t*c,uint32_t*sequence,uint64_t now){
    if(!*sequence)*sequence=hash32((uint32_t)now^(uint32_t)(now>>32)^UINT32_C(0xa341316c));
    *sequence+=UINT32_C(0x9e3779b9);
    return c->fixed_seed_enabled?c->seed:hash32(*sequence);
}
static int32_t abs32(int32_t v){return v<0?-v:v;}
static void bound(int32_t*x,int32_t*y,uint16_t limit){
    int32_t sum=abs32(*x)+abs32(*y);
    if(sum>limit){*x=*x*limit/sum;*y=*y*limit/sum;}
}
static void knot(const preset_perturb_config_t*c,uint32_t seed,uint32_t index,int32_t*x,int32_t*y){
    uint32_t base=seed+index*UINT32_C(0x9e3779b9);
    *x=(int32_t)(hash32(base^UINT32_C(0x68bc21eb))%(2u*c->amplitude_x+1u))-c->amplitude_x;
    *y=(int32_t)(hash32(base^UINT32_C(0x02e5be93))%(2u*c->amplitude_y+1u))-c->amplitude_y;
    bound(x,y,c->max_deviation);
}
static int64_t eased(uint32_t tick,uint32_t total,uint8_t shape){
    const int64_t q=((int64_t)tick<<16)/total;
    if(!shape)return q;
    const int64_t q2=q*q>>16,q3=q2*q>>16;
    if(shape==1)return 3*q2-2*q3;
    const int64_t q4=q3*q>>16,q5=q4*q>>16;
    int64_t result=6*q5-15*q4+10*q3;
    return result<0?0:result>65536?65536:result;
}
static int32_t rounded(int64_t q){return (int32_t)(q<0?-((-q+32768)/65536):(q+32768)/65536);}
void preset_perturb_offset(const preset_perturb_config_t*c,uint32_t seed,uint32_t tick,uint32_t total,int16_t*x,int16_t*y){
    *x=*y=0;
    if(!c||!c->enabled||!c->max_deviation||!tick||tick>=total||!preset_perturb_valid(c))return;
    int32_t ax,ay,bx,by;const uint32_t i=tick/c->period_ms;
    knot(c,seed,i,&ax,&ay);knot(c,seed,i+1,&bx,&by);
    int64_t q=eased(tick%c->period_ms,c->period_ms,c->smoothness);
    int64_t qx=(int64_t)ax*65536+(bx-ax)*q,qy=(int64_t)ay*65536+(by-ay)*q;
    uint32_t ramp=total/2;if(ramp>c->period_ms)ramp=c->period_ms;
    uint32_t edge=tick<total-tick?tick:total-tick;
    if(edge<ramp){int64_t gate=eased(edge,ramp,2);qx=qx*gate/65536;qy=qy*gate/65536;}
    int32_t ox=rounded(qx),oy=rounded(qy);bound(&ox,&oy,c->max_deviation);
    *x=(int16_t)ox;*y=(int16_t)oy;
}
bool preset_perturb_delta(const preset_perturb_config_t*c,uint32_t seed,uint32_t tick,uint32_t total,int16_t*x,int16_t*y){
    int16_t ax,ay,bx,by;preset_perturb_offset(c,seed,tick,total,&bx,&by);
    preset_perturb_offset(c,seed,tick?tick-1:0,total,&ax,&ay);
    int32_t dx=(int32_t)*x+bx-ax,dy=(int32_t)*y+by-ay;
    if(dx<INT16_MIN||dx>INT16_MAX||dy<INT16_MIN||dy>INT16_MAX)return false;
    *x=(int16_t)dx;*y=(int16_t)dy;return true;
}
