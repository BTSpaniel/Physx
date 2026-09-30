// SPDX-License-Identifier: MIT
// Generic buffer bookkeeping. This header contains NO physics solver.
#pragma once
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <new>

namespace pr {
enum Status : int { OK=0, INVALID=-1, FULL=-2, DUPLICATE=-3, NOT_FOUND=-4, READ_FAILED=-5 };
class PoseCache {
    struct Entry { void* actor; std::uint32_t id; };
    std::unique_ptr<Entry[]> entries_;
    std::unique_ptr<std::uint32_t[]> ids_;
    std::unique_ptr<float[]> poses_;
    std::uint32_t capacity_=0, count_=0;
public:
    PoseCache() = default;
    PoseCache(const PoseCache&) = delete;
    PoseCache& operator=(const PoseCache&) = delete;
    bool init(std::uint32_t capacity) noexcept {
        if (capacity_ || !capacity) return false;
        const auto max = std::numeric_limits<std::size_t>::max();
        const std::size_t n = capacity;
        if (n > max / (7*sizeof(float)) || n > max/sizeof(Entry)) return false;
        std::unique_ptr<Entry[]> entries(new(std::nothrow) Entry[capacity]);
        std::unique_ptr<std::uint32_t[]> ids(new(std::nothrow) std::uint32_t[capacity]);
        std::unique_ptr<float[]> poses(new(std::nothrow) float[std::size_t(capacity)*7]);
        if (!entries || !ids || !poses) return false;
        entries_=std::move(entries); ids_=std::move(ids); poses_=std::move(poses);
        capacity_=capacity;
        return true;
    }
    int add(std::uint32_t id, void* actor) noexcept {
        if (!id || !actor || !capacity_) return INVALID;
        for (std::uint32_t i=0; i<count_; ++i)
            if (entries_[i].id==id || entries_[i].actor==actor) return DUPLICATE;
        if (count_==capacity_) return FULL;
        entries_[count_++]={actor,id};
        return OK;
    }
    int remove(std::uint32_t id) noexcept {
        for (std::uint32_t i=0; i<count_; ++i) if (entries_[i].id==id) {
            // Keep registration order predictable. Removal is O(n); snapshot is O(n).
            for (std::uint32_t j=i+1; j<count_; ++j) entries_[j-1]=entries_[j];
            --count_; return OK;
        }
        return NOT_FOUND;
    }
    using Reader = bool(*)(void*, float*);
    int snapshot(Reader read) noexcept {
        if (!read || !capacity_) return INVALID;
        for (std::uint32_t i=0; i<count_; ++i) {
            ids_[i]=entries_[i].id;
            if (!read(entries_[i].actor, poses_.get()+std::size_t(i)*7)) return READ_FAILED;
        }
        return OK;
    }
    std::uint32_t count() const noexcept { return count_; }
    std::uint32_t capacity() const noexcept { return capacity_; }
    const float* poses() const noexcept { return poses_.get(); }
    const std::uint32_t* ids() const noexcept { return ids_.get(); }
};
}
