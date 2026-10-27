#include "hid_input.h"
#include <string.h>
bool hid_input_session_begin(hid_input_session_t *session,uint32_t epoch,uint8_t address,
                             uint8_t instance,const hid_layout_t *layout) {
    if (!session) return false;
    hid_input_session_end(session);
    if (!epoch || !address || address>127 || !layout || !layout->ready || layout->result!=HID_LAYOUT_OK)
        return false;
    *session=(hid_input_session_t){epoch,address,instance,layout};
    return true;
}
void hid_input_session_end(hid_input_session_t *session) { if (session) memset(session,0,sizeof(*session)); }
hid_input_result_t hid_input_accept_report(const hid_input_session_t *session, uint32_t current_epoch,
                                         const uint8_t *bytes, size_t length, mouse_delta_t *output) {
    if (!session || !bytes || !length || !output) return HID_INPUT_INVALID;
    if (!session->epoch || session->epoch != current_epoch) return HID_INPUT_STALE;
    if (!session->layout) return HID_INPUT_NOT_IMPLEMENTED; // unbound Task 1 boundary remains safe
    mouse_delta_t candidate=*output;
    const hid_decode_result_t result=hid_decode_report(session->layout,bytes,length,&candidate);
    if (result==HID_DECODE_INVALID) return HID_INPUT_INVALID;
    if (result==HID_DECODE_IGNORED) return HID_INPUT_IGNORED;
    candidate.epoch=session->epoch;
    *output=candidate;
    return HID_INPUT_VALID;
}
