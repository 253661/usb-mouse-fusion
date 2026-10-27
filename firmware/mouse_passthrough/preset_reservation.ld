/* Supplemental assertion; the SDK still owns all output sections. */
ASSERT(__flash_binary_end <= 0x10080000, "Firmware overlaps reserved preset banks");
