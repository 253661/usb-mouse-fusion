#include "j4_uart.h"
#include "pico/stdlib.h"
#include "pico/unique_id.h"
#include "hardware/uart.h"
#if FUSION_J4_AUX
#include "j4_aux_bridge.h"
#include "event_bus.h"
static uint32_t target_generation;
#endif
static j4_link_t link;
uint32_t const fusion_j4_contract[]={0x4a345031,J4_VERSION,0,FUSION_AI_TX_PIN,FUSION_AI_RX_PIN,
    J4_LED_PIN,J4_BAUD,8,1,0,J4_RX_BUDGET,J4_TX_BUDGET,J4_PAYLOAD_MAX,J4_DEFAULT_IDLE_MS,J4_FRAGMENT_TIMEOUT_US};
_Static_assert(FUSION_AI_TX_PIN==0&&FUSION_AI_RX_PIN==1,"Current isolated UART0 map required");
void j4_uart_init(void) {
    pico_unique_board_id_t id;pico_get_unique_board_id(&id);j4_link_init(&link,id.id);
#if FUSION_J4_AUX
    j4_aux_bridge_attach(&link);target_generation=event_bus_target_generation();
#endif
    (void)uart_init(uart0,J4_BAUD);uart_set_format(uart0,8,1,UART_PARITY_NONE);
    uart_set_hw_flow(uart0,false,false);uart_set_fifo_enabled(uart0,true);
    gpio_set_function(FUSION_AI_TX_PIN,GPIO_FUNC_UART);gpio_set_function(FUSION_AI_RX_PIN,GPIO_FUNC_UART);
    gpio_init(J4_LED_PIN);gpio_put(J4_LED_PIN,false);gpio_set_dir(J4_LED_PIN,GPIO_OUT);
}
void j4_uart_tick(uint64_t now,const j4_snapshot_t *snapshot) {
#if FUSION_J4_AUX
    const uint32_t current=event_bus_target_generation();
    if(current!=target_generation){j4_link_transport_reset(&link);target_generation=current;}
#endif
    j4_link_tick(&link,now);
    if(uart_get_hw(uart0)->rsr){uart_get_hw(uart0)->rsr=0;j4_link_transport_reset(&link);}
    for(unsigned i=0;i<J4_RX_BUDGET&&uart_is_readable(uart0);++i){
        const uint32_t raw=uart_get_hw(uart0)->dr;
        if(raw&0xf00){j4_link_transport_reset(&link);continue;}
        j4_link_feed(&link,(uint8_t)raw,now,snapshot);
    }
    for(unsigned i=0;i<J4_TX_BUDGET&&uart_is_writable(uart0);++i){
        uint8_t byte;if(!j4_link_take_tx(&link,&byte,1))break;
        uart_get_hw(uart0)->dr=byte; // FIFO readiness checked; no blocking SDK helper
    }
    gpio_put(J4_LED_PIN,j4_link_identifying(&link,now));
}
bool j4_uart_tx_idle(void){
    return link.tx_sent>=link.tx_size&&link.realtime_tx_sent>=link.realtime_tx_size&&!(uart_get_hw(uart0)->fr&UART_UARTFR_BUSY_BITS);
}
