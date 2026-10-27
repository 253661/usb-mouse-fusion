#ifndef FUSION_PROJECT_CONFIG_H
#define FUSION_PROJECT_CONFIG_H
#ifndef FUSION_DIAGNOSTIC_CDC
#define FUSION_DIAGNOSTIC_CDC 1
#endif
#ifndef FUSION_CONTROLLED_J2
#define FUSION_CONTROLLED_J2 0
#endif
#ifndef FUSION_STANDALONE_FLASH
#define FUSION_STANDALONE_FLASH 0
#endif
#ifndef FUSION_J4_LINK
#define FUSION_J4_LINK 0
#endif
#ifndef FUSION_J4_AUX
#define FUSION_J4_AUX 0
#endif
#ifndef FUSION_TRAJECTORY
#define FUSION_TRAJECTORY 0
#endif
#ifndef FUSION_PERSISTENCE
#define FUSION_PERSISTENCE 0
#endif
#if (FUSION_PERSISTENCE != 0 && FUSION_PERSISTENCE != 1) || (FUSION_PERSISTENCE && !FUSION_TRAJECTORY)
#error Persistent presets require the explicit trajectory Flash profile
#endif
#if (FUSION_TRAJECTORY != 0 && FUSION_TRAJECTORY != 1) || (FUSION_TRAJECTORY && !FUSION_J4_AUX)
#error Preset runtime requires the explicit API profile
#endif
#if (FUSION_J4_AUX != 0 && FUSION_J4_AUX != 1) || (FUSION_J4_AUX && !FUSION_J4_LINK)
#error API auxiliary ingress requires the explicit J4 profile
#endif
#if FUSION_J4_LINK != 0 && FUSION_J4_LINK != 1
#error Invalid J4 link profile
#endif
#if FUSION_J4_LINK && !FUSION_STANDALONE_FLASH
#error J4 link is an explicit Standalone Flash profile
#endif
#if FUSION_STANDALONE_FLASH != 0 && FUSION_STANDALONE_FLASH != 1
#error Invalid standalone Flash profile
#endif
#if FUSION_STANDALONE_FLASH && (FUSION_CONTROLLED_J2 || FUSION_DIAGNOSTIC_CDC)
#error Standalone Flash requires HID-only and cannot enable the controlled CDC profile
#endif
#if FUSION_CONTROLLED_J2 != 0 && FUSION_CONTROLLED_J2 != 1
#error Invalid controlled J2 profile
#endif
#if FUSION_CONTROLLED_J2 && !FUSION_DIAGNOSTIC_CDC
#error Controlled J2 RAM tests require the explicit CDC command interface
#endif
#define FUSION_TASK1_J2_LOCKED (!(FUSION_CONTROLLED_J2 || FUSION_STANDALONE_FLASH))
// J2 stays off until J1 SET_CONFIGURATION has granted the declared USB budget.
#define FUSION_USB_MAX_POWER_MA (FUSION_STANDALONE_FLASH ? 500 : 100)
#define FUSION_HOST_RHPORT 1
#define FUSION_DEVICE_RHPORT 0
#define FUSION_SYS_HZ 120000000u
#define FUSION_USB_HZ 48000000u
#define FUSION_PIO_INDEX 0
#define FUSION_PIO_SM_TX 0
#define FUSION_PIO_SM_RX 1
#define FUSION_PIO_SM_EOP 2
#define FUSION_DMA_CHANNEL 0
#define FUSION_ALARM_NUM 2
#define FUSION_HOST_FRAME_US 1000
#define FUSION_CONTROL_TIMEOUT_US 1000000u
#define FUSION_ENUM_TIMEOUT_US 5000000u
#define FUSION_CAPTURE_DESC_BYTES 1024
#define FUSION_CAPTURE_REPORT_BYTES 64
#define FUSION_HOST_INTERFACE_MAX 8
// pid.codes private test PID: anyone may use in-house, MUST NOT redistribute/sell/manufacture.
// It is NOT globally unique. See README and evidence usb_identity_policy.json.
#ifndef FUSION_USB_VID
#define FUSION_USB_VID 0x1209
#endif
#ifndef FUSION_USB_PID
#define FUSION_USB_PID 0x0001
#endif
#if FUSION_USB_VID <= 0 || FUSION_USB_VID >= 0xffff || FUSION_USB_PID <= 0 || FUSION_USB_PID >= 0xffff
#error Invalid USB product identity
#endif
#endif
