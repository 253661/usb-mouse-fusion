#include <string.h>
#include "tusb.h"
#include "project_config.h"
#include "mouse_report.h"
// Default shared VID/PID is private in-house test only; build profiles may use authorized IDs.
// No device/brand identity or format is copied from the J2 mouse.
tusb_desc_device_t const fusion_device_descriptor={
    .bLength=sizeof(tusb_desc_device_t),.bDescriptorType=TUSB_DESC_DEVICE,.bcdUSB=0x0200,
#if FUSION_DIAGNOSTIC_CDC
    .bDeviceClass=TUSB_CLASS_MISC,.bDeviceSubClass=MISC_SUBCLASS_COMMON,.bDeviceProtocol=MISC_PROTOCOL_IAD,
#else
    .bDeviceClass=0,.bDeviceSubClass=0,.bDeviceProtocol=0,
#endif
    .bMaxPacketSize0=CFG_TUD_ENDPOINT0_SIZE,.idVendor=FUSION_USB_VID,.idProduct=FUSION_USB_PID,
    .bcdDevice=0x0100,.iManufacturer=1,.iProduct=2,.iSerialNumber=3,.bNumConfigurations=1
};
// Fixed 8 bytes: Button Usage 1..16, signed16 relative X/Y, signed8 Wheel/AC Pan.
// At least Usage 1..5 is required. Windows support above five still NEED VERIFY.
uint8_t const fusion_mouse_report_descriptor[]={
    0x05,0x01,0x09,0x02,0xa1,0x01,0x09,0x01,0xa1,0x00,
    0x05,0x09,0x19,0x01,0x29,0x10,0x15,0x00,0x25,0x01,0x75,0x01,0x95,0x10,0x81,0x02,
    0x05,0x01,0x09,0x30,0x09,0x31,0x16,0x00,0x80,0x26,0xff,0x7f,0x75,0x10,0x95,0x02,0x81,0x06,
    0x09,0x38,0x15,0x80,0x25,0x7f,0x75,0x08,0x95,0x01,0x81,0x06,
    0x05,0x0c,0x0a,0x38,0x02,0x95,0x01,0x81,0x06,0xc0,0xc0
};
uint8_t const fusion_configuration_descriptor[]={
#if FUSION_DIAGNOSTIC_CDC
    TUD_CONFIG_DESCRIPTOR(1,3,0,TUD_CONFIG_DESC_LEN+TUD_CDC_DESC_LEN+TUD_HID_DESC_LEN,0,FUSION_USB_MAX_POWER_MA),
    TUD_CDC_DESCRIPTOR(0,4,0x81,8,0x02,0x82,64),
    TUD_HID_DESCRIPTOR(2,5,HID_ITF_PROTOCOL_NONE,sizeof(fusion_mouse_report_descriptor),0x83,MOUSE_REPORT_BYTES,1)
#else
    TUD_CONFIG_DESCRIPTOR(1,1,0,TUD_CONFIG_DESC_LEN+TUD_HID_DESC_LEN,0,FUSION_USB_MAX_POWER_MA),
    TUD_HID_DESCRIPTOR(0,5,HID_ITF_PROTOCOL_NONE,sizeof(fusion_mouse_report_descriptor),0x81,MOUSE_REPORT_BYTES,1)
#endif
};
#ifndef FUSION_USB_MANUFACTURER
#define FUSION_USB_MANUFACTURER "USB Mouse Fusion"
#endif
const char fusion_usb_manufacturer[]=FUSION_USB_MANUFACTURER;
#ifdef FUSION_USB_PRODUCT
const char fusion_usb_product[]=FUSION_USB_PRODUCT;
#else
#if FUSION_STANDALONE_FLASH
const char fusion_usb_product[]="USB Mouse Fusion";
#elif FUSION_CONTROLLED_J2
const char fusion_usb_product[]="C4-L4 Controlled J2 RAM HID+CDC";
#elif FUSION_DIAGNOSTIC_CDC
const char fusion_usb_product[]="C4-L4 Task3 RAM Bridge HID+CDC";
#else
const char fusion_usb_product[]="C4-L4 Task3 RAM Bridge HID only";
#endif
#endif
#ifdef FUSION_USB_SERIAL
const char fusion_usb_serial[]=FUSION_USB_SERIAL;
#elif FUSION_STANDALONE_FLASH || FUSION_CONTROLLED_J2
// Default remains the successfully tested board identity; profiles are opt-in.
const char fusion_usb_serial[]="C4L4-J2-CTRL-RAM";
#elif FUSION_DIAGNOSTIC_CDC
const char fusion_usb_serial[]="C4L4-T3-RAM-CDC";
#else
const char fusion_usb_serial[]="C4L4-T3-RAM-HID";
#endif
uint8_t const *tud_descriptor_device_cb(void) { return (uint8_t const *)&fusion_device_descriptor; }
uint8_t const *tud_descriptor_configuration_cb(uint8_t index) { return index==0 ? fusion_configuration_descriptor : NULL; }
uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance) { return instance==0 ? fusion_mouse_report_descriptor : NULL; }
uint16_t const *tud_descriptor_string_cb(uint8_t index,uint16_t language) {
    (void)language;
    static uint16_t descriptor[64];
    static const char *const strings[]={NULL,fusion_usb_manufacturer,fusion_usb_product,fusion_usb_serial,"Diagnostic CDC","Standard Mouse"};
    if (index==0) { descriptor[0]=(TUSB_DESC_STRING<<8)|4; descriptor[1]=0x0409; return descriptor; }
    if (index>=sizeof(strings)/sizeof(strings[0])) return NULL;
    size_t length=strlen(strings[index]); if (length>63) length=63;
    for (size_t i=0;i<length;++i) descriptor[i+1]=(uint8_t)strings[index][i];
    descriptor[0]=(uint16_t)((TUSB_DESC_STRING<<8)|(2*length+2)); return descriptor;
}
