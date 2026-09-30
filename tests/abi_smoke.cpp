// SPDX-License-Identifier: MIT
// REAL Rust/C++ ABI probe. No PhysX headers, solver or imitation physics.
// Link this file to the ACTUAL pr_pose_core library; never to a substitute.
#include "pr_rust_core.h"
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>
#ifdef __EMSCRIPTEN__
#include <emscripten/emscripten.h>
#define PR_EXPORT EMSCRIPTEN_KEEPALIVE
#else
#define PR_EXPORT
#endif
namespace {
struct Storage {
    PrCache cache{};
    PrEntry entries[3]{};
    std::uint32_t ids[3]{}, scratch_ids[3]{};
    float poses[21]{}, scratch_poses[21]{};
    int init() {
        return prr_init(&cache, 3, entries, ids, poses, scratch_ids, scratch_poses);
    }
};
std::uint32_t checks=0, active_seed=0, active_step=0;
}
extern "C" std::int32_t pr_probe_reader(void* token, float* out) {
    const auto* pose=static_cast<const float*>(token);
    for (int i=0; i<7; ++i) out[i]=pose[i];
    return 0;
}
extern "C" std::int32_t pr_probe_incomplete(void*, float*) { return 0; }
extern "C" std::int32_t pr_probe_failed(void*, float*) { return -1; }
#define CHECK(condition) do { ++checks; if (!(condition)) return int(checks); } while (false)
extern "C" PR_EXPORT int pr_abi_selftest() {
    checks=0; active_seed=0; active_step=0;
    static_assert(sizeof(PrEntry)==8+sizeof(void*), "entry layout");
    static_assert(sizeof(PrCache)==16+5*sizeof(void*), "cache layout");
    static_assert(offsetof(PrEntry, actor)==8, "actor offset");
    static_assert(offsetof(PrCache, capacity)==5*sizeof(void*), "capacity offset");
    CHECK(prr_abi()==2);
    CHECK(prr_pointer_size()==sizeof(void*));
    CHECK(prr_entry_size()==sizeof(PrEntry));
    CHECK(prr_cache_size()==sizeof(PrCache));
    CHECK(prr_capacity_valid(0)==0);
    CHECK(prr_capacity_valid(3)==1);
    // Volatile operands force the quad-float support routines on Emscripten.
    // Rust's precompiled weak builtins once introduced EH imports here even
    // though the original pointer/callback-only probe passed.
    volatile long double wide_a=1.5L, wide_b=0.5L;
    CHECK(wide_a - wide_b == 1.0L);
    CHECK(wide_a / wide_b == 3.0L);
    Storage s;
    CHECK(s.init()==0);
    CHECK(prr_snapshot(&s.cache, pr_probe_reader)==0 && s.cache.published==0);
    float a[7]={0,2,0,0,0,0,1}, b[7]={0,3,0,0,0,0,1};
    float c[7]={0,4,0,0,0,0,1}, d[7]={0,5,0,0,0,0,1};
    CHECK(prr_add(&s.cache, UINT32_MAX, a)==0);
    CHECK(prr_add(&s.cache, 16777217u, b)==0);
    CHECK(prr_add(&s.cache, UINT32_MAX, c)==-3);
    CHECK(prr_add(&s.cache, 5u, a)==-3);
    CHECK(prr_add(&s.cache, 0u, c)==-1);
    CHECK(prr_add(&s.cache, 5u, nullptr)==-1);
    CHECK(prr_snapshot(&s.cache, pr_probe_reader)==0);
    CHECK(s.cache.published==2 && s.ids[0]==UINT32_MAX && s.ids[1]==16777217u);
    CHECK(s.poses[1]==2 && s.poses[8]==3 && s.poses[6]==1);
    a[1]=7; b[1]=std::numeric_limits<float>::quiet_NaN();
    CHECK(prr_snapshot(&s.cache, pr_probe_reader)==-5);
    CHECK(s.cache.published==2 && s.poses[1]==2 && s.poses[8]==3);
    CHECK(prr_snapshot(&s.cache, pr_probe_incomplete)==-5);
    CHECK(prr_snapshot(&s.cache, pr_probe_failed)==-5);
    CHECK(prr_snapshot(&s.cache, nullptr)==-1);
    CHECK(s.cache.published==2 && s.poses[1]==2);
    b[1]=3;
    CHECK(prr_add(&s.cache, 3u, c)==0 && s.cache.published==0);
    CHECK(prr_add(&s.cache, 4u, d)==-2 && s.cache.count==3);
    CHECK(prr_snapshot(&s.cache, pr_probe_reader)==0 && s.cache.published==3);
    CHECK(s.poses[1]==7 && s.ids[2]==3);
    CHECK(prr_remove(&s.cache, 16777217u)==0 && s.cache.published==0);
    CHECK(prr_snapshot(&s.cache, pr_probe_reader)==0 && s.cache.published==2);
    CHECK(s.ids[0]==UINT32_MAX && s.ids[1]==3 && s.poses[8]==4);
    CHECK(prr_remove(&s.cache, 16777217u)==-4);
    CHECK(prr_clear(&s.cache)==0 && s.cache.count==0 && s.cache.published==0);
    CHECK(prr_add(&s.cache, 8u, d)==0);
    CHECK(prr_snapshot(&s.cache, pr_probe_reader)==0 && s.ids[0]==8);
    CHECK(prr_init(&s.cache,3,s.entries,s.ids,s.poses,s.ids,s.scratch_poses)==-1);
    CHECK(s.cache.count==1 && s.cache.published==1); // invalid re-init left context unchanged
    CHECK(prr_remove(nullptr, 1u)==-1 && prr_clear(nullptr)==-1);
    // Every non-finite component must preserve the last publication, including
    // rows beyond published count. These are checks against the real Rust core.
    for (int j=7; j<21; ++j) s.poses[j]=12345.25f;
    s.ids[1]=0xa5a5a5a5u; s.ids[2]=0x5a5a5a5au;
    for (int component=0; component<7; ++component) {
        const float saved=d[component];
        const float bads[]={std::numeric_limits<float>::quiet_NaN(),
            std::numeric_limits<float>::infinity(), -std::numeric_limits<float>::infinity()};
        for (float bad:bads) {
            d[component]=bad;
            CHECK(prr_snapshot(&s.cache,pr_probe_reader)==-5);
            CHECK(s.cache.published==1 && s.ids[0]==8 && s.poses[1]==5);
            for (int j=7;j<21;++j) CHECK(s.poses[j]==12345.25f);
            CHECK(s.ids[1]==0xa5a5a5a5u && s.ids[2]==0x5a5a5a5au);
            d[component]=saved;
            CHECK(prr_snapshot(&s.cache,pr_probe_reader)==0);
        }
    }
    // Ordered, model-based churn. All token pointers address live local arrays.
    for (active_seed=1; active_seed<=8; ++active_seed) {
        Storage batch; CHECK(batch.init()==0);
        float tokens[6][7]{};
        for (unsigned i=0;i<6;++i) {tokens[i][0]=float(i);tokens[i][6]=1;}
        const std::uint32_t ids[]={1,2,3,4,16777217u,UINT32_MAX};
        unsigned model[3]{};unsigned size=0;std::uint32_t rng=active_seed;
        for (active_step=0; active_step<512; ++active_step) {
            rng=rng*1664525u+1013904223u;
            const unsigned index=(rng>>8)%6u, op=(rng>>24)%3u;
            unsigned at=0;while(at<size && model[at]!=index) ++at;
            if (op==0) {
                const int expected=at<size?-3:(size==3?-2:0);
                CHECK(prr_add(&batch.cache,ids[index],tokens[index])==expected);
                if (expected==0) model[size++]=index;
            } else if (op==1) {
                CHECK(prr_remove(&batch.cache,ids[index])==(at<size?0:-4));
                if(at<size) {for(unsigned j=at+1;j<size;++j) model[j-1]=model[j];--size;}
            } else tokens[index][1]=float(active_step);
            CHECK(prr_snapshot(&batch.cache,pr_probe_reader)==0);
            CHECK(batch.cache.count==size && batch.cache.published==size);
            for (unsigned j=0;j<size;++j) {
                CHECK(batch.ids[j]==ids[model[j]]);
                for(unsigned k=0;k<7;++k) CHECK(batch.poses[j*7+k]==tokens[model[j]][k]);
            }
        }
    }
    active_seed=0;active_step=0;
    return 0;
}
extern "C" PR_EXPORT std::uint32_t pr_abi_checks() { return checks; }
extern "C" PR_EXPORT std::uint32_t pr_abi_seed() { return active_seed; }
extern "C" PR_EXPORT std::uint32_t pr_abi_step() { return active_step; }
#ifndef __EMSCRIPTEN__
int main() {
    const int result=pr_abi_selftest();
    std::printf("{\"scope\":\"real Rust/C++ ABI, not physics\",\"checks\":%u,\"failure\":%d}\n",checks,result);
    if(result) std::printf("Failure seed=%u step=%u\n",active_seed,active_step);
    return result ? 1 : 0;
}
#endif
