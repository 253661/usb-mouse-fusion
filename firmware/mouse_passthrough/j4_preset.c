#include "j4_preset.h"
#include "passthrough_core.h"
static preset_runtime_t runtime;
uint32_t const fusion_preset_contract[]={0x50525331,2,1000,PRESET_MODE_COUNT,PRESET_SLOT_BYTES,
    PRESET_TRIGGER_MAX,PRESET_FLASH_BYTES,PRESET_FIRMWARE_BYTES,PRESET_BANK_BYTES,PRESET_METADATA_BYTES,0};
uint32_t const fusion_bypass_contract[]={0x42595031,1,31,1,0x1000,0x2e,0x2f,16,0};
uint32_t const fusion_mode_control_contract[]={0x4d435431,1,0x2000,0x30,2,5,6,0};
static preset_store_t *store;
static preset_persistence_t *persistence;
static preset_trigger_record_t staged[PRESET_TRIGGER_MAX];
static uint8_t trigger_expected,trigger_received;static bool trigger_staging;
static uint16_t r16(const uint8_t *p){return (uint16_t)(p[0]|((uint16_t)p[1]<<8));}
static uint32_t r32(const uint8_t *p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static void w16(uint8_t *p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static void w32(uint8_t *p,uint32_t v){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i));}
static void w64(uint8_t *p,uint64_t v){for(unsigned i=0;i<8;i++)p[i]=(uint8_t)(v>>(8*i));}
void j4_preset_init(void){preset_runtime_init(&runtime);store=NULL;persistence=NULL;trigger_staging=false;}
void j4_preset_attach_store(preset_store_t*s){store=s;}
void j4_preset_attach_persistence(preset_persistence_t*p){persistence=p;}
void j4_preset_tick(uint64_t now,bool ready){preset_runtime_tick(&runtime,now,ready);}
void j4_preset_transfer_reset(void){preset_upload_abort(&runtime);trigger_staging=false;}
preset_runtime_t *j4_preset_runtime(void){return &runtime;}
uint32_t j4_preset_capabilities(void){return 0x1fb00u|(store?0x400u:0u)|(persistence?PRESET_CAP_DEVICE_FLASH:0u);}
static void bypass_status(uint8_t*b,uint16_t*n){
    const preset_bypass_t*s=&runtime.bypass;
    b[0]=1;b[1]=s->mask;b[2]=s->active;b[3]=preset_bypass_inhibited(&runtime);b[4]=s->input_valid;
    b[5]=s->rearm;b[6]=s->raw;b[7]=bridge_auxiliary_inflight();w32(b+8,s->generation);w32(b+12,s->entries);*n=16;
}
uint8_t j4_preset_dispatch(uint8_t op,const uint8_t *p,size_t size,uint64_t now,uint8_t *b,uint16_t *n){
    *n=0;
    if(op==0x31||op==0x32){
        if(!persistence)return PRESET_NOT_SUPPORTED;
        if(op==0x31){if(size)return 2;}
        else{
            if(size!=9||p[0]!=1)return 2;
            if(trigger_staging)return PRESET_UNSAFE;
            uint8_t result=preset_persistence_request(persistence,r32(p+1),r32(p+5));if(result)return result;
        }
        preset_persistence_status(persistence,b);*n=21;return 0;
    }
    if(runtime.maintenance_pending&&(op==0x21||op==0x22||op==0x23||op==0x24||op==0x26||op==0x29||op==0x2a||op==0x2c||op==0x2e))return PRESET_BUSY;
    if(runtime.bypass.mask){physical_snapshot_t s;bool valid=event_bus_snapshot(&s);preset_bypass_snapshot(&runtime,&s,valid);}
    switch(op){
    case 0x30:
        if(size)return 2;
        b[0]=1;b[1]=runtime.selected;b[2]=runtime.mode_enabled;b[3]=preset_mode_controlled(&runtime);*n=4;return 0;
    case 0x2e:{
        if(size!=2)return 2;
        if(p[0]!=1)return PRESET_NOT_SUPPORTED;
        uint8_t result=preset_bypass_configure(&runtime,p[1]);if(result)return result;
        if(runtime.bypass.mask){physical_snapshot_t s;bool valid=event_bus_snapshot(&s);preset_bypass_snapshot(&runtime,&s,valid);}
        bypass_status(b,n);return 0;
    }
    case 0x2f:
        if(size)return 2;
        bypass_status(b,n);return 0;
    case 0x20:
        if(size)return 2;
        b[0]=2;b[1]=PRESET_MODE_COUNT;w16(b+2,PRESET_SLOT_BYTES);b[4]=PRESET_TRIGGER_MAX;b[5]=3;
        w32(b+6,PRESET_FLASH_BYTES);w32(b+10,PRESET_FIRMWARE_BYTES);w32(b+14,PRESET_BANK_BYTES);w16(b+18,PRESET_METADATA_BYTES);b[20]=store!=NULL||persistence!=NULL;*n=21;return 0;
    case 0x21:
        if(size!=11)return 2;
        return preset_upload_begin(&runtime,p[4],r16(p+5),r32(p+7),r32(p));
    case 0x22:
        if(size<7)return 2;
        return preset_upload_chunk(&runtime,r32(p),r16(p+4),p+6,size-6);
    case 0x23:
        if(size!=4)return 2;
        return preset_upload_commit(&runtime,r32(p));
    case 0x24:
        if(size==12){
            if(p[0]!=1||p[3]>1||p[1]>4)return 2;
            if(preset_bypass_inhibited(&runtime))return p[1]==1?preset_control(&runtime,1,0,now):PRESET_BYPASS;
            if(r32(p+8)!=runtime.bypass.generation)return PRESET_AUX_EPOCH;
            if(p[3]&&(p[1]!=0||p[2]>=PRESET_MODE_COUNT||!runtime.sizes[p[2]]||r32(runtime.slots[p[2]]+28)!=r32(p+4)))return PRESET_STALE;
            return preset_control(&runtime,p[1],p[2],now);
        }
        if(size!=2&&size!=6)return 2;
        if(runtime.bypass.mask&&p[0]!=5){
            if(preset_bypass_inhibited(&runtime))return p[0]==1?preset_control(&runtime,1,0,now):PRESET_BYPASS;
            return PRESET_AUX_EPOCH;
        }
        if(size==6){
            if(p[0]!=0||p[1]>=PRESET_MODE_COUNT)return 2;
            if(!runtime.sizes[p[1]]||r32(runtime.slots[p[1]]+28)!=r32(p+2))return 16;
        }
        return preset_control(&runtime,p[0],p[1],now);
    case 0x25:
        if(size)return 2;
        b[0]=runtime.selected;b[1]=preset_runtime_busy(&runtime);b[2]=runtime.active_slot;b[3]=runtime.upload_state;
        b[4]=0;for(unsigned i=0;i<PRESET_MODE_COUNT;i++)if(runtime.sizes[i])b[4]|=(uint8_t)(1u<<i);
        b[5]=runtime.triggers.count;b[6]=runtime.last_error;
        w32(b+7,runtime.player.consumed);w32(b+11,runtime.player.skipped);w32(b+15,runtime.player.offered);
        w32(b+19,runtime.bridge_accepted);w32(b+23,runtime.bridge_dropped);
        w64(b+27,(uint64_t)runtime.output_dx);w64(b+35,(uint64_t)runtime.output_dy);w32(b+43,runtime.last_apply_token);
        *n=47;return 0;
    case 0x26:
        if(size<4||p[0]!=1)return 2;
        if(p[1]==0){
            if(size!=4||p[2]>PRESET_TRIGGER_MAX||p[3])return 2;
            trigger_expected=p[2];trigger_received=0;trigger_staging=true;return 0;
        }
        if(!trigger_staging)return 2;
        if(p[1]==1){
            if(p[2]!=trigger_received||!p[3]||p[3]>3||size!=4+p[3]*12u||p[2]+p[3]>trigger_expected)return 2;
            for(unsigned i=0;i<p[3];i++)preset_trigger_decode(&staged[trigger_received+i],p+4+i*12);
            trigger_received+=p[3];return 0;
        }
        if(p[1]==2){
            if(size!=4||p[2]||p[3]||trigger_received!=trigger_expected)return 2;
            // Validate before releasing a previously active hold or publishing.
            preset_trigger_t check;uint8_t result=preset_trigger_configure(&check,staged,trigger_expected);
            if(result)return result;
            preset_runtime_physical_reset(&runtime);runtime.triggers=check;runtime.mode_enabled=false;trigger_staging=false;preset_config_changed(&runtime);return 0;
        }
        return 2;
    case 0x29:
        if(size!=4||r32(p)!=runtime.upload_token)return 2;
        preset_upload_abort(&runtime);return 0;
    case 0x2a:{
        if(size!=1+PRESET_RULE_BYTES||p[0]>=PRESET_MODE_COUNT)return 2;
        preset_rule_t rule;if(!preset_rule_decode(&rule,p+1))return 2;
        return preset_set_rule(&runtime,p[0],&rule);
    }
    case 0x2b:
        if(size!=1||p[0]>=PRESET_MODE_COUNT)return 2;
        b[0]=p[0];preset_rule_encode(b+1,&runtime.rules[p[0]]);*n=1+PRESET_RULE_BYTES;return 0;
    case 0x2c:
        if((size!=10&&size!=14)||p[5]>1)return 2;
        if(p[0]!=PRESET_EXEC_KEEP){
            if(preset_bypass_inhibited(&runtime))return p[0]==PRESET_EXEC_STOP?0:PRESET_BYPASS;
            if((runtime.bypass.mask&&size!=14)||(size==14&&r32(p+10)!=runtime.bypass.generation))return PRESET_AUX_EPOCH;
        }
        if(p[5]){
            if(p[0]!=PRESET_EXEC_SWITCH||p[1]>=PRESET_MODE_COUNT)return 2;
            if(!runtime.sizes[p[1]]||r32(runtime.slots[p[1]]+28)!=r32(p+6))return 16;
        }
        return preset_execute(&runtime,p[0],p[1],r16(p+2),p[4],0,now);
    case 0x2d:{
        if(size)return 2;
        const preset_motion_t *m=&runtime.motion;
        b[0]=m->phase;b[1]=m->slot;b[2]=m->target_slot;b[3]=m->reason;b[4]=m->known;b[5]=m->rule.run_mode;b[6]=m->held;
        w32(b+7,m->cycles);w32(b+11,m->elapsed_ms);w32(b+15,m->return_skipped);w32(b+19,runtime.run_serial);
        w32(b+23,runtime.player.consumed);w32(b+27,runtime.player.skipped);w64(b+31,(uint64_t)m->x);w64(b+39,(uint64_t)m->y);
        *n=47;return 0;
    }
    case 0x27:case 0x28:{
        if(persistence)return PRESET_NOT_SUPPORTED; // Legacy simulated storage never enters hardware maintenance.
        if(!store)return PRESET_NOT_SUPPORTED;
        if(size!=1||p[0]!=1)return PRESET_UNSAFE;
        uint8_t result=op==0x27?preset_store_save(store,&runtime,true):preset_store_restore(store,&runtime);
        if(result)return result;
        w32(b,store->generation);w32(b+4,store->payload_bytes);b[8]=(uint8_t)store->active_bank;*n=9;return 0;
    }
    default:return PRESET_NOT_SUPPORTED;
    }
}
