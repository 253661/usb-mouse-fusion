#include "hid_decode.h"
#include <limits.h>
// Bit extraction is little-endian and does not read beyond the report payload.
static uint32_t extract(const uint8_t *payload,uint16_t offset,uint8_t width) {
    uint32_t value=0;
    for (unsigned bit=0;bit<width;++bit) {
        unsigned source=(unsigned)offset+bit;
        value |= (uint32_t)((payload[source/8]>>(source%8))&1u)<<bit;
    }
    return value;
}
hid_decode_result_t hid_decode_report(const hid_layout_t *layout, const uint8_t *report,
                                      size_t length, mouse_delta_t *event) {
    if (!layout || !report || !event || !length || length>HID_LAYOUT_INPUT_MAX || !layout->ready ||
        layout->result!=HID_LAYOUT_OK || layout->report_count>HID_LAYOUT_REPORT_MAX ||
        layout->field_count>HID_LAYOUT_FIELD_MAX || layout->application_count>HID_LAYOUT_APPLICATION_MAX ||
        layout->selected_application>=layout->application_count) return HID_DECODE_INVALID;
    const uint8_t id=layout->has_report_ids ? report[0] : 0;
    unsigned index=0;
    while (index<layout->report_count && layout->reports[index].id!=id) ++index;
    if (index==layout->report_count) return HID_DECODE_IGNORED;
    const uint32_t bits=layout->reports[index].input_bits;
    if (!bits) return HID_DECODE_IGNORED; // Feature/Output-only ID is not an Input.
    if (bits>(HID_LAYOUT_INPUT_MAX-(layout->has_report_ids ? 1u : 0u))*8u ||
        length!=(bits+7u)/8u+(layout->has_report_ids ? 1u : 0u)) return HID_DECODE_INVALID;
    const uint8_t *payload=report+(layout->has_report_ids ? 1 : 0);
    mouse_delta_t candidate=*event;
    candidate.dx=candidate.dy=candidate.wheel=candidate.pan=0;
    candidate.buttons=candidate.buttons_valid=0;
    bool found=false;
    for (unsigned i=0;i<layout->field_count;++i) {
        const hid_field_t *f=&layout->fields[i];
        if (f->application!=layout->selected_application || f->report_index!=index) continue;
        if (!f->bits || f->bits>32 || f->bit_offset>bits || f->bits>bits-f->bit_offset)
            return HID_DECODE_INVALID;
        const uint32_t raw=extract(payload,f->bit_offset,f->bits);
        int64_t value=raw;
        if (f->logical_min<0 && (raw & (UINT32_C(1)<<(f->bits-1)))) value-=INT64_C(1)<<f->bits;
        const int64_t high=f->logical_min<0 ? (int64_t)(int32_t)f->logical_max : (int64_t)f->logical_max;
        if (value<f->logical_min || value>high || value<INT32_MIN || value>INT32_MAX) return HID_DECODE_INVALID;
        if (f->usage_page==9) {
            if (!f->usage || f->usage>16) return HID_DECODE_INVALID;
            const uint32_t mask=UINT32_C(1)<<(f->usage-1);
            candidate.buttons_valid |= mask;
            if (value) candidate.buttons |= mask;
        } else if (f->usage_page==1 && f->usage==0x30) candidate.dx=(int32_t)value;
        else if (f->usage_page==1 && f->usage==0x31) candidate.dy=(int32_t)value;
        else if (f->usage_page==1 && f->usage==0x38) candidate.wheel=(int32_t)value;
        else if (f->usage_page==0x0c && f->usage==0x238) candidate.pan=(int32_t)value;
        else return HID_DECODE_INVALID;
        found=true;
    }
    if (!found) return HID_DECODE_IGNORED;
    *event=candidate; // Commit only after every mapped field passes; no partial update.
    return HID_DECODE_VALID;
}
