// SPDX-License-Identifier: MIT
// EXPERIMENTAL: compile into the SAME Emscripten module as the WebIDL bindings.
// This adapter requires the real PhysX SDK. It has NOT been compiled here.
#include "PxPhysicsAPI.h"
#include "pose_cache.hpp"
#include <emscripten/emscripten.h>
#include <cmath>
#include <cstdint>
#include <new>

namespace {
struct Context { std::uint32_t handle=0; pr::PoseCache cache; Context* next=nullptr; };
Context* head=nullptr;
std::uint32_t next_handle=1;
Context* get(std::uint32_t h) noexcept {
    for (auto* p=head; p; p=p->next) if (p->handle==h) return p;
    return nullptr;
}
bool read_pose(void* raw, float* out) {
    // Borrowed pointer: caller MUST remove actors BEFORE release and call only
    // after fetchResults(), never concurrently with simulation or destruction.
    const auto t=static_cast<physx::PxRigidActor*>(raw)->getGlobalPose();
    const float values[7]={t.p.x,t.p.y,t.p.z,t.q.x,t.q.y,t.q.z,t.q.w};
    for (int i=0;i<7;++i) { if (!std::isfinite(values[i])) return false; out[i]=values[i]; }
    return true;
}
}
extern "C" {
EMSCRIPTEN_KEEPALIVE std::uint32_t pr_bulk_abi() { return 1; }
EMSCRIPTEN_KEEPALIVE std::uint32_t pr_bulk_sdk_version() { return PX_PHYSICS_VERSION; }
EMSCRIPTEN_KEEPALIVE std::uint32_t pr_bulk_create(std::uint32_t capacity) {
    if (!next_handle) return 0; // Do not reuse stale handles after uint32 wrap.
    auto* c=new(std::nothrow) Context;
    if (!c) return 0;
    if (!c->cache.init(capacity)) { delete c; return 0; }
    c->handle=next_handle++; c->next=head; head=c; return c->handle;
}
EMSCRIPTEN_KEEPALIVE int pr_bulk_add(std::uint32_t h, std::uint32_t id, void* actor) {
    auto* c=get(h); return c ? c->cache.add(id,actor) : pr::INVALID;
}
EMSCRIPTEN_KEEPALIVE int pr_bulk_remove(std::uint32_t h, std::uint32_t id) {
    auto* c=get(h); return c ? c->cache.remove(id) : pr::INVALID;
}
EMSCRIPTEN_KEEPALIVE int pr_bulk_snapshot(std::uint32_t h) {
    auto* c=get(h); return c ? c->cache.snapshot(read_pose) : pr::INVALID;
}
EMSCRIPTEN_KEEPALIVE std::uint32_t pr_bulk_count(std::uint32_t h) {
    auto* c=get(h); return c ? c->cache.count() : 0;
}
EMSCRIPTEN_KEEPALIVE const float* pr_bulk_pose_ptr(std::uint32_t h) {
    auto* c=get(h); return c ? c->cache.poses() : nullptr;
}
EMSCRIPTEN_KEEPALIVE const std::uint32_t* pr_bulk_ids_ptr(std::uint32_t h) {
    auto* c=get(h); return c ? c->cache.ids() : nullptr;
}
EMSCRIPTEN_KEEPALIVE int pr_bulk_destroy(std::uint32_t h) {
    auto** p=&head;
    while (*p && (*p)->handle!=h) p=&(*p)->next;
    if (!*p) return pr::INVALID;
    auto* c=*p; *p=c->next; delete c; return pr::OK;
}
}
