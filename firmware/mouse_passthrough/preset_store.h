#ifndef FUSION_PRESET_STORE_H
#define FUSION_PRESET_STORE_H
#include "preset_runtime.h"
#ifdef __cplusplus
extern "C" {
#endif
enum { PRESET_FLASH_BYTES=2097152,PRESET_FIRMWARE_BYTES=524288,PRESET_BANK_BYTES=786432,
       PRESET_METADATA_BYTES=4096,PRESET_STORE_HEADER=512,PRESET_PAGE_BYTES=256 };
typedef struct {
    void *context;
    bool (*read)(void*,uint32_t,uint8_t*,uint32_t);
    bool (*erase)(void*,uint32_t,uint32_t);
    bool (*program)(void*,uint32_t,const uint8_t*,uint32_t);
} preset_store_io_t;
typedef struct {preset_store_io_t io;uint32_t generation,payload_bytes;int active_bank;
    uint8_t *workspace;uint32_t workspace_bytes;uint32_t request_cookie,last_cookie;} preset_store_t;
void preset_store_init(preset_store_t*,const preset_store_io_t*);
void preset_store_workspace(preset_store_t*,uint8_t*,uint32_t);
uint8_t preset_store_save(preset_store_t*,const preset_runtime_t*,bool explicitly_safe);
uint8_t preset_store_restore(preset_store_t*,preset_runtime_t*);
// Empty startup runtime only. Reuses its upload buffer; failure leaves no partial config.
uint8_t preset_store_restore_boot(preset_store_t*,preset_runtime_t*);
#ifdef __cplusplus
}
#endif
#endif
