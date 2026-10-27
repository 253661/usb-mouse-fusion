// Media-independent transaction. The Pico backend has a separate irreversible boot guard.
#include <string.h>
#include "preset_store.h"
#include "passthrough_core.h"
static uint32_t r32(const uint8_t*p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static void w32(uint8_t*p,uint32_t v){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i));}
static uint32_t crc_update(uint32_t crc,const uint8_t*p,uint32_t n){
    for(uint32_t i=0;i<n;i++){crc^=p[i];for(unsigned j=0;j<8;j++)crc=(crc>>1)^((0u-(crc&1u))&UINT32_C(0xedb88320));}return crc;
}
static uint32_t base(unsigned bank){return PRESET_FIRMWARE_BYTES+bank*PRESET_BANK_BYTES;}
static unsigned marker_offset(const uint8_t*h){return h[4]>=4?508:252;}
static unsigned mode_count(const uint8_t*h){return h[4]>=4?PRESET_MODE_COUNT:4;}
static uint32_t header_crc(const uint8_t*h){uint32_t crc=UINT32_MAX;
    for(unsigned i=0;i<marker_offset(h);i++){uint8_t value=i>=24&&i<28?0:h[i];crc=crc_update(crc,&value,1);}return ~crc;}
void preset_store_init(preset_store_t*s,const preset_store_io_t*io){memset(s,0,sizeof *s);s->active_bank=-1;if(io)s->io=*io;}
void preset_store_workspace(preset_store_t*s,uint8_t*p,uint32_t size){s->workspace=p;s->workspace_bytes=size;}
static bool available(const preset_store_t*s){return s->io.read&&s->io.erase&&s->io.program;}
static int valid(preset_store_t*s,unsigned bank,uint8_t*h){
    if(!s->io.read(s->io.context,base(bank),h,PRESET_STORE_HEADER))return -1;
    if(!memcmp(h,"MFP1",4)&&h[4]>4)return -2; // Recognized future metadata is never free space.
    if(memcmp(h,"MFP1",4)||h[4]<1||h[4]>4||h[5]||h[6]!=0||h[7]!=(h[4]==4?2:1)||
       r32(h+marker_offset(h))!=UINT32_C(0x54494d43)||r32(h+24)!=header_crc(h)||h[20]>PRESET_TRIGGER_MAX||h[21]!=1||h[22]>=mode_count(h))return false;
    uint32_t payload=r32(h+12),total=(uint32_t)h[20]*12;
    if(h[4]>=2){
        if(h[23]!=PRESET_RULE_SCHEMA)return false;
        for(unsigned i=0;i<mode_count(h);i++){preset_rule_t rule;if(!preset_rule_decode(&rule,h+64+i*PRESET_RULE_BYTES))return false;}
    }
    if(h[4]==3&&(h[176]!=1||!preset_bypass_mask_valid(h[177])||h[178]||h[179]))return false;
    if(h[4]==4&&(h[304]!=1||!preset_bypass_mask_valid(h[305])||h[306]||h[307]))return false;
    for(unsigned i=0;i<mode_count(h);i++){uint32_t n=r32(h+32+i*4);
        // A recognized bank beyond this firmware's capacity is not corrupt/free space.
        // Keep it intact instead of treating it as permission to erase a legacy bank.
        if(n>PRESET_SLOT_BYTES)return -2;
        if(n&&n<41)return false;
        total+=n;}
    if(total!=payload||payload>PRESET_BANK_BYTES-PRESET_METADATA_BYTES)return false;
    uint32_t crc=UINT32_MAX;uint8_t page[256];
    for(uint32_t offset=0;offset<payload;offset+=256){uint32_t n=payload-offset;if(n>256)n=256;
        if(!s->io.read(s->io.context,base(bank)+PRESET_METADATA_BYTES+offset,page,n))return -1;
        crc=crc_update(crc,page,n);
    }
    return ~crc==r32(h+16);
}
static int choose(preset_store_t*s,uint8_t*h){
    uint8_t a[PRESET_STORE_HEADER],b[PRESET_STORE_HEADER];int va=valid(s,0,a),vb=valid(s,1,b);int bank=-1;
    if(va==-2||vb==-2)return -3;
    if(va<0||vb<0)return -2; // unknown contents must never authorize an erase
    if(va&&vb)bank=(uint32_t)(r32(b+8)-r32(a+8))<UINT32_C(0x80000000)&&r32(b+8)!=r32(a+8)?1:0;
    else if(va)bank=0;else if(vb)bank=1;
    if(bank>=0){memcpy(h,bank?b:a,PRESET_STORE_HEADER);s->active_bank=bank;s->generation=r32(h+8);s->payload_bytes=r32(h+12);
        s->last_cookie=h[4]==4&&!memcmp(h+308,"MFC1",4)?r32(h+312):0;}
    else{s->active_bank=-1;s->generation=s->payload_bytes=s->last_cookie=0;}
    return bank;
}
static void payload_slice(const preset_runtime_t*p,uint32_t offset,uint8_t*out,uint32_t n){
    uint32_t start=0;
    for(unsigned slot=0;slot<PRESET_MODE_COUNT;slot++){
        uint32_t end=start+p->sizes[slot];
        if(offset<end&&n){uint32_t take=end-offset;if(take>n)take=n;memcpy(out,p->slots[slot]+offset-start,take);out+=take;offset+=take;n-=take;}
        start=end;
    }
    while(n){uint32_t index=(offset-start)/12,inside=(offset-start)%12;uint8_t record[12];preset_trigger_encode(record,&p->triggers.records[index]);
        uint32_t take=12-inside;if(take>n)take=n;memcpy(out,record+inside,take);out+=take;offset+=take;n-=take;}
}
uint8_t preset_store_save(preset_store_t*s,const preset_runtime_t*p,bool safe){
    if(!available(s))return PRESET_NOT_SUPPORTED;
    if(!safe||preset_runtime_busy(p)||bridge_trajectory_busy()||p->upload_state==1||p->upload_state==2)return PRESET_UNSAFE;
    uint8_t header[PRESET_STORE_HEADER],page[256],readback[PRESET_STORE_HEADER];int chosen=choose(s,header);
    if(chosen==-2)return PRESET_STORAGE_ERROR;
    if(chosen==-3)return PRESET_NOT_SUPPORTED;
    unsigned bank=s->active_bank==0?1:0;uint32_t payload=(uint32_t)p->triggers.count*12;
    for(unsigned i=0;i<PRESET_MODE_COUNT;i++)payload+=p->sizes[i];
    if(payload>PRESET_BANK_BYTES-PRESET_METADATA_BYTES)return 8;
    memset(header,255,sizeof header);memcpy(header,"MFP1",4);header[4]=4;header[5]=0;header[6]=0;header[7]=2;
    if(!preset_bypass_mask_valid(p->bypass.mask))return 2;
    header[304]=1;header[305]=p->bypass.mask;header[306]=header[307]=0;
    memcpy(header+308,"MFC1",4);w32(header+312,s->request_cookie);
    w32(header+8,s->generation+1);w32(header+12,payload);header[20]=p->triggers.count;header[21]=1;header[22]=p->selected;header[23]=1;
    for(unsigned i=0;i<PRESET_MODE_COUNT;i++){
        if(!preset_rule_valid(&p->rules[i]))return 2;
        preset_rule_encode(header+64+i*PRESET_RULE_BYTES,&p->rules[i]);
    }
    uint32_t crc=UINT32_MAX;
    for(uint32_t offset=0;offset<payload;offset+=256){uint32_t n=payload-offset;if(n>256)n=256;payload_slice(p,offset,page,n);crc=crc_update(crc,page,n);}
    w32(header+16,~crc);for(unsigned i=0;i<PRESET_MODE_COUNT;i++)w32(header+32+i*4,p->sizes[i]);w32(header+24,header_crc(header));
    const uint32_t erase_bytes=PRESET_METADATA_BYTES+((payload+4095u)/4096u)*4096u;
    for(uint32_t offset=0;offset<erase_bytes;offset+=4096)if(!s->io.erase(s->io.context,base(bank)+offset,4096))return PRESET_STORAGE_ERROR;
    for(unsigned offset=0;offset<PRESET_STORE_HEADER;offset+=256)
        if(!s->io.program(s->io.context,base(bank)+offset,header+offset,256)||!s->io.read(s->io.context,base(bank)+offset,readback,256)||memcmp(header+offset,readback,256))return PRESET_STORAGE_ERROR;
    for(uint32_t offset=0;offset<payload;offset+=256){uint32_t n=payload-offset;if(n>256)n=256;memset(page,255,256);payload_slice(p,offset,page,n);
        uint32_t address=base(bank)+PRESET_METADATA_BYTES+offset;
        if(!s->io.program(s->io.context,address,page,256)||!s->io.read(s->io.context,address,readback,256)||memcmp(page,readback,256))return PRESET_STORAGE_ERROR;
    }
    // CRC covers header + payload independently; marker is the final NOR mutation.
    w32(header+508,UINT32_C(0x54494d43));
    if(!s->io.program(s->io.context,base(bank)+256,header+256,256)||valid(s,bank,readback)!=1)return PRESET_STORAGE_ERROR;
    s->active_bank=(int)bank;s->generation=r32(header+8);s->payload_bytes=payload;s->last_cookie=s->request_cookie;return 0;
}
uint8_t preset_store_restore_boot(preset_store_t*s,preset_runtime_t*p){
    if(!available(s))return PRESET_NOT_SUPPORTED;
    if(p->ready||preset_runtime_busy(p)||bridge_trajectory_busy()||p->upload_state)return PRESET_UNSAFE;
    for(unsigned i=0;i<PRESET_MODE_COUNT;i++)if(p->sizes[i])return PRESET_UNSAFE;
    uint8_t header[PRESET_STORE_HEADER];int bank=choose(s,header);
    if(bank==-2)return PRESET_STORAGE_ERROR;
    if(bank==-3)return PRESET_NOT_SUPPORTED;
    if(bank<0)return 9;
    uint32_t offset=0,crc=UINT32_MAX;uint8_t error=0;
    for(unsigned i=0;i<mode_count(header);i++){
        uint32_t n=r32(header+32+i*4);
        if(n){
            if(!s->io.read(s->io.context,base((unsigned)bank)+4096+offset,p->upload,n)){error=PRESET_STORAGE_ERROR;goto failed;}
            crc=crc_update(crc,p->upload,n);
            error=preset_apply(p,(uint8_t)i,p->upload,n);if(error)goto failed;
        }
        offset+=n;
    }
    const uint32_t trigger_bytes=(uint32_t)header[20]*12;
    if(trigger_bytes&&!s->io.read(s->io.context,base((unsigned)bank)+4096+offset,p->upload,trigger_bytes)){error=PRESET_STORAGE_ERROR;goto failed;}
    crc=crc_update(crc,p->upload,trigger_bytes);
    if(~crc!=r32(header+16)){error=PRESET_BAD_CRC;goto failed;}
    preset_trigger_record_t records[PRESET_TRIGGER_MAX];
    for(unsigned i=0;i<header[20];i++)preset_trigger_decode(&records[i],p->upload+i*12);
    error=preset_trigger_configure(&p->triggers,records,header[20]);if(error)goto failed;
    for(unsigned i=0;i<mode_count(header);i++)if(header[4]>=2)(void)preset_rule_decode(&p->rules[i],header+64+i*PRESET_RULE_BYTES);
    p->selected=header[22];
    error=preset_bypass_configure(p,header[4]==4?header[305]:(header[4]==3?header[177]:0));if(error)goto failed;
    preset_runtime_physical_reset(p);p->mode_enabled=false;p->upload_state=0;
    return 0;
failed:
    preset_runtime_init(p);bridge_auxiliary_clear();bridge_bypass_set(false);return error;
}
uint8_t preset_store_restore(preset_store_t*s,preset_runtime_t*p){
    if(!available(s))return PRESET_NOT_SUPPORTED;
    if(preset_runtime_busy(p)||bridge_trajectory_busy()||p->upload_state==1||p->upload_state==2)return PRESET_UNSAFE;
    uint8_t header[PRESET_STORE_HEADER];int bank=choose(s,header);
    if(bank==-2)return PRESET_STORAGE_ERROR;
    if(bank==-3)return PRESET_NOT_SUPPORTED;
    if(bank<0)return 9;
    uint32_t payload=r32(header+12);
    if(!s->workspace||s->workspace_bytes<payload)return PRESET_NOT_SUPPORTED;
    if(!s->io.read(s->io.context,base((unsigned)bank)+4096,s->workspace,payload))return PRESET_STORAGE_ERROR;
    if(~crc_update(UINT32_MAX,s->workspace,payload)!=r32(header+16))return PRESET_BAD_CRC;
    uint32_t offset=0;
    // Snapshot and validate every object before touching runtime data.
    for(unsigned i=0;i<mode_count(header);i++){uint32_t n=r32(header+32+i*4);trajectory_view_t view;
        if(n&&!trajectory_open(&view,s->workspace+offset,n))return PRESET_BAD_CRC;
        offset+=n;
    }
    preset_trigger_record_t records[PRESET_TRIGGER_MAX];preset_trigger_t check;
    for(unsigned i=0;i<header[20];i++){
        preset_trigger_decode(&records[i],s->workspace+offset+i*12);
    }
    if(preset_trigger_configure(&check,records,header[20]))return PRESET_BAD_CRC;
    preset_runtime_physical_reset(p);preset_motion_cancel(p,PRESET_REASON_STOPPED,true);offset=0;
    for(unsigned i=0;i<PRESET_MODE_COUNT;i++){uint32_t n=i<mode_count(header)?r32(header+32+i*4):0;
        if(n){if(preset_apply(p,(uint8_t)i,s->workspace+offset,n))return PRESET_STORAGE_ERROR;}
        else p->sizes[i]=0;
        offset+=n;
        if(header[4]>=2&&i<mode_count(header))(void)preset_rule_decode(&p->rules[i],header+64+i*PRESET_RULE_BYTES);
        else p->rules[i]=preset_rule_default();
    }
    p->triggers=check;p->selected=header[22];
    (void)preset_bypass_configure(p,header[4]==4?header[305]:(header[4]==3?header[177]:0));
    // Persist configuration only, never active state, a press, or a running job.
    if(p->bypass.mask)preset_runtime_physical_reset(p);
    return 0;
}
