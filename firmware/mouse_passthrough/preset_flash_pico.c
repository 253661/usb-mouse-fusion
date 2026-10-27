// Boot-maintenance-only RP2040 primitives. No background flash mutation while USB is active.
#include <string.h>
#include "pico/stdlib.h"
#include "hardware/flash.h"
#include "hardware/sync.h"
#include "hardware/dma.h"
#include "tusb.h"
#include "preset_flash_backend.h"
extern uint8_t __flash_binary_end;
// Guard revision, bytes, firmware ceiling, sector/page, irreversible boot seal,
// normal-runtime write permission=0, normal-runtime store attachment=0.
uint32_t const fusion_preset_flash_contract[]={0x46534731,1,2097152,524288,4096,256,1,0,0};
static bool sealed;
static preset_flash_backend_t *opened;
static preset_flash_backend_t *maintenance;
static bool core1_parked;
static bool boot_safe(void){
    if(sealed||get_core_num()!=0||tud_inited())return false;
    for(unsigned i=0;i<NUM_DMA_CHANNELS;i++)if(dma_channel_is_busy(i))return false;
    return true; // main seals this latch before either USB init or Core1 launch.
}
static bool read(void*ctx,uint32_t off,uint8_t*out,uint32_t n){
    (void)ctx;memcpy(out,(const uint8_t*)(XIP_BASE+off),n);return true;
}
static bool maintenance_safe(void){
    if(!maintenance||!core1_parked||get_core_num()!=0||tud_inited())return false;
    for(unsigned i=0;i<NUM_DMA_CHANNELS;i++)if(dma_channel_is_busy(i))return false;
    return true;
}
static bool __not_in_flash_func(erase)(void*ctx,uint32_t off,uint32_t n){
    (void)ctx;if(!boot_safe()&&!maintenance_safe())return false;
    uint32_t saved=save_and_disable_interrupts();flash_range_erase(off,n);restore_interrupts(saved);return true;
}
static bool __not_in_flash_func(program)(void*ctx,uint32_t off,const uint8_t*in,uint32_t n){
    (void)ctx;if((!boot_safe()&&!maintenance_safe())||(uintptr_t)in<SRAM_BASE||(uintptr_t)in+n>SRAM_END)return false;
    uint32_t saved=save_and_disable_interrupts();flash_range_program(off,in,n);restore_interrupts(saved);return true;
}
bool preset_flash_pico_boot_init(preset_flash_backend_t*b,uint32_t bytes,preset_store_io_t*out){
    if(opened||!boot_safe())return false;
    uintptr_t end=(uintptr_t)&__flash_binary_end;
    if(end<XIP_BASE||end>XIP_BASE+PRESET_FIRMWARE_BYTES)return false;
    preset_store_io_t raw={NULL,read,erase,program};
    if(!preset_flash_backend_init(b,&raw,(uint32_t)(end-XIP_BASE),bytes,out))return false;
    opened=b;return true;
}
uint32_t preset_flash_pico_capacity(void){
    if(!boot_safe())return 0;
    uint8_t tx[4]={0x9f,0,0,0},rx[4]={0};uint32_t irq=save_and_disable_interrupts();
    flash_do_cmd(tx,rx,sizeof tx);restore_interrupts(irq);
    return rx[1]==0xef&&(rx[2]==0x40||rx[2]==0x60)&&rx[3]==0x15?PRESET_FLASH_BYTES:0;
}
bool preset_flash_pico_maintenance_init(preset_flash_backend_t*b,uint32_t bytes,preset_store_io_t*out,bool parked){
    if(!sealed||maintenance||!parked||get_core_num()!=0||tud_inited())return false;
    for(unsigned i=0;i<NUM_DMA_CHANNELS;i++)if(dma_channel_is_busy(i))return false;
    uintptr_t end=(uintptr_t)&__flash_binary_end;
    if(end<XIP_BASE||end>XIP_BASE+PRESET_FIRMWARE_BYTES)return false;
    preset_store_io_t raw={NULL,read,erase,program};
    if(!preset_flash_backend_init(b,&raw,(uint32_t)(end-XIP_BASE),bytes,out))return false;
    maintenance=b;core1_parked=true;return true;
}
void preset_flash_pico_seal(void){sealed=true;preset_flash_backend_seal(opened);preset_flash_backend_seal(maintenance);}
