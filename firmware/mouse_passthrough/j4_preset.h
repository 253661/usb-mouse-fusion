#ifndef FUSION_J4_PRESET_H
#define FUSION_J4_PRESET_H
#include "preset_runtime.h"
#include "preset_store.h"
#include "preset_persistence.h"
#ifdef __cplusplus
extern "C" {
#endif
void j4_preset_init(void);
void j4_preset_tick(uint64_t now_us,bool target_ready);
void j4_preset_transfer_reset(void);
void j4_preset_attach_store(preset_store_t*);
void j4_preset_attach_persistence(preset_persistence_t*);
preset_runtime_t *j4_preset_runtime(void);
uint32_t j4_preset_capabilities(void);
uint8_t j4_preset_dispatch(uint8_t opcode,const uint8_t *payload,size_t size,uint64_t now_us,uint8_t *body,uint16_t *body_size);
#ifdef __cplusplus
}
#endif
#endif
