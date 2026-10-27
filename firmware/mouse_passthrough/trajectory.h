#ifndef FUSION_TRAJECTORY_H
#define FUSION_TRAJECTORY_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "preset_perturb.h"
#ifdef __cplusplus
extern "C" {
#endif
enum { TRAJECTORY_TICK_US=1000, TRAJECTORY_BLOCK_SAMPLES=256, TRAJECTORY_MAX_SAMPLES=300000,
       PRESET_SLOT_BYTES=4096, PRESET_MODE_COUNT=8 };
typedef struct {
    const uint8_t *image; uint32_t size,samples,blocks,data_start;
    uint16_t format_version,sampling_target_ms;
    preset_perturb_config_t perturb;
} trajectory_view_t;
typedef struct {
    const trajectory_view_t *view; uint64_t start_us;
    uint32_t consumed,offered,skipped,target_generation; bool running;
} trajectory_player_t;
uint32_t trajectory_crc32(const uint8_t *data,size_t size);
bool trajectory_open(trajectory_view_t *view,const uint8_t *image,size_t size);
bool trajectory_header(trajectory_view_t *view,const uint8_t *image,size_t size);
bool trajectory_block_valid(const trajectory_view_t *view,uint32_t index);
bool trajectory_delta_at(const trajectory_view_t *view,uint32_t index,int16_t *dx,int16_t *dy);
void trajectory_start(trajectory_player_t *player,const trajectory_view_t *view,uint64_t now_us,uint32_t generation);
void trajectory_stop(trajectory_player_t *player);
bool trajectory_step(trajectory_player_t *player,uint64_t now_us,int16_t *dx,int16_t *dy,uint64_t *expires_us);
#ifdef __cplusplus
}
#endif
#endif
