#ifndef FUSION_BRINGUP_CONTROL_H
#define FUSION_BRINGUP_CONTROL_H
#include "lifecycle.h"
#include "board_io.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef enum { BRINGUP_MODE_OFF, BRINGUP_MODE_POWER, BRINGUP_MODE_ARMED } bringup_mode_t;
typedef enum { BRINGUP_OFF, BRINGUP_POWER, BRINGUP_ARM, BRINGUP_RECOVER } bringup_command_t;
typedef enum { BRINGUP_OK, BRINGUP_LOCKED, BRINGUP_INTERLOCK, BRINGUP_FAULT_LATCHED,
               BRINGUP_POWER_REQUIRED, BRINGUP_SETTLE_REQUIRED, BRINGUP_RECOVERY_UNAVAILABLE } bringup_result_t;
typedef struct { lifecycle_inputs_t host; bool clock_ok, host_initialized, host_coherent; } bringup_inputs_t;
typedef struct { lifecycle_t lifecycle; bringup_mode_t mode; bool controlled; uint64_t power_started_us; } bringup_control_t;
void bringup_control_init(bringup_control_t *c,bool controlled);
void bringup_control_target_reset(bringup_control_t *c,uint64_t now_us);
bringup_result_t bringup_control_request(bringup_control_t *c,bringup_command_t command,bringup_inputs_t inputs,uint64_t now_us);
lifecycle_actions_t bringup_control_step(bringup_control_t *c,bringup_inputs_t inputs,uint64_t now_us);
// Consumes the GPIO boundary's observed values, never a second healthy resample.
lifecycle_actions_t bringup_control_gpio_result(bringup_control_t *c,bringup_inputs_t inputs,board_io_apply_result_t result,uint64_t now_us);
const char *bringup_result_name(bringup_result_t result);
// Core0 integration; no waiting, GPIO writes are performed only by that owner.
bringup_result_t fusion_bringup_execute(bringup_command_t command,uint64_t now_us);
bringup_mode_t fusion_bringup_mode(void);
#ifdef __cplusplus
}
#endif
#endif
