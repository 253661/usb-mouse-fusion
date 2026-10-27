#ifndef FUSION_BOARD_IO_H
#define FUSION_BOARD_IO_H
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { uint32_t en_latch, oe_n_latch, en_sense, oe_n_sense, fault_n, target_vbus; } board_io_status_t;
typedef enum { BOARD_IO_APPLIED=0, BOARD_IO_REJECTED_FAULT=1,
               BOARD_IO_REJECTED_TARGET=2, BOARD_IO_REJECTED_LOCKED=4 } board_io_apply_result_t;
void board_io_init_safe(void);
void board_io_force_safe(void);
board_io_apply_result_t board_io_apply(bool en,bool oe_n); // preserve every observed rejection for Core0
board_io_status_t board_io_snapshot(void);
#ifdef __cplusplus
}
#endif
#endif
