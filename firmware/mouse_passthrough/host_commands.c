#include "host_commands.h"
#include "atomic_u32.h"
void host_commands_quiesce(host_commands_t *c,uint32_t reason) {
    c->serial=(c->serial+1)&UINT32_C(0x1fffffff);
    if (!c->serial) ++c->serial;
    fusion_store_u32(&c->request,(c->serial<<3)|(reason&7));
}
bool host_commands_quiesced(const host_commands_t *c) {
    const uint32_t r=fusion_load_u32(&c->request);
    return r && fusion_load_u32(&c->quiesce_ack)==r && fusion_load_u32(&c->request)==r;
}
bool host_commands_rearm(host_commands_t *c) {
    if (!host_commands_quiesced(c)) return false;
    fusion_store_u32(&c->rearm_request,fusion_load_u32(&c->request)); return true;
}
bool host_commands_armed(const host_commands_t *c) {
    const uint32_t r=fusion_load_u32(&c->request);
    return r && fusion_load_u32(&c->quiesce_ack)==r && fusion_load_u32(&c->rearm_request)==r &&
        fusion_load_u32(&c->rearm_ack)==r && fusion_load_u32(&c->request)==r;
}
void host_commands_frames(host_commands_t *c,bool enabled) {
    fusion_store_u32(&c->frame_ticket,enabled && host_commands_armed(c) ? fusion_load_u32(&c->request) : 0);
}
bool host_commands_ack_quiesce(host_commands_t *c,uint32_t ticket) {
    if (!ticket || fusion_load_u32(&c->request)!=ticket) return false;
    fusion_store_u32(&c->quiesce_ack,ticket); return fusion_load_u32(&c->request)==ticket;
}
bool host_commands_ack_rearm(host_commands_t *c,uint32_t ticket) {
    if (!ticket || fusion_load_u32(&c->request)!=ticket || fusion_load_u32(&c->quiesce_ack)!=ticket ||
        fusion_load_u32(&c->rearm_request)!=ticket) return false;
    fusion_store_u32(&c->rearm_ack,ticket); return fusion_load_u32(&c->request)==ticket;
}
bool host_commands_run(const host_commands_t *c,uint32_t ticket) {
    return ticket && fusion_load_u32(&c->request)==ticket && fusion_load_u32(&c->frame_ticket)==ticket &&
        fusion_load_u32(&c->rearm_ack)==ticket;
}
