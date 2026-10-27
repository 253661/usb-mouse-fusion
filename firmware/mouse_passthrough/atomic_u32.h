#ifndef FUSION_ATOMIC_U32_H
#define FUSION_ATOMIC_U32_H
#include <stdint.h>
// All concurrent accesses use these operations; no volatile-only synchronization.
// Single writer per counter/index. GCC emits load/store + barriers on Cortex-M0+.
#ifdef _MSC_VER
#include <intrin.h>
static inline uint32_t fusion_load_u32(const uint32_t *p) {
    return (uint32_t)_InterlockedCompareExchange((volatile long *)p, 0, 0);
}
static inline void fusion_store_u32(uint32_t *p, uint32_t value) {
    (void)_InterlockedExchange((volatile long *)p, (long)value);
}
#else
static inline uint32_t fusion_load_u32(const uint32_t *p) { return __atomic_load_n(p, __ATOMIC_ACQUIRE); }
static inline void fusion_store_u32(uint32_t *p, uint32_t value) { __atomic_store_n(p, value, __ATOMIC_RELEASE); }
#endif
#endif
