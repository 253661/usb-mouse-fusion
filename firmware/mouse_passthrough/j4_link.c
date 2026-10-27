#include <string.h>
#include "j4_link.h"
#if FUSION_TRAJECTORY
#include "j4_preset.h"
#endif
static uint16_t read16(const uint8_t *p) { return (uint16_t)(p[0]|((uint16_t)p[1]<<8)); }
static uint32_t read32(const uint8_t *p) { return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24); }
static void put16(uint8_t *p,uint16_t x) { p[0]=(uint8_t)x;p[1]=(uint8_t)(x>>8); }
static void put32(uint8_t *p,uint32_t x) { for(unsigned i=0;i<4;++i)p[i]=(uint8_t)(x>>(8*i)); }
uint16_t j4_crc16(const uint8_t *data,size_t size) {
    uint16_t crc=0xffff;
    for(size_t i=0;i<size;++i){crc^=(uint16_t)data[i]<<8;for(unsigned bit=0;bit<8;++bit)crc=(uint16_t)((crc<<1)^((crc&0x8000)?0x1021:0));}
    return crc;
}
size_t j4_encode(const j4_frame_t *f,uint8_t *out,size_t capacity) {
    if(f->size>J4_PAYLOAD_MAX||!f->sequence||capacity<(size_t)f->size+14)return 0;
    uint8_t raw[J4_RAW_MAX];raw[0]=J4_VERSION;raw[1]=f->opcode;put16(raw+2,f->sequence);put32(raw+4,f->session);put16(raw+8,f->size);
    memcpy(raw+10,f->payload,f->size);const size_t n=10+f->size;put16(raw+n,j4_crc16(raw,n));
    size_t used=1,mark=0;uint8_t code=1;
    for(size_t i=0;i<n+2;++i){if(raw[i]==0){out[mark]=code;mark=used++;code=1;}else{out[used++]=raw[i];++code;}}
    out[mark]=code;out[used++]=0;return used;
}
enum { DECODE_OK, DECODE_FORMAT, DECODE_CRC };
static unsigned decode(const uint8_t *encoded,size_t size,j4_frame_t *f) {
    if(size<13||size>J4_WIRE_MAX-1)return DECODE_FORMAT;
    uint8_t raw[J4_RAW_MAX];size_t pos=0,used=0;
    while(pos<size){
        const unsigned code=encoded[pos++];
        if(!code||pos+code-1>size||used+code-1>sizeof raw)return DECODE_FORMAT;
        for(unsigned i=1;i<code;++i){if(encoded[pos]==0)return DECODE_FORMAT;raw[used++]=encoded[pos++];}
        if(code!=255&&pos<size){if(used==sizeof raw)return DECODE_FORMAT;raw[used++]=0;}
    }
    if(used<12||raw[0]!=J4_VERSION||!read16(raw+2)||read16(raw+8)>J4_PAYLOAD_MAX||used!=(size_t)read16(raw+8)+12)return DECODE_FORMAT;
    if(read16(raw+used-2)!=j4_crc16(raw,used-2))return DECODE_CRC;
    f->opcode=raw[1];f->sequence=read16(raw+2);f->session=read32(raw+4);f->size=read16(raw+8);memcpy(f->payload,raw+10,f->size);
    return DECODE_OK;
}
bool j4_decode(const uint8_t *encoded,size_t size,j4_frame_t *f) { return decode(encoded,size,f)==DECODE_OK; }
void j4_link_init(j4_link_t *l,const uint8_t id[8]) {
    memset(l,0,sizeof(*l));memcpy(l->board_id,id,8);l->idle_ms=J4_DEFAULT_IDLE_MS;
#if FUSION_J4_AUX
    l->aux_ttl_ms=J4_AUX_DEFAULT_TTL_MS;l->aux_max_delta=J4_AUX_MAX_DELTA;
#endif
}
#if FUSION_J4_AUX
void j4_link_set_aux_ops(j4_link_t *l,const j4_aux_ops_t *ops) {
    if(ops)l->aux_ops=*ops;else memset(&l->aux_ops,0,sizeof l->aux_ops);
}
static void clear_aux(j4_link_t *l) { if(l->aux_ops.clear)l->aux_ops.clear(l->aux_ops.context); }
#endif
static void clear_session(j4_link_t *l) {
#if FUSION_TRAJECTORY
    j4_preset_transfer_reset(); // staged transfer only, not presets/playback
#endif
    l->session=0;l->last_sequence=0;l->cached_valid=false;l->cached_size=0;
    l->identify_until_us=0;l->idle_ms=J4_DEFAULT_IDLE_MS;
#if FUSION_J4_AUX
    clear_aux(l);l->aux_sequence=0;l->aux_last_dx=l->aux_last_dy=0;
    // An already-started frame must finish, even when its session is retired.
    // Unsent replies can be discarded with the session's pending effect.
    if(!l->tx_sent)l->tx_size=0;
    if(!l->realtime_tx_sent)l->realtime_tx_size=0;
    l->realtime_reserved=false;
#else
    l->tx_size=l->tx_sent=0;
#endif
}
void j4_link_tick(j4_link_t *l,uint64_t now) {
    if(l->rx_size&&now-l->last_rx_us>=J4_FRAGMENT_TIMEOUT_US){l->rx_size=0;l->discarding=true;++l->format_errors;}
    if(l->session&&now-l->last_valid_us>=(uint64_t)l->idle_ms*1000)clear_session(l);
}
bool j4_link_identifying(const j4_link_t *l,uint64_t now) { return l->session&&now<l->identify_until_us; }
void j4_link_transport_reset(j4_link_t *l) {
    clear_session(l);l->rx_size=0;l->discarding=false;++l->transport_errors;
}
static bool same_request(const j4_frame_t *a,const j4_frame_t *b) {
    return a->opcode==b->opcode&&a->sequence==b->sequence&&a->session==b->session&&a->size==b->size&&memcmp(a->payload,b->payload,a->size)==0;
}
static void respond(j4_link_t *l,const j4_frame_t *request,uint32_t session,uint8_t result,const uint8_t *body,uint16_t size,bool cache) {
    j4_frame_t reply={.opcode=(uint8_t)(request->opcode|0x80),.sequence=request->sequence,.session=session,.size=(uint16_t)(size+1)};
    reply.payload[0]=result;if(size)memcpy(reply.payload+1,body,size);
    l->tx_size=j4_encode(&reply,l->tx,sizeof l->tx);l->tx_sent=0;
    if(cache){l->last_request=*request;l->last_sequence=request->sequence;l->cached_valid=true;
        l->cached_size=l->tx_size;memcpy(l->cached,l->tx,l->tx_size);}
}
#if FUSION_J4_AUX
static void respond_realtime(j4_link_t *l,const j4_frame_t *request,uint8_t result,const uint8_t *body,uint16_t size) {
    j4_frame_t reply={.opcode=(uint8_t)(request->opcode|0x80),.sequence=request->sequence,.session=request->session,.size=(uint16_t)(size+1)};
    reply.payload[0]=result;if(size)memcpy(reply.payload+1,body,size);
    l->realtime_tx_size=j4_encode(&reply,l->realtime_tx,sizeof l->realtime_tx);l->realtime_tx_sent=0;
    l->realtime_reserved=false;
}
static void dispatch_move(j4_link_t *l,const j4_frame_t *f,uint64_t now) {
    if(l->realtime_reserved||l->realtime_tx_sent<l->realtime_tx_size){
        ++l->tx_busy_drops;
        // Even a request with no available ACK slot cannot become a later move.
        // Session-invalid/malformed/old requests do not advance this session.
        if(l->session&&f->session==l->session&&((f->opcode==J4_MOVE&&(f->size==12||f->size==16))||(f->opcode==J4_MOVE_LEFT_HELD&&f->size==32)||(f->opcode==J4_MOVE_SMOOTH&&f->size==34))){
            const uint32_t sequence=read32(f->payload);
            if(sequence&&sequence>l->aux_sequence){l->aux_sequence=sequence;++l->aux_dropped;}
        }
        return;
    }
    // Reserve the realtime reply slot before any bridge effect is possible.
    l->realtime_reserved=true;
    uint8_t result=J4_OK,body[5]={0};uint16_t size=0;
    if(!l->session||f->session!=l->session)result=J4_NO_SESSION;
    else if(f->opcode==J4_MOVE_SMOOTH ? f->size!=34 : f->opcode==J4_MOVE_LEFT_HELD ? f->size!=32 : (f->size!=12
#if FUSION_TRAJECTORY
            &&f->size!=16
#endif
            ))result=J4_BAD_PAYLOAD;
    else {
        const uint32_t sequence=read32(f->payload);
        if(!sequence||sequence<=l->aux_sequence)result=J4_STALE_SEQUENCE;
        else {
            l->aux_sequence=sequence;
            const uint32_t remaining=read32(f->payload+8)-(uint32_t)now;
            const int16_t dx=(int16_t)read16(f->payload+4),dy=(int16_t)read16(f->payload+6);
            const int32_t maximum=l->aux_max_delta;
            if(f->sequence!=(uint16_t)(((sequence-1)%65535)+1))result=J4_BAD_SEQUENCE;
#if FUSION_TRAJECTORY
            else if(preset_bypass_inhibited(j4_preset_runtime()))result=PRESET_BYPASS;
            else if((f->size==12&&j4_preset_runtime()->bypass.mask)||
                    (f->size>=16&&read32(f->payload+12)!=j4_preset_runtime()->bypass.generation))result=PRESET_AUX_EPOCH;
#endif
            else if(!remaining||remaining>100000||now>UINT64_MAX-remaining)result=J4_EXPIRED;
            else if(l->aux_mode!=1)result=J4_DISABLED;
            else if(dx>maximum||dx< -maximum||dy>maximum||dy< -maximum)result=J4_RANGE;
            else if(f->opcode==J4_MOVE_SMOOTH){
                uint32_t gate[4];for(unsigned i=0;i<4;i++)gate[i]=read32(f->payload+16+4*i);
                result=l->aux_ops.offer_smooth?l->aux_ops.offer_smooth(l->aux_ops.context,dx,dy,now,now+remaining,gate,read16(f->payload+32)):J4_UNAVAILABLE;
            }else if(f->opcode==J4_MOVE_LEFT_HELD){
                uint32_t gate[4];for(unsigned i=0;i<4;i++)gate[i]=read32(f->payload+16+4*i);
                result=l->aux_ops.offer_gated?l->aux_ops.offer_gated(l->aux_ops.context,dx,dy,now,now+remaining,gate):J4_UNAVAILABLE;
            }else if(!l->aux_ops.offer)result=J4_UNAVAILABLE;
            else result=l->aux_ops.offer(l->aux_ops.context,l->session,sequence,dx,dy,now,now+remaining);
            if(result==J4_OK){
                ++l->aux_accepted;l->aux_last_dx=dx;l->aux_last_dy=dy;l->last_valid_us=now;
                put32(body,sequence);body[4]=J4_AUX_ACCEPTED;size=5;
            }else ++l->aux_dropped;
        }
    }
#if FUSION_TRAJECTORY
    if(result==PRESET_BYPASS||result==PRESET_AUX_EPOCH){put32(body,j4_preset_runtime()->bypass.generation);size=4;}
#endif
    respond_realtime(l,f,result,body,size);
}
#endif
static uint16_t remaining_ms(uint64_t until,uint64_t now) { return until>now?(uint16_t)((until-now+999)/1000):0; }
static void dispatch(j4_link_t *l,const j4_frame_t *f,uint64_t now,const j4_snapshot_t *snapshot) {
    ++l->rx_frames;
#if FUSION_J4_AUX
    if(f->opcode==J4_MOVE||f->opcode==J4_MOVE_LEFT_HELD||f->opcode==J4_MOVE_SMOOTH){dispatch_move(l,f,now);return;}
#endif
    // Reserve the control response slot before any command can change state.
    if(l->tx_sent<l->tx_size){++l->tx_busy_drops;return;}
    if(l->cached_valid&&same_request(f,&l->last_request)&&
       (l->session||l->last_request.opcode==J4_CLOSE)){
        memcpy(l->tx,l->cached,l->cached_size);l->tx_size=l->cached_size;l->tx_sent=0;l->last_valid_us=now;return;
    }
    uint8_t body[J4_PAYLOAD_MAX-1]={0};uint16_t size=0;uint8_t error=J4_OK;
    if(f->opcode==J4_INFO){
        if(f->size)error=J4_BAD_PAYLOAD;
        else if(f->session&&f->session!=l->session)error=J4_NO_SESSION;
        else {
            if(f->session)l->last_valid_us=now;
            body[0]=J4_VERSION;
#if FUSION_TRAJECTORY
#if FUSION_PERSISTENCE
            memcpy(body+1,"0.6.0-flash",sizeof "0.6.0-flash");
#else
            if(j4_preset_capabilities()&PRESET_CAP_DEVICE_FLASH)memcpy(body+1,"0.6.0-flash",sizeof "0.6.0-flash");
            else memcpy(body+1,"0.5.1-template",sizeof "0.5.1-template");
#endif
            put32(body+25,255|j4_preset_capabilities());
#elif FUSION_J4_AUX
            memcpy(body+1,"0.3.0-api",9);put32(body+25,255);
#else
            memcpy(body+1,"0.2.0-j4-proto",14);put32(body+25,15);
#endif
#if FUSION_J4_AUX
            if(l->aux_ops.offer_gated&&l->aux_ops.gate_status){
                put32(body+25,read32(body+25)|J4_CAP_AI_GATE|J4_CAP_AI_QUEUE);
                memcpy(body+1,"0.6.2-ai-queue",sizeof "0.6.2-ai-queue");
                if(l->aux_ops.offer_smooth&&l->aux_ops.cancel_smooth){
                    put32(body+25,read32(body+25)|J4_CAP_AI_SMOOTH);
                    memcpy(body+1,"0.6.3-ai-smooth",sizeof "0.6.3-ai-smooth");
                }
            }
#endif
            memcpy(body+17,l->board_id,8);put32(body+29,J4_BAUD);put16(body+33,J4_DEFAULT_IDLE_MS);put16(body+35,J4_PAYLOAD_MAX);size=37;
        }
        respond(l,f,f->session,error,body,size,false);return;
    }
    if(f->opcode==J4_HELLO){
        if(f->session||f->size!=4||!read32(f->payload)){respond(l,f,0,J4_BAD_PAYLOAD,NULL,0,false);return;}
        clear_session(l);l->session=read32(f->payload);l->last_valid_us=now;
        respond(l,f,l->session,J4_OK,f->payload,4,true);return;
    }
    if(!l->session||f->session!=l->session){respond(l,f,f->session,J4_NO_SESSION,NULL,0,false);return;}
    const uint16_t next=l->last_sequence==0xffff?1:(uint16_t)(l->last_sequence+1);
    if(f->sequence!=next){respond(l,f,l->session,J4_BAD_SEQUENCE,NULL,0,false);return;}
#if FUSION_TRAJECTORY
    if(f->opcode>=0x20&&f->opcode<=0x32){
        error=j4_preset_dispatch(f->opcode,f->payload,f->size,now,body,&size);
        if(error==PRESET_BYPASS||error==PRESET_AUX_EPOCH){put32(body,j4_preset_runtime()->bypass.generation);size=4;}
        if(error==J4_OK)l->last_valid_us=now;
        respond(l,f,l->session,error,body,size,error==J4_OK);return;
    }
#endif
    switch(f->opcode){
#if FUSION_J4_AUX
    case J4_CANCEL_SMOOTH:
        if(f->size!=4){error=J4_BAD_PAYLOAD;break;}
        if(!l->aux_ops.cancel_smooth){error=J4_UNAVAILABLE;break;}
        if(read32(f->payload)>l->aux_sequence)l->aux_sequence=read32(f->payload);
        l->aux_ops.cancel_smooth(l->aux_ops.context);put32(body,l->aux_sequence);size=4;break;
    case J4_AI_GATE_STATUS:{
        if(f->size){error=J4_BAD_PAYLOAD;break;}
        if(!l->aux_ops.gate_status){error=J4_UNAVAILABLE;break;}
        uint32_t gate[4]={0};uint16_t buttons=0;
        const bool valid=l->aux_ops.gate_status(l->aux_ops.context,gate,&buttons);
        body[0]=(uint8_t)valid;body[1]=(uint8_t)(valid&&(buttons&1));put16(body+2,buttons);
        for(unsigned i=0;i<4;i++)put32(body+4+4*i,gate[i]);
        size=20;break;
    }
    case J4_CLOCK:
        if(f->size){error=J4_BAD_PAYLOAD;break;}
        body[0]=J4_AUX_REVISION;put32(body+1,(uint32_t)now);size=5;
        break;
    case J4_GET_CONFIG:
        if(f->size){error=J4_BAD_PAYLOAD;break;}
        body[0]=l->aux_mode;put16(body+1,l->aux_ttl_ms);put16(body+3,l->aux_max_delta);size=5;
        break;
    case J4_SET_CONFIG:
        if(f->size!=4||read16(f->payload)<1||read16(f->payload)>J4_AUX_MAX_TTL_MS||
           read16(f->payload+2)<1||read16(f->payload+2)>J4_AUX_MAX_DELTA){error=J4_BAD_PAYLOAD;break;}
        clear_aux(l);l->aux_ttl_ms=read16(f->payload);l->aux_max_delta=read16(f->payload+2);
        body[0]=l->aux_mode;put16(body+1,l->aux_ttl_ms);put16(body+3,l->aux_max_delta);size=5;
        break;
    case J4_SET_MODE:
        if(f->size!=1||f->payload[0]>1){error=J4_BAD_PAYLOAD;break;}
#if FUSION_TRAJECTORY
        if(j4_preset_runtime()->bypass.mask){
            physical_snapshot_t s;bool valid=event_bus_snapshot(&s);preset_bypass_snapshot(j4_preset_runtime(),&s,valid);
            if(preset_bypass_inhibited(j4_preset_runtime())){error=PRESET_BYPASS;put32(body,j4_preset_runtime()->bypass.generation);size=4;break;}
        }
#endif
        clear_aux(l);l->aux_mode=f->payload[0];body[0]=l->aux_mode;size=1;
        break;
    case J4_AUX_STATUS:
        if(f->size){error=J4_BAD_PAYLOAD;break;}
        body[0]=l->aux_mode;put32(body+1,l->aux_accepted);put32(body+5,l->aux_dropped);put32(body+9,l->aux_sequence);
        put16(body+13,(uint16_t)l->aux_last_dx);put16(body+15,(uint16_t)l->aux_last_dy);size=17;
        break;
#endif
    case J4_PING:
        if(f->size)error=J4_BAD_PAYLOAD;
        break;
    case J4_STATUS:
        if(f->size){error=J4_BAD_PAYLOAD;break;}
        if(snapshot){put32(body,snapshot->flags);body[4]=snapshot->host_phase;body[5]=snapshot->host_error;put16(body+6,snapshot->buttons);}
        put32(body+8,l->rx_frames);put32(body+12,l->crc_errors);put32(body+16,l->format_errors);
        put32(body+20,l->transport_errors);put32(body+24,l->tx_busy_drops);put32(body+28,l->session);
        put16(body+32,l->idle_ms);put16(body+34,remaining_ms(l->identify_until_us,now));size=36;
        break;
    case J4_IDENTIFY:
        if(f->size!=2||read16(f->payload)>5000){error=J4_BAD_PAYLOAD;break;}
        l->identify_until_us=read16(f->payload)?now+(uint64_t)read16(f->payload)*1000:0;
        memcpy(body,f->payload,2);size=2;
        break;
    case J4_SET_IDLE:
        if(f->size!=2||read16(f->payload)<500||read16(f->payload)>5000){error=J4_BAD_PAYLOAD;break;}
        l->idle_ms=read16(f->payload);memcpy(body,f->payload,2);size=2;
        break;
    case J4_CLOSE:
        if(f->size){error=J4_BAD_PAYLOAD;break;}
        clear_session(l);respond(l,f,f->session,J4_OK,NULL,0,true);return;
    default:error=J4_BAD_COMMAND;break;
    }
    if(error==J4_OK)l->last_valid_us=now;
    respond(l,f,l->session,error,body,size,error==J4_OK);
}
void j4_link_feed(j4_link_t *l,uint8_t byte,uint64_t now,const j4_snapshot_t *snapshot) {
    j4_link_tick(l,now);
    if(byte){
        if(l->discarding)return;
        if(l->rx_size==J4_WIRE_MAX-1){l->rx_size=0;l->discarding=true;++l->format_errors;return;}
        l->rx[l->rx_size++]=byte;l->last_rx_us=now;return;
    }
    if(l->discarding){l->discarding=false;l->rx_size=0;return;}
    if(!l->rx_size)return;
    j4_frame_t frame;const unsigned result=decode(l->rx,l->rx_size,&frame);l->rx_size=0;
    if(result==DECODE_OK)dispatch(l,&frame,now,snapshot);
    else if(result==DECODE_CRC)++l->crc_errors;
    else ++l->format_errors;
}
size_t j4_link_take_tx(j4_link_t *l,uint8_t *out,size_t capacity) {
#if FUSION_J4_AUX
    if(l->realtime_reserved)return 0;
    const bool control_partial=l->tx_sent&&l->tx_sent<l->tx_size;
    if(!control_partial&&l->realtime_tx_sent<l->realtime_tx_size){
        size_t count=l->realtime_tx_size-l->realtime_tx_sent;if(count>capacity)count=capacity;
        if(count){memcpy(out,l->realtime_tx+l->realtime_tx_sent,count);l->realtime_tx_sent+=count;}
        return count;
    }
#endif
    size_t count=l->tx_size-l->tx_sent;if(count>capacity)count=capacity;
    if(count){memcpy(out,l->tx+l->tx_sent,count);l->tx_sent+=count;}
    return count;
}
