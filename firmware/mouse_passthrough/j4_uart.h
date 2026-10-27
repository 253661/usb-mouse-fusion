#ifndef FUSION_J4_UART_H
#define FUSION_J4_UART_H
#include "j4_link.h"
void j4_uart_init(void);
void j4_uart_tick(uint64_t now_us,const j4_snapshot_t *snapshot);
bool j4_uart_tx_idle(void);
#endif
