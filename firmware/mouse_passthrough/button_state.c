#include <string.h>
#include "button_state.h"
static bool valid_source(source_id_t source) { return (unsigned)source < SOURCE_COUNT; }
void mouse_buttons_init(mouse_button_state_t *state) { memset(state, 0, sizeof(*state)); }
bool mouse_buttons_begin(mouse_button_state_t *state, source_id_t source, uint32_t epoch) {
    if (!state || !valid_source(source) || !epoch) return false;
    state->sources[source] = (mouse_button_source_t){.epoch=epoch, .buttons=0, .active=true};
    return true;
}
bool mouse_buttons_apply(mouse_button_state_t *state, source_id_t source, const mouse_delta_t *event) {
    if (!state || !event || !valid_source(source) ||
        ((event->buttons | event->buttons_valid) & ~(uint32_t)MOUSE_SUPPORTED_BUTTON_MASK)) return false;
    mouse_button_source_t *entry = &state->sources[source];
    if (!entry->active || event->epoch != entry->epoch) return false;
    entry->buttons = (entry->buttons & ~event->buttons_valid) | (event->buttons & event->buttons_valid);
    return true;
}
bool mouse_buttons_clear(mouse_button_state_t *state, source_id_t source, uint32_t epoch) {
    if (!state || !valid_source(source)) return false;
    mouse_button_source_t *entry = &state->sources[source];
    if (!entry->active || entry->epoch != epoch) return false;
    entry->buttons = 0;
    entry->active = false;
    return true;
}
void mouse_buttons_target_reset(mouse_button_state_t *state) { mouse_buttons_init(state); }
uint16_t mouse_buttons_value(const mouse_button_state_t *state) {
    uint32_t value = 0;
    for (unsigned i = 0; i < SOURCE_COUNT; ++i) if (state->sources[i].active) value |= state->sources[i].buttons;
    return (uint16_t)value;
}
