// C4-L4 native schematic/PCB: U2 W25Q16JVSSIQ (2MiB), Y1 12MHz.
// No official Pico LED, UART or PIO USB board defaults.
// This header is also included by assembler; use preprocessor directives only.
#ifndef _BOARDS_FUSION_C4_L4_H
#define _BOARDS_FUSION_C4_L4_H

#define PICO_RP2040 1
#define PICO_XOSC_HZ 12000000
#define PICO_FLASH_SIZE_BYTES (2 * 1024 * 1024)
// pico_board_cmake_set(PICO_FLASH_SIZE_BYTES, 2097152)
#define PICO_BOOT_STAGE2_CHOOSE_GENERIC_03H 1
// Standard 03h serial read: no quad-enable status-register programming.
// SDK default clk_sys is 125MHz; Flash SCK is 125MHz / 8 = 15.625MHz.
#define PICO_FLASH_SPI_CLKDIV 8

#define FUSION_AI_TX_PIN 0
#define FUSION_AI_RX_PIN 1
#define FUSION_HOST_DP_PIN 16
#define FUSION_HOST_DM_PIN 17
#define FUSION_HOST_EN_PIN 18
#define FUSION_HOST_FAULT_N_PIN 19
#define FUSION_TARGET_VBUS_PRESENT_PIN 20
#define FUSION_HOST_DATA_OE_N_PIN 21

#endif
