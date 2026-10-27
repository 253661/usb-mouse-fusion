#include <string.h>
#include "preset_flash_backend.h"
static bool range(uint32_t offset,uint32_t n){
    return n&&offset>=PRESET_FIRMWARE_BYTES&&offset<=PRESET_FLASH_BYTES&&n<=PRESET_FLASH_BYTES-offset;
}
static bool read(void*ctx,uint32_t off,uint8_t*out,uint32_t n){
    preset_flash_backend_t*b=ctx;return b->initialized&&out&&range(off,n)&&b->raw.read(b->raw.context,off,out,n);
}
static bool erase(void*ctx,uint32_t off,uint32_t n){
    preset_flash_backend_t*b=ctx;
    return b->initialized&&!b->sealed&&range(off,n)&&off%4096==0&&n==4096&&b->raw.erase(b->raw.context,off,n);
}
static bool program(void*ctx,uint32_t off,const uint8_t*in,uint32_t n){
    preset_flash_backend_t*b=ctx;
    if(!b->initialized||b->sealed||!in||!range(off,n)||off%256||n!=256)return false;
    // Always stage a complete page in SRAM before a hardware callback can disable XIP.
    memcpy(b->page,in,256);return b->raw.program(b->raw.context,off,b->page,256);
}
bool preset_flash_backend_init(preset_flash_backend_t*b,const preset_store_io_t*raw,uint32_t end,uint32_t bytes,preset_store_io_t*out){
    if(!b||b->initialized||b->sealed||!raw||!raw->read||!raw->erase||!raw->program||!out||
       end<256||end>PRESET_FIRMWARE_BYTES||bytes!=PRESET_FLASH_BYTES)return false;
    b->raw=*raw;b->initialized=true;*out=(preset_store_io_t){b,read,erase,program};return true;
}
void preset_flash_backend_seal(preset_flash_backend_t*b){if(b)b->sealed=true;}
uint8_t preset_flash_backend_save(preset_flash_backend_t*b,preset_store_t*s,const preset_runtime_t*p){
    if(!b||!b->initialized||b->sealed)return PRESET_UNSAFE;
    return preset_store_save(s,p,true);
}
