#ifndef FUSION_HID_INPUT_H
#define FUSION_HID_INPUT_H
#include <stddef.h>
#include "bridge_types.h"
#include "hid_decode.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { uint32_t epoch; uint8_t address, instance; const hid_layout_t *layout; } hid_input_session_t;
typedef enum { HID_INPUT_NOT_IMPLEMENTED, HID_INPUT_STALE, HID_INPUT_INVALID,
               HID_INPUT_VALID, HID_INPUT_IGNORED } hid_input_result_t;
// Layout stays in its Core1 owner for the whole session; no cross-core pointers.
bool hid_input_session_begin(hid_input_session_t *session,uint32_t epoch,uint8_t address,
                             uint8_t instance,const hid_layout_t *layout);
void hid_input_session_end(hid_input_session_t *session);
hid_input_result_t hid_input_accept_report(const hid_input_session_t *session, uint32_t current_epoch,
                                         const uint8_t *bytes, size_t length, mouse_delta_t *output);
#ifdef __cplusplus
}
#endif
#endif
