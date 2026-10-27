#include "pico/stdlib.h"
#include "board_io.h"
#include "project_config.h"
// Pins come from the read-only current cdc_alive/boards/c4_l4.h board header.
void board_io_init_safe(void) {
    gpio_init(FUSION_HOST_EN_PIN);
    gpio_put(FUSION_HOST_EN_PIN, false);
    gpio_set_dir(FUSION_HOST_EN_PIN, GPIO_OUT);
    gpio_init(FUSION_HOST_DATA_OE_N_PIN);
    gpio_put(FUSION_HOST_DATA_OE_N_PIN, true);
    gpio_set_dir(FUSION_HOST_DATA_OE_N_PIN, GPIO_OUT);
    gpio_init(FUSION_HOST_FAULT_N_PIN);
    gpio_set_dir(FUSION_HOST_FAULT_N_PIN, GPIO_IN);
    gpio_disable_pulls(FUSION_HOST_FAULT_N_PIN); // external R3, no guessed bias
    gpio_init(FUSION_TARGET_VBUS_PRESENT_PIN);
    gpio_set_dir(FUSION_TARGET_VBUS_PRESENT_PIN, GPIO_IN);
    gpio_disable_pulls(FUSION_TARGET_VBUS_PRESENT_PIN);
}
void board_io_force_safe(void) {
    // Isolate data before removing power, including every controlled OFF/fault path.
    gpio_put(FUSION_HOST_DATA_OE_N_PIN, true);
    gpio_put(FUSION_HOST_EN_PIN, false);
}
board_io_status_t board_io_snapshot(void) {
    return (board_io_status_t){
        gpio_get_out_level(FUSION_HOST_EN_PIN), gpio_get_out_level(FUSION_HOST_DATA_OE_N_PIN),
        gpio_get(FUSION_HOST_EN_PIN), gpio_get(FUSION_HOST_DATA_OE_N_PIN),
        gpio_get(FUSION_HOST_FAULT_N_PIN), gpio_get(FUSION_TARGET_VBUS_PRESENT_PIN)};
}
board_io_apply_result_t board_io_apply(bool en,bool oe_n) {
#if FUSION_TASK1_J2_LOCKED
    (void)oe_n; board_io_force_safe();
    return en ? BOARD_IO_REJECTED_LOCKED : BOARD_IO_APPLIED;
#else
    // Only the controlled or standalone profile reaches this branch. The policy owns
    // settle/ACK permission; recheck the physical safety inputs at the GPIO boundary.
    if (!en) { board_io_force_safe(); return BOARD_IO_APPLIED; }
    const bool fault_n=gpio_get(FUSION_HOST_FAULT_N_PIN);
    const bool target=gpio_get(FUSION_TARGET_VBUS_PRESENT_PIN);
    const unsigned rejection=(!fault_n ? BOARD_IO_REJECTED_FAULT : 0u) |
                             (!target ? BOARD_IO_REJECTED_TARGET : 0u);
    if (rejection) { board_io_force_safe(); return (board_io_apply_result_t)rejection; }
    gpio_put(FUSION_HOST_EN_PIN,true);
    gpio_put(FUSION_HOST_DATA_OE_N_PIN,oe_n);
    return BOARD_IO_APPLIED;
#endif
}
