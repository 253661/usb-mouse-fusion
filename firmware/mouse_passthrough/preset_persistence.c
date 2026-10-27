#include <string.h>
#include "preset_persistence.h"
#include "passthrough_core.h"
static void w32(uint8_t*p,uint32_t v){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i));}
void preset_persistence_init(preset_persistence_t*c,preset_store_t*s,preset_runtime_t*p,uint8_t result){
    memset(c,0,sizeof *c);c->store=s;c->runtime=p;c->boot_result=result;c->last_result=255;
}
uint8_t preset_persistence_request(preset_persistence_t*c,uint32_t revision,uint32_t cookie){
    if(!c||!c->store||!c->runtime)return PRESET_NOT_SUPPORTED;
    preset_runtime_t*p=c->runtime;
    if(!cookie)return 2;
    if(revision!=p->config_revision)return PRESET_STALE;
    if(c->phase)return c->phase==PRESET_PERSIST_PENDING&&c->cookie==cookie?0:PRESET_BUSY;
    if(cookie==c->store->last_cookie)return 0; // Committed request cannot cause another erase.
    physical_snapshot_t snapshot;mouse_report_t report;bridge_get_report(&report);
    if((event_bus_snapshot(&snapshot)&&snapshot.buttons)||report.buttons||preset_runtime_busy(p)||bridge_auxiliary_inflight()||
       p->upload_state==1||p->upload_state==2)return PRESET_UNSAFE;
    c->cookie=cookie;c->phase=PRESET_PERSIST_PENDING;c->last_result=255;
    p->maintenance_pending=true;p->mode_enabled=false;bridge_auxiliary_clear();bridge_bypass_set(true);
    return 0;
}
uint8_t preset_persistence_execute(preset_persistence_t*c,const preset_save_guard_t*g){
    if(!c||c->phase!=PRESET_PERSIST_PENDING||!g||!g->reply_drained||!g->host_quiesced||!g->usb_off||!g->core1_parked||!g->dma_idle)return PRESET_UNSAFE;
    c->phase=PRESET_PERSIST_WRITING;c->store->request_cookie=c->cookie;
    c->last_result=preset_store_save(c->store,c->runtime,true);return c->last_result;
}
void preset_persistence_status(const preset_persistence_t*c,uint8_t b[21]){
    b[0]=1;b[1]=c->phase;b[2]=c->boot_result;b[3]=c->last_result;
    w32(b+4,c->store->generation);w32(b+8,c->store->payload_bytes);w32(b+12,c->runtime->config_revision);
    w32(b+16,c->phase?c->cookie:c->store->last_cookie);b[20]=(uint8_t)c->store->active_bank;
}
