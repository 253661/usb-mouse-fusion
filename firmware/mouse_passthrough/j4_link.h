#ifndef FUSION_J4_LINK_H
#define FUSION_J4_LINK_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifndef FUSION_J4_AUX
#define FUSION_J4_AUX 0
#endif
#ifndef FUSION_TRAJECTORY
#define FUSION_TRAJECTORY 0
#endif
#ifdef __cplusplus
extern "C" {
#endif
enum { J4_VERSION=1, J4_PAYLOAD_MAX=48, J4_RAW_MAX=60, J4_WIRE_MAX=62,
       J4_BAUD=115200, J4_DEFAULT_IDLE_MS=2000, J4_FRAGMENT_TIMEOUT_US=50000,
       J4_RX_BUDGET=32, J4_TX_BUDGET=32, J4_LED_PIN=22 };
enum { J4_INFO=1, J4_HELLO=2, J4_PING=3, J4_STATUS=4, J4_IDENTIFY=5, J4_CLOSE=6, J4_SET_IDLE=7 };
enum { J4_OK=0, J4_BAD_COMMAND=1, J4_BAD_PAYLOAD=2, J4_NO_SESSION=3, J4_BAD_SEQUENCE=4 };
#if FUSION_J4_AUX
enum { J4_MOVE=0x10, J4_CLOCK=0x11, J4_GET_CONFIG=0x12, J4_SET_CONFIG=0x13, J4_SET_MODE=0x14, J4_AUX_STATUS=0x15 };
enum { J4_AI_GATE_STATUS=0x16,J4_MOVE_LEFT_HELD=0x17,J4_CAP_AI_GATE=0x40000,J4_LEFT_GATE_CLOSED=20 };
enum { J4_CAP_AI_QUEUE=0x80000 };
enum { J4_CAP_AI_SMOOTH=0x100000,J4_MOVE_SMOOTH=0x18,J4_CANCEL_SMOOTH=0x19 };
enum { J4_EXPIRED=5, J4_DISABLED=6, J4_STALE_SEQUENCE=7, J4_RANGE=8, J4_UNAVAILABLE=9, J4_CYCLE_CLOSED=10,
       J4_AUX_REVISION=2, J4_AUX_ACCEPTED=2, J4_AUX_DEFAULT_TTL_MS=20, J4_AUX_MAX_TTL_MS=100, J4_AUX_MAX_DELTA=32767 };
typedef struct {
    void *context;
    // Synchronous Core0 callbacks; must not reenter the link or transmit its bytes.
    // J4_OK means accepted for this bridge cycle, not HID submission/completion.
    uint8_t (*offer)(void *context,uint32_t session,uint32_t sequence,int16_t dx,int16_t dy,
                     uint64_t received_us,uint64_t expires_us);
    void (*clear)(void *context);
    bool (*gate_status)(void *context,uint32_t gate[4],uint16_t *buttons);
    uint8_t (*offer_gated)(void *context,int16_t dx,int16_t dy,uint64_t received_us,uint64_t expires_us,const uint32_t gate[4]);
    uint8_t (*offer_smooth)(void *context,int16_t dx,int16_t dy,uint64_t received_us,uint64_t expires_us,const uint32_t gate[4],uint16_t duration_ms);
    void (*cancel_smooth)(void *context);
} j4_aux_ops_t;
#endif
typedef struct { uint8_t opcode; uint16_t sequence, size; uint32_t session; uint8_t payload[J4_PAYLOAD_MAX]; } j4_frame_t;
typedef struct { uint32_t flags; uint8_t host_phase, host_error; uint16_t buttons; } j4_snapshot_t;
typedef struct {
    uint8_t board_id[8], rx[J4_WIRE_MAX], tx[J4_WIRE_MAX], cached[J4_WIRE_MAX];
    size_t rx_size, tx_size, tx_sent, cached_size;
    bool discarding, cached_valid;
    uint32_t session, rx_frames, crc_errors, format_errors, transport_errors, tx_busy_drops;
    uint16_t last_sequence, idle_ms;
    uint64_t last_rx_us, last_valid_us, identify_until_us;
    j4_frame_t last_request;
#if FUSION_J4_AUX
    uint8_t realtime_tx[J4_WIRE_MAX];
    size_t realtime_tx_size, realtime_tx_sent;
    bool realtime_reserved;
    j4_aux_ops_t aux_ops;
    // Protocol acceptance/rejection counters, never HID delivery counters.
    // aux_sequence consumes all new well-formed, session-bound motions.
    uint32_t aux_sequence, aux_accepted, aux_dropped;
    int16_t aux_last_dx, aux_last_dy;
    // RAM settings survive session retirement and reset only at link init/boot.
    uint16_t aux_ttl_ms, aux_max_delta;
    uint8_t aux_mode;
#endif
} j4_link_t;
uint16_t j4_crc16(const uint8_t *data,size_t size);
size_t j4_encode(const j4_frame_t *frame,uint8_t *out,size_t capacity);
bool j4_decode(const uint8_t *encoded,size_t size,j4_frame_t *frame); // excludes final 0
void j4_link_init(j4_link_t *link,const uint8_t board_id[8]);
void j4_link_tick(j4_link_t *link,uint64_t now_us);
void j4_link_feed(j4_link_t *link,uint8_t byte,uint64_t now_us,const j4_snapshot_t *snapshot);
size_t j4_link_take_tx(j4_link_t *link,uint8_t *out,size_t capacity);
void j4_link_transport_reset(j4_link_t *link);
bool j4_link_identifying(const j4_link_t *link,uint64_t now_us);
#if FUSION_J4_AUX
void j4_link_set_aux_ops(j4_link_t *link,const j4_aux_ops_t *ops);
#endif
#ifdef __cplusplus
}
#endif
#endif
