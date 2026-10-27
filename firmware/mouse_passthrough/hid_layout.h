#ifndef FUSION_HID_LAYOUT_H
#define FUSION_HID_LAYOUT_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { HID_LAYOUT_DESCRIPTOR_MAX=1024, HID_LAYOUT_INPUT_MAX=64,
       HID_LAYOUT_REPORT_MAX=16, HID_LAYOUT_FIELD_MAX=128,
       HID_LAYOUT_APPLICATION_MAX=8, HID_LAYOUT_COLLECTION_MAX=8,
       HID_LAYOUT_GLOBAL_MAX=4, HID_LAYOUT_USAGE_RANGE_MAX=32,
       HID_LAYOUT_NO_APPLICATION=255 };
typedef enum {
    HID_LAYOUT_OK=0, HID_LAYOUT_NOT_IMPLEMENTED, HID_LAYOUT_BAD_ARGUMENT,
    HID_LAYOUT_EMPTY, HID_LAYOUT_DESCRIPTOR_LIMIT, HID_LAYOUT_TRUNCATED_ITEM,
    HID_LAYOUT_LONG_ITEM, HID_LAYOUT_DELIMITER, HID_LAYOUT_UNSUPPORTED_ITEM,
    HID_LAYOUT_MALFORMED, HID_LAYOUT_COLLECTION_LIMIT, HID_LAYOUT_GLOBAL_LIMIT,
    HID_LAYOUT_USAGE_LIMIT, HID_LAYOUT_REPORT_LIMIT, HID_LAYOUT_FIELD_LIMIT,
    HID_LAYOUT_INPUT_LIMIT, HID_LAYOUT_ARITHMETIC, HID_LAYOUT_NO_MOUSE,
    HID_LAYOUT_MISSING_XY, HID_LAYOUT_ABSOLUTE_AXIS, HID_LAYOUT_BUTTON_ARRAY,
    HID_LAYOUT_BUTTON_LIMIT, HID_LAYOUT_FIELD_WIDTH, HID_LAYOUT_LOGICAL_RANGE,
    HID_LAYOUT_FIELD_FLAGS, HID_LAYOUT_DUPLICATE_USAGE, HID_LAYOUT_WHEEL_FEATURE,
    HID_LAYOUT_APPLICATION_LIMIT
} hid_layout_result_t;
typedef struct {
    uint16_t bit_offset, usage_page, usage, flags;
    int32_t logical_min;
    uint32_t logical_max;
    uint16_t report_count;
    uint8_t bits, report_index, application, reserved;
} hid_field_t;
typedef struct {
    uint32_t input_bits, output_bits, feature_bits;
    uint8_t id, reserved[3];
} hid_report_layout_t;
typedef struct {
    uint32_t buttons;
    hid_layout_result_t result;
    uint16_t fields;
    uint8_t has_x, has_y, has_wheel, has_pan;
    uint16_t collection_number;
} hid_application_t;
typedef struct {
    hid_field_t fields[HID_LAYOUT_FIELD_MAX];
    hid_report_layout_t reports[HID_LAYOUT_REPORT_MAX];
    hid_application_t applications[HID_LAYOUT_APPLICATION_MAX];
    uint16_t field_count;
    uint8_t report_count, application_count, selected_application;
    bool ready, has_report_ids, partial, parsed; // parsed != supported; complete descriptor syntax only
    hid_layout_result_t result;
    int32_t parser_error;
} hid_layout_t;
extern const uint32_t fusion_hid_layout_contract[12]; // inspected in the linked ARM ELF
// Core1 only. No allocation, USB types or retained descriptor pointers.
// On failure out is invalidated; it cannot reuse a previously successful layout.
hid_layout_result_t hid_layout_build(const uint8_t *descriptor, size_t length, hid_layout_t *out);
const char *hid_layout_result_name(hid_layout_result_t result);
#ifdef __cplusplus
}
#endif
#endif
