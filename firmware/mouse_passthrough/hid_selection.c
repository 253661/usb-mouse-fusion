#include "hid_selection.h"
hid_selection_t hid_select_interfaces(const hid_interface_candidate_t *candidates,size_t count) {
    hid_selection_t out={.selected_instance=HID_LAYOUT_NO_APPLICATION};
    if (!candidates || count>HID_SELECTION_INTERFACE_MAX) { out.unknown_interfaces=1; return out; }
    for (size_t i=0;i<count;++i) {
        const hid_interface_candidate_t *c=&candidates[i];
        if (!c->present) continue;
        const hid_layout_t *l=c->layout;
        if (!l || l->application_count>HID_LAYOUT_APPLICATION_MAX) { ++out.unknown_interfaces; continue; }
        // Count observed Mouse apps even when the interface cannot normalize Input.
        out.mouse_applications+=l->application_count;
        if (!l->parsed) ++out.unknown_interfaces;
        const bool usable=c->available && l->parsed && l->ready && l->result==HID_LAYOUT_OK &&
            l->selected_application<l->application_count;
        if (usable) {
            ++out.supported_interfaces;
            if (out.selected_instance==HID_LAYOUT_NO_APPLICATION) out.selected_instance=(uint32_t)i;
        } else if (l->application_count) ++out.rejected_mouse_interfaces;
    }
    out.partial=out.supported_interfaces && (out.mouse_applications>1 ||
        out.rejected_mouse_interfaces || out.unknown_interfaces);
    return out;
}
