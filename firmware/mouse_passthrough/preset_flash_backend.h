#ifndef FUSION_PRESET_FLASH_BACKEND_H
#define FUSION_PRESET_FLASH_BACKEND_H
#include "preset_store.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    preset_store_io_t raw;
    uint8_t page[PRESET_PAGE_BYTES];
    bool initialized,sealed;
} preset_flash_backend_t;
// Zero-initialized object, one boot only. Size must come from a verified device identity.
bool preset_flash_backend_init(preset_flash_backend_t*,const preset_store_io_t*,uint32_t firmware_end,uint32_t flash_bytes,preset_store_io_t*);
void preset_flash_backend_seal(preset_flash_backend_t*); // irreversible, call before USB/Host/Core1 begins
uint8_t preset_flash_backend_save(preset_flash_backend_t*,preset_store_t*,const preset_runtime_t*);
// Pico adapter is linked but never opened/attached by normal firmware.
bool preset_flash_pico_boot_init(preset_flash_backend_t*,uint32_t identified_flash_bytes,preset_store_io_t*);
void preset_flash_pico_seal(void);
uint32_t preset_flash_pico_capacity(void); // JEDEC read before USB/Core1.
bool preset_flash_pico_maintenance_init(preset_flash_backend_t*,uint32_t,preset_store_io_t*,bool core1_parked);
#ifdef __cplusplus
}
#endif
#endif
