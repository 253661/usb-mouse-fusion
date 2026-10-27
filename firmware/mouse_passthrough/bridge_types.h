#ifndef FUSION_BRIDGE_TYPES_H
#define FUSION_BRIDGE_TYPES_H
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef enum { SOURCE_PHYSICAL, SOURCE_TRAJECTORY, SOURCE_AI, SOURCE_COUNT } source_id_t;
enum { MOUSE_STANDARD_FIVE_MASK = 0x1f, MOUSE_SUPPORTED_BUTTON_MASK = 0xffff };
// Usage n maps to bit n-1 AFTER descriptor decoding, never to a fixed J2 byte.
// uint32_t storage leaves room for later expansion; current supported/output mask is 16 bits.
typedef struct {
    uint32_t epoch, sequence;
    uint64_t received_us;
    int32_t dx, dy, wheel, pan;
    uint32_t buttons, buttons_valid;
} mouse_delta_t;
typedef struct {
    source_id_t source;
    uint32_t session;
    uint64_t expires_us; // session/packet TTL; motion also expires at received_us period end
    mouse_delta_t delta;
} aux_delta_t;
typedef struct { uint16_t buttons; int16_t x, y; int8_t wheel, pan; } mouse_report_t;
// Physical ingress is Core1-owned; target reset/bridge output is Core0-owned.
// Firmware auxiliary ingress stays empty; independent queues are enabled only
// for native logic simulation, with no UART/trajectory/APP implementation.
bool physical_offer(const mouse_delta_t *event);
bool auxiliary_offer(const aux_delta_t *event);
void source_invalidate(source_id_t source, uint32_t session);
void target_session_reset(void);
void trigger_observe_physical(const mouse_delta_t *event); // bounded, read-only
#ifdef __cplusplus
}
#endif
#endif
