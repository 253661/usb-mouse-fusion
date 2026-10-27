#include <string.h>
#include "host_pio_state.h"
bool host_pio_clear_session(root_port_t *root,endpoint_t *eps,usb_device_t *devices) {
    if (!root || !eps || !devices) return false;
    for (unsigned i=0;i<PIO_USB_EP_POOL_CNT;++i) if (eps[i].transfer_started) return false;
    memset(eps,0,sizeof(endpoint_t)*PIO_USB_EP_POOL_CNT);
    memset(devices,0,sizeof(usb_device_t)*PIO_USB_DEVICE_CNT);
    root->connected=false; root->suspended=true; root->addr0_exists=false;
    root->event=EVENT_NONE; root->root_device=NULL;
    root->ints=root->ep_complete=root->ep_error=root->ep_stalled=root->ep_continue=0;
    root->dev_addr=0; root->setup_packet=NULL;
    return true;
}
