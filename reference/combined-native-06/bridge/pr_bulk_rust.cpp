// SPDX-License-Identifier: MIT
// Compile this + pr_pose_core.a INTO the same module as WebIDL/PhysX.
// Real PhysX adapter; not a fake solver. Compilation needs actual SDK headers.
#include "PxPhysicsAPI.h"
#include "pr_rust_core.h"
#include <cstdint>
#include <memory>
#include <new>
#include <cmath>
#include <emscripten/heap.h>
#include <emscripten/emscripten.h>

namespace {
constexpr int OK = 0, INVALID = -1;
struct Context {
    std::uint32_t handle = 0;
    PrCache cache{};
    std::unique_ptr<PrEntry[]> entries;
    std::unique_ptr<std::uint32_t[]> ids, scratch_ids;
    std::unique_ptr<float[]> poses, scratch_poses;
    Context* next = nullptr;
    bool init(std::uint32_t n) noexcept {
        if (prr_abi() != 2 || prr_pointer_size() != sizeof(void*) ||
            prr_entry_size() != sizeof(PrEntry) || prr_cache_size() != sizeof(PrCache) ||
            !prr_capacity_valid(n)) return false;
        entries.reset(new(std::nothrow) PrEntry[n]);
        ids.reset(new(std::nothrow) std::uint32_t[n]);
        scratch_ids.reset(new(std::nothrow) std::uint32_t[n]);
        poses.reset(new(std::nothrow) float[std::size_t(n)*7]);
        scratch_poses.reset(new(std::nothrow) float[std::size_t(n)*7]);
        if (!entries || !ids || !scratch_ids || !poses || !scratch_poses) return false;
        return prr_init(&cache, n, entries.get(), ids.get(), poses.get(),
                        scratch_ids.get(), scratch_poses.get()) == OK;
    }
};
// Public handles aren't reused. This adapter is restricted to ONE owning worker
// for the entire module, like the existing non-pthread PhysX build.
Context* head = nullptr;
std::uint32_t next_handle = 1;
Context* get(std::uint32_t h) noexcept {
    for (auto* p=head; p; p=p->next) if (p->handle == h) return p;
    return nullptr;
}
}
// Explicit C linkage matches Rust's unsafe extern "C" callback.
extern "C" std::int32_t pr_read_physx_pose(void* actor, float* out) {
    // Caller unregisters BEFORE PxRigidActor::release; call after fetchResults.
    const auto t = static_cast<physx::PxRigidActor*>(actor)->getGlobalPose();
    out[0]=t.p.x; out[1]=t.p.y; out[2]=t.p.z;
    out[3]=t.q.x; out[4]=t.q.y; out[5]=t.q.z; out[6]=t.q.w;
    return OK; // Rust checks all seven components and publishes transactionally.
}
extern "C" {
EMSCRIPTEN_KEEPALIVE std::uint32_t pr_bulk_abi() { return 1; }
EMSCRIPTEN_KEEPALIVE std::uint32_t pr_bulk_backend() { return 2; } // 2 = Rust core
EMSCRIPTEN_KEEPALIVE std::uint32_t pr_bulk_sdk_version() { return PX_PHYSICS_VERSION; }
EMSCRIPTEN_KEEPALIVE std::uint32_t pr_flow_stage_abi() { return 1; }
EMSCRIPTEN_KEEPALIVE std::uint32_t pr_flow_simd_enabled() { return prr_simd128_enabled(); }
EMSCRIPTEN_KEEPALIVE std::uint32_t pr_flow_boundary_convex_abi() { return 1; }
// Borrow a LIVE PxShape after fetchResults. Output is scaled shape-local data:
// lower.xyz, upper.xyz, then unit planes n.xyz,d with dot(n,x)<=d. Actor and
// shape-local poses are intentionally separate. No collision skin is added.
// A null output with capacity0 queries the plane count; success also returns
// that count. -1 means invalid input/geometry; -2 means insufficient capacity.
EMSCRIPTEN_KEEPALIVE int pr_flow_boundary_convex(void* shapePointer, float* output, std::uint32_t capacity)
{
    const auto address = reinterpret_cast<std::uintptr_t>(shapePointer);
    const auto heapSize = emscripten_get_heap_size();
    if (!shapePointer || address % alignof(physx::PxShape) || address > heapSize || sizeof(physx::PxShape) > heapSize - address) return -1;
    auto* shape = static_cast<physx::PxShape*>(shapePointer);
    const auto& baseGeometry = shape->getGeometry();
    if (baseGeometry.getType() != physx::PxGeometryType::eCONVEXMESH) return -1;
    const auto& geometry = static_cast<const physx::PxConvexMeshGeometry&>(baseGeometry);
    if (!geometry.isValid() || !geometry.convexMesh) return -1;
    const auto count = geometry.convexMesh->getNbPolygons();
    if (!count || count > 256) return -1;
    if (!output && capacity == 0) return static_cast<int>(count);
    const std::uint32_t required = 6 + count * 4;
    if (capacity < required) return -2;
    const auto outputAddress = reinterpret_cast<std::uintptr_t>(output);
    if (!output || outputAddress % alignof(float) || outputAddress > heapSize || required > (heapSize - outputAddress) / sizeof(float)) return -1;
    float values[6 + 256 * 4];
    auto bounds = physx::PxBounds3::empty();
    const auto* vertices = geometry.convexMesh->getVertices();
    for (physx::PxU32 i = 0; i < geometry.convexMesh->getNbVertices(); ++i)
    {
        const auto point = geometry.scale.transform(vertices[i]);
        if (!point.isFinite()) return -1;
        bounds.include(point);
    }
    if (!bounds.isValid() || bounds.isEmpty()) return -1;
    values[0] = bounds.minimum.x; values[1] = bounds.minimum.y; values[2] = bounds.minimum.z;
    values[3] = bounds.maximum.x; values[4] = bounds.maximum.y; values[5] = bounds.maximum.z;
    const auto normalTransform = geometry.scale.getInverse().toMat33().getTranspose();
    for (physx::PxU32 i = 0; i < count; ++i)
    {
        physx::PxHullPolygon polygon;
        if (!geometry.convexMesh->getPolygonData(i, polygon)) return -1;
        const auto normal = normalTransform * physx::PxVec3(polygon.mPlane[0], polygon.mPlane[1], polygon.mPlane[2]);
        const float length = normal.magnitude(), distance = -polygon.mPlane[3] / length;
        if (!normal.isFinite() || !std::isfinite(length) || length <= 0 || !std::isfinite(distance)) return -1;
        values[6 + i * 4] = normal.x / length; values[7 + i * 4] = normal.y / length;
        values[8 + i * 4] = normal.z / length; values[9 + i * 4] = distance;
    }
    for (std::uint32_t i = 0; i < required; ++i) output[i] = values[i];
    return static_cast<int>(count);
}
EMSCRIPTEN_KEEPALIVE int pr_flow_seed(std::uint32_t* values, std::uint32_t count) {
    return prr_flow_seed(values, count);
}
EMSCRIPTEN_KEEPALIVE int pr_flow_verify(const std::uint32_t* values, std::uint32_t count) {
    return prr_flow_verify(values, count);
}
EMSCRIPTEN_KEEPALIVE std::uint32_t pr_bulk_create(std::uint32_t capacity) {
    if (!next_handle) return 0;
    auto c = std::unique_ptr<Context>(new(std::nothrow) Context);
    if (!c || !c->init(capacity)) return 0;
    c->handle = next_handle++;
    c->next = head; head = c.release(); return head->handle;
}
EMSCRIPTEN_KEEPALIVE int pr_bulk_add(std::uint32_t h, std::uint32_t id, void* actor) {
    auto* c=get(h); return c ? prr_add(&c->cache,id,actor) : INVALID;
}
EMSCRIPTEN_KEEPALIVE int pr_bulk_remove(std::uint32_t h, std::uint32_t id) {
    auto* c=get(h); return c ? prr_remove(&c->cache,id) : INVALID;
}
EMSCRIPTEN_KEEPALIVE int pr_bulk_snapshot(std::uint32_t h) {
    auto* c=get(h); return c ? prr_snapshot(&c->cache,pr_read_physx_pose) : INVALID;
}
EMSCRIPTEN_KEEPALIVE std::uint32_t pr_bulk_count(std::uint32_t h) {
    auto* c=get(h); return c ? c->cache.published : 0;
}
EMSCRIPTEN_KEEPALIVE const float* pr_bulk_pose_ptr(std::uint32_t h) {
    auto* c=get(h); return c ? c->cache.poses : nullptr;
}
EMSCRIPTEN_KEEPALIVE const std::uint32_t* pr_bulk_ids_ptr(std::uint32_t h) {
    auto* c=get(h); return c ? c->cache.ids : nullptr;
}
EMSCRIPTEN_KEEPALIVE int pr_bulk_destroy(std::uint32_t h) {
    auto** p=&head;
    while (*p && (*p)->handle!=h) p=&(*p)->next;
    if (!*p) return INVALID;
    auto* c=*p; *p=c->next; delete c; return OK;
}
}
