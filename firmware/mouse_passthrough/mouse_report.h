#ifndef FUSION_MOUSE_REPORT_H
#define FUSION_MOUSE_REPORT_H
#include "bridge_types.h"
enum { MOUSE_REPORT_BYTES = 8 };
#ifdef __cplusplus
extern "C" {
#endif
void mouse_report_encode(const mouse_report_t *report, uint8_t bytes[MOUSE_REPORT_BYTES]);
#ifdef __cplusplus
}
#endif
#endif
