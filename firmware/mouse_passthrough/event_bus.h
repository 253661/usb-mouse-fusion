#ifndef FUSION_EVENT_BUS_H
#define FUSION_EVENT_BUS_H
#include "bridge_types.h"
#ifdef __cplusplus
extern "C" {
#endif
enum { PHYSICAL_QUEUE_CAPACITY = 128, PHYSICAL_SNAPSHOT_ATTEMPTS = 2 };
typedef struct { mouse_delta_t delta; uint32_t target_generation; } physical_record_t;
typedef struct {
    uint32_t epoch, connected, target_generation, barrier, barrier_cutoff;
    uint32_t buttons, sequence, snapshot_cutoff, overflow;
    uint32_t button_presses[5]; // Sticky per-epoch raw edges, independent of FIFO admission.
} physical_snapshot_t;
// init only BEFORE Core1 starts. Core1 alone begins/offers/invalidates physical
// sessions; Core0 alone peeks/pops/discards/resets the target generation.
void event_bus_init(void);
bool event_bus_physical_begin(uint32_t epoch);
bool event_bus_offer(const mouse_delta_t *event);
void event_bus_invalidate(uint32_t epoch);
bool event_bus_snapshot(physical_snapshot_t *out); // at most two attempts; out unchanged on failure
bool event_bus_peek(physical_record_t *out);
bool event_bus_pop(void);
uint32_t event_bus_discard_before(uint32_t cutoff);
void event_bus_target_reset(void);
uint32_t event_bus_target_generation(void);
uint32_t event_bus_depth(void);
uint32_t event_bus_high_water(void);
#ifdef __cplusplus
}
#endif
#endif
