#include <string.h>
#include "trajectory.h"
static uint16_t r16(const uint8_t *p){return (uint16_t)(p[0]|((uint16_t)p[1]<<8));}
static uint32_t r32(const uint8_t *p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
uint32_t trajectory_crc32(const uint8_t *data,size_t size){
    uint32_t crc=UINT32_MAX;
    for(size_t i=0;i<size;i++){
        crc^=(i>=28&&i<32)?0:data[i];
        for(unsigned j=0;j<8;j++)crc=(crc>>1)^((0u-(crc&1u))&UINT32_C(0xedb88320));
    }
    return ~crc;
}
static bool block_valid(const uint8_t *p,size_t size,unsigned count){
    if(!size)return false;
    switch(p[0]){
    case 0:return size==1;
    case 1:return size==5;
    case 2:return size==1+2*count;
    case 3:return size==1+4*count;
    case 4:case 5:{
        const unsigned width=p[0]==4?3:5;unsigned samples=0;
        if(size<=1||(size-1)%width)return false;
        for(size_t i=1;i<size;i+=width){samples+=(unsigned)p[i]+1;if(samples>count)return false;}
        return samples==count;
    }
    default:return false;
    }
}
bool trajectory_header(trajectory_view_t *view,const uint8_t *p,size_t size){
    if(!view||!p||size<41||size>UINT32_MAX||r32(p+8)!=1000||r32(p+24)!=1)return false;
    const uint32_t n=r32(p+12),blocks=r32(p+16),bytes=r32(p+20);
    if(!n||n>TRAJECTORY_MAX_SAMPLES)return false;
    if(!memcmp(p,"MFT2",4)){
        if(size<88||r16(p+4)!=2||r16(p+6)!=64||blocks<2||blocks>TRAJECTORY_MAX_SAMPLES+1||
           bytes!=(uint64_t)blocks*12||size!=64+(uint64_t)bytes)return false;
        const uint16_t target=r16(p+48);if(target!=5&&target!=10)return false;
        for(unsigned i=50;i<64;i++)if(p[i])return false;
        preset_perturb_config_t config;if(!preset_perturb_decode(&config,p+32))return false;
        *view=(trajectory_view_t){.image=p,.size=(uint32_t)size,.samples=n,.blocks=blocks,.data_start=64,
            .format_version=2,.sampling_target_ms=target,.perturb=config};return true;
    }
    if(memcmp(p,"MFT1",4)||r16(p+4)!=1||r16(p+6)!=32)return false;
    if(!n||n>TRAJECTORY_MAX_SAMPLES||blocks!=(n+255)/256)return false;
    const size_t start=32+4*(size_t)(blocks+1);
    if(start>size||bytes!=size-start||r32(p+32)!=0||r32(p+32+4*blocks)!=bytes)return false;
    *view=(trajectory_view_t){.image=p,.size=(uint32_t)size,.samples=n,.blocks=blocks,.data_start=(uint32_t)start,
        .format_version=1,.perturb=preset_perturb_default()};return true;
}
bool trajectory_block_valid(const trajectory_view_t *v,uint32_t i){
    if(!v||i>=v->blocks)return false;
    if(v->format_version==2){
        const uint8_t*p=v->image+64+12*i;uint32_t t=r32(p);
        if(!i)return t==0&&r32(p+4)==0&&r32(p+8)==0;
        const uint8_t*a=p-12;uint32_t before=r32(a);
        if(t<=before||t>v->samples||(i==v->blocks-1&&t!=v->samples))return false;
        // Every rounded integer increment must fit signed16; validate without walking each ms.
        for(unsigned axis=4;axis<=8;axis+=4){
            int64_t d=(int64_t)(int32_t)r32(p+axis)-(int32_t)r32(a+axis);
            uint64_t magnitude=(uint64_t)(d<0?-d:d),span=t-before;
            if((magnitude+span-1)/span>(uint64_t)(d<0?32768:32767))return false;
        }
        return true;
    }
    const uint32_t a=r32(v->image+32+4*i),b=r32(v->image+36+4*i),bytes=v->size-v->data_start;
    const unsigned count=v->samples-i*256<256?v->samples-i*256:256;
    return a<b&&b<=bytes&&block_valid(v->image+v->data_start+a,b-a,count);
}
static int64_t round_ratio(int64_t value,uint32_t divisor){
    return value<0?-((-value+divisor/2)/divisor):(value+divisor/2)/divisor;
}
static void keypoint_position(const trajectory_view_t*v,uint32_t t,int32_t*x,int32_t*y){
    uint32_t lo=0,hi=v->blocks-1;
    while(lo+1<hi){uint32_t mid=lo+(hi-lo)/2;if(r32(v->image+64+mid*12)<=t)lo=mid;else hi=mid;}
    const uint8_t*a=v->image+64+lo*12,*b=v->image+64+hi*12;
    uint32_t ta=r32(a),tb=r32(b),dt=t-ta;
    *x=(int32_t)((int64_t)(int32_t)r32(a+4)+round_ratio(((int64_t)(int32_t)r32(b+4)-(int32_t)r32(a+4))*dt,tb-ta));
    *y=(int32_t)((int64_t)(int32_t)r32(a+8)+round_ratio(((int64_t)(int32_t)r32(b+8)-(int32_t)r32(a+8))*dt,tb-ta));
}
bool trajectory_open(trajectory_view_t *v,const uint8_t *p,size_t size){
    if(!trajectory_header(v,p,size)||trajectory_crc32(p,size)!=r32(p+28))return false;
    for(uint32_t i=0;i<v->blocks;i++)if(!trajectory_block_valid(v,i))return false;
    return true;
}
bool trajectory_delta_at(const trajectory_view_t *v,uint32_t index,int16_t *dx,int16_t *dy){
    if(!v||index>=v->samples||!dx||!dy)return false;
    if(v->format_version==2){
        int32_t ax,ay,bx,by;keypoint_position(v,index,&ax,&ay);keypoint_position(v,index+1,&bx,&by);
        *dx=(int16_t)((int64_t)bx-ax);*dy=(int16_t)((int64_t)by-ay);return true;
    }
    const uint32_t block=index/256,offset=index%256;
    const uint32_t a=r32(v->image+32+4*block),b=r32(v->image+36+4*block);
    const uint8_t *p=v->image+v->data_start+a;size_t at=1;
    switch(p[0]){
    case 0:*dx=*dy=0;return true;
    case 1:*dx=(int16_t)r16(p+1);*dy=(int16_t)r16(p+3);return true;
    case 2:at+=offset*2;*dx=(int8_t)p[at];*dy=(int8_t)p[at+1];return true;
    case 3:at+=offset*4;*dx=(int16_t)r16(p+at);*dy=(int16_t)r16(p+at+2);return true;
    case 4:case 5:{
        const unsigned width=p[0]==4?3:5;unsigned sample=0;
        for(;at<b-a;at+=width){
            sample+=(unsigned)p[at]+1;
            if(offset<sample){
                *dx=width==3?(int8_t)p[at+1]:(int16_t)r16(p+at+1);
                *dy=width==3?(int8_t)p[at+2]:(int16_t)r16(p+at+3);return true;
            }
        }
        return false;
    }
    default:return false;
    }
}
void trajectory_start(trajectory_player_t *p,const trajectory_view_t *v,uint64_t now,uint32_t generation){
    memset(p,0,sizeof *p);p->view=v;p->target_generation=generation;
    if(!v||now>UINT64_MAX-((uint64_t)v->samples+2)*1000)return;
    p->start_us=now+(1000-now%1000)%1000;p->running=true;
}
void trajectory_stop(trajectory_player_t *p){p->running=false;}
bool trajectory_step(trajectory_player_t *p,uint64_t now,int16_t *dx,int16_t *dy,uint64_t *expires){
    if(!p->running||now<p->start_us)return false;
    const uint64_t due=(now-p->start_us)/1000;
    if(due<=p->consumed)return false;
    if(due>p->view->samples){p->skipped+=p->view->samples-p->consumed;p->consumed=p->view->samples;p->running=false;return false;}
    p->skipped+=(uint32_t)due-p->consumed-1;p->consumed=(uint32_t)due;++p->offered;
    if(due==p->view->samples)p->running=false;
    *expires=p->start_us+(due+1)*1000;
    return trajectory_delta_at(p->view,(uint32_t)due-1,dx,dy);
}
