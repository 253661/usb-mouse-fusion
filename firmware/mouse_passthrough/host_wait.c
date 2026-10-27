#include "host_session.h"
extern uint32_t fusion_host_time_ms(void);
extern void fusion_host_sleep_1ms(void);
bool fusion_host_delay_ms(uint32_t ms) {
    const uint32_t start=fusion_host_time_ms();
    const uint32_t generation=fusion_usbh_generation();
    while ((uint32_t)(fusion_host_time_ms()-start)<ms) {
        if (fusion_host_cancel_requested() || fusion_usbh_generation()!=generation) return false;
        fusion_host_sleep_1ms(); // Core1 only; normal reset/debounce duration retained
    }
    return !fusion_host_cancel_requested() && fusion_usbh_generation()==generation;
}
