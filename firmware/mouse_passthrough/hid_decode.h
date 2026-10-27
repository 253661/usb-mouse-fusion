#ifndef FUSION_HID_DECODE_H
#define FUSION_HID_DECODE_H
#include "hid_layout.h"
#include "bridge_types.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef enum { HID_DECODE_VALID, HID_DECODE_IGNORED, HID_DECODE_INVALID } hid_decode_result_t;
// INVALID/IGNORED leave event byte-for-byte untouched. Only consume on VALID.
// VALID replaces every increment/mask, preserving caller epoch/sequence/time.
hid_decode_result_t hid_decode_report(const hid_layout_t *layout, const uint8_t *report,
                                      size_t length, mouse_delta_t *event);
#ifdef __cplusplus
}
#endif
#endif
