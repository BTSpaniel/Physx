// SPDX-License-Identifier: MIT
#ifndef PR_RUST_CORE_H
#define PR_RUST_CORE_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Internal Rust ABI 2. The public JS-facing pr_bulk ABI remains 1. */
typedef struct PrEntry { uint32_t id; uint32_t reserved; void* actor; } PrEntry;
typedef struct PrCache {
    PrEntry* entries;
    uint32_t* ids;
    float* poses;
    uint32_t* scratch_ids;
    float* scratch_poses;
    uint32_t capacity;
    uint32_t count;
    uint32_t published;
    uint32_t initialized;
} PrCache;
typedef int32_t (*PrPoseReader)(void* actor, float* seven_floats);
uint32_t prr_abi(void);
uint32_t prr_pointer_size(void);
uint32_t prr_entry_size(void);
uint32_t prr_cache_size(void);
uint32_t prr_capacity_valid(uint32_t capacity);
uint32_t prr_simd128_enabled(void);
int32_t prr_flow_seed(uint32_t* values, uint32_t count);
int32_t prr_flow_verify(const uint32_t* values, uint32_t count);
int32_t prr_init(PrCache*, uint32_t, PrEntry*, uint32_t*, float*, uint32_t*, float*);
int32_t prr_add(PrCache*, uint32_t id, void* actor);
int32_t prr_remove(PrCache*, uint32_t id);
int32_t prr_snapshot(PrCache*, PrPoseReader);
int32_t prr_clear(PrCache*);
/* All buffers are caller-owned and must be disjoint and live. Serialize all
   calls on each context; callbacks must not unwind or reenter. Actor pointers
   are borrowed. Invalid arbitrary addresses cannot be safely probed. */
#ifdef __cplusplus
}
#endif
#endif
