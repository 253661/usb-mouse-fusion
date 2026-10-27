#ifndef FUSION_HID_SELECTION_H
#define FUSION_HID_SELECTION_H
#include "hid_layout.h"
#ifdef __cplusplus
extern "C" {
#endif
enum { HID_SELECTION_INTERFACE_MAX=8 };
typedef struct {
    const hid_layout_t *layout;
    bool present, available; // available only after the current protocol/session is bound
} hid_interface_candidate_t;
typedef struct {
    uint32_t selected_instance, supported_interfaces, mouse_applications;
    uint32_t rejected_mouse_interfaces, unknown_interfaces, partial;
} hid_selection_t;
// Core1 or pure tests. No retained pointers and no USB/output calls.
hid_selection_t hid_select_interfaces(const hid_interface_candidate_t *candidates,size_t count);
#ifdef __cplusplus
}
#endif
#endif
