#include "mouse_report.h"
void mouse_report_encode(const mouse_report_t *report, uint8_t bytes[MOUSE_REPORT_BYTES]) {
    const uint16_t x = (uint16_t)report->x, y = (uint16_t)report->y;
    bytes[0]=(uint8_t)report->buttons; bytes[1]=(uint8_t)(report->buttons>>8);
    bytes[2]=(uint8_t)x; bytes[3]=(uint8_t)(x>>8);
    bytes[4]=(uint8_t)y; bytes[5]=(uint8_t)(y>>8);
    bytes[6]=(uint8_t)report->wheel; bytes[7]=(uint8_t)report->pan;
}
