#ifndef FUSION_HOST_SESSION_H
#define FUSION_HOST_SESSION_H
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
// Core1 only, frame IRQ stopped/returned before quiesce. Retains HCD/PIO/DMA claims.
bool fusion_usbh_quiesce(uint8_t rhport);
bool fusion_usbh_rearm(uint8_t rhport);
uint32_t fusion_usbh_generation(void);
typedef struct {
    uint32_t generation, enumerating, configured, stage, next_step, daddr;
    uint32_t request, value, length, actual_len;
} fusion_usbh_probe_t;
void fusion_usbh_probe(fusion_usbh_probe_t *out); // Core1 read-only, before cleanup erases failure context.
bool fusion_host_cancel_requested(void);
void fusion_host_event_lost(void); // persistent fault notice, also from Core1 frame IRQ
void fusion_host_connection_event(bool attached); // Core1 task, before applying the matching event
bool fusion_host_delay_ms(uint32_t ms); // preserves normal duration, checks cancellation each 1ms
#ifdef __cplusplus
}
#endif
#endif
