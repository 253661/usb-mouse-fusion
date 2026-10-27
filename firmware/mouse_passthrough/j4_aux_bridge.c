#include "j4_aux_bridge.h"
#include "passthrough_core.h"
static uint8_t offer(void *context,uint32_t session,uint32_t sequence,int16_t dx,int16_t dy,
                     uint64_t received_us,uint64_t expires_us){
    (void)context;(void)session;(void)sequence;
    return bridge_ai_offer(dx,dy,received_us,expires_us);
}
static void clear(void *context){(void)context;bridge_ai_clear();}
static bool gate_status(void *context,uint32_t gate[4],uint16_t *buttons){(void)context;return bridge_ai_gate_status(gate,buttons);}
static uint8_t offer_gated(void *context,int16_t dx,int16_t dy,uint64_t received_us,uint64_t expires_us,const uint32_t gate[4]){
    (void)context;return bridge_ai_offer_gated(dx,dy,received_us,expires_us,gate);
}
static uint8_t offer_smooth(void *context,int16_t dx,int16_t dy,uint64_t received_us,uint64_t expires_us,const uint32_t gate[4],uint16_t duration_ms){
    (void)context;return bridge_ai_offer_smooth(dx,dy,received_us,expires_us,gate,duration_ms);
}
static void cancel_smooth(void *context){(void)context;bridge_ai_smooth_cancel();}
void j4_aux_bridge_attach(j4_link_t *link){
    const j4_aux_ops_t ops={.context=NULL,.offer=offer,.clear=clear,.gate_status=gate_status,.offer_gated=offer_gated,.offer_smooth=offer_smooth,.cancel_smooth=cancel_smooth};
    j4_link_set_aux_ops(link,&ops);
}
uint32_t const fusion_api_contract[]={0x41504932,2,1,1,100,32767,BRIDGE_PERIOD_US,
    BRIDGE_COALESCE_US,BRIDGE_SUBMIT_MARGIN_US,0,0}; // X/Y only; no buttons/Flash
uint32_t const fusion_ai_gate_contract[]={0x41494731,1,J4_CAP_AI_GATE,J4_AI_GATE_STATUS,J4_MOVE_LEFT_HELD,20,32,J4_LEFT_GATE_CLOSED,4};
uint32_t const fusion_ai_queue_contract[]={0x41495131,1,J4_CAP_AI_QUEUE,1,BRIDGE_PERIOD_US,1}; // one latest whole sample, at most next period
