#ifndef FUSION_HOST_PIO_STATE_H
#define FUSION_HOST_PIO_STATE_H
#include <stdbool.h>
#include "../rp2040/vendor/pico-pio-usb/src/usb_definitions.h"
#ifdef __cplusplus
extern "C" {
#endif
// Frame gate closed, Core1 IRQ returned. Preserves pin/program/resource configuration.
bool host_pio_clear_session(root_port_t *root, endpoint_t *endpoints, usb_device_t *devices);
#ifdef __cplusplus
}
#endif
#endif
