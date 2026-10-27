#ifndef FUSION_BUTTON_STATE_H
#define FUSION_BUTTON_STATE_H
#include "bridge_types.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { uint32_t epoch, buttons; bool active; } mouse_button_source_t;
typedef struct { mouse_button_source_t sources[SOURCE_COUNT]; } mouse_button_state_t;
// Pure, single-owner module: USB types/GPIO/UART/CDC are deliberately absent.
void mouse_buttons_init(mouse_button_state_t *state);
bool mouse_buttons_begin(mouse_button_state_t *state, source_id_t source, uint32_t epoch);
bool mouse_buttons_apply(mouse_button_state_t *state, source_id_t source, const mouse_delta_t *event);
bool mouse_buttons_clear(mouse_button_state_t *state, source_id_t source, uint32_t epoch);
void mouse_buttons_target_reset(mouse_button_state_t *state);
uint16_t mouse_buttons_value(const mouse_button_state_t *state);
#ifdef __cplusplus
}
#endif
#endif
