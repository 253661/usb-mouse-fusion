#ifndef FUSION_PRESET_PERSISTENCE_H
#define FUSION_PRESET_PERSISTENCE_H
#include "preset_store.h"
#ifdef __cplusplus
extern "C" {
#endif
enum {PRESET_CAP_DEVICE_FLASH=0x20000,PRESET_PERSIST_IDLE=0,PRESET_PERSIST_PENDING=1,PRESET_PERSIST_WRITING=2};
typedef struct {bool reply_drained,host_quiesced,usb_off,core1_parked,dma_idle;} preset_save_guard_t;
typedef struct {preset_store_t *store;preset_runtime_t *runtime;uint32_t cookie;uint8_t phase,boot_result,last_result;} preset_persistence_t;
void preset_persistence_init(preset_persistence_t*,preset_store_t*,preset_runtime_t*,uint8_t boot_result);
uint8_t preset_persistence_request(preset_persistence_t*,uint32_t expected_revision,uint32_t cookie);
uint8_t preset_persistence_execute(preset_persistence_t*,const preset_save_guard_t*);
void preset_persistence_status(const preset_persistence_t*,uint8_t body[21]);
#ifdef __cplusplus
}
#endif
#endif
