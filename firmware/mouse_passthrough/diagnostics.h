#ifndef FUSION_DIAGNOSTICS_H
#define FUSION_DIAGNOSTICS_H
#include <stdbool.h>
#include <stdint.h>
void diagnostics_tick(uint64_t now_us);
void diagnostics_set_clock_status(bool ok);
#endif
