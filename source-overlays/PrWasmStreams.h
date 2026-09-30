// Copyright (c) 2026 Particle Realms contributors.
// SPDX-License-Identifier: MIT
// PhysX 5.10 stream ABI, with a deliberately bounded wasm32/WebIDL interface.
// This is a source-port component, not an implementation of ovphysx.
#ifndef PR_WASM_STREAMS_H
#define PR_WASM_STREAMS_H

#include "foundation/PxIO.h"
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <limits>

namespace pr_web {

// A stream can never occupy all of a 2 GiB WASM heap. This upper bound is an
// addressability ceiling, not a reservation or a guarantee that allocation works.
constexpr std::uint32_t kMaxStreamBytes = 0x7fffffffU;
static_assert(sizeof(physx::PxU64) == 8, "PhysX 64-bit stream ABI required");
static_assert(sizeof(physx::PxU32) == 4, "32-bit WebIDL counts required");

class PxDefaultMemoryInputData final : public physx::PxInputData {
public:
    PxDefaultMemoryInputData(const physx::PxU8* data, physx::PxU32 length) noexcept
        : data_(data), length_(length) {
        if (length > kMaxStreamBytes || (length != 0 && data == nullptr)) {
            data_ = nullptr;
            length_ = 0;
            error_ = true;
        }
    }

    // Native PhysX calls retain the actual SDK's 64-bit virtual signatures.
    physx::PxU64 read(void* dest, physx::PxU64 count) override {
        if (error_ || count == 0) return 0;
        const physx::PxU64 remaining = length_ - position_;
        const physx::PxU64 n = count < remaining ? count : remaining;
        if (n == 0) return 0;
        if (dest == nullptr) { error_ = true; return 0; }
        // Both pointers must refer to live caller-owned storage. This class
        // cannot authenticate arbitrary pointers or detect external frees.
        std::memmove(dest, data_ + position_, static_cast<std::size_t>(n));
        position_ += static_cast<physx::PxU32>(n);
        return n;
    }
    physx::PxU64 getLength() const override { return length_; }
    void seek(physx::PxU64 position) override {
        position_ = position < length_ ? static_cast<physx::PxU32>(position) : length_;
    }
    physx::PxU64 tell() const override { return position_; }

    // BindTo selects these methods without changing the existing JS method names.
    physx::PxU32 read32(void* dest, physx::PxU32 count) {
        return static_cast<physx::PxU32>(read(dest, count));
    }
    physx::PxU32 getLength32() const noexcept { return length_; }
    void seek32(physx::PxU32 position) { seek(position); }
    physx::PxU32 tell32() const noexcept { return position_; }
    bool isValid() const noexcept { return !error_; }

    PxDefaultMemoryInputData(const PxDefaultMemoryInputData&) = delete;
    PxDefaultMemoryInputData& operator=(const PxDefaultMemoryInputData&) = delete;
private:
    const physx::PxU8* data_;
    physx::PxU32 length_;
    physx::PxU32 position_ = 0;
    bool error_ = false;
};

// Allocator policy makes allocation failure testable without changing the
// production ABI or pretending to allocate multi-gigabyte buffers in tests.
struct SystemAllocator {
    static void* resize(void* data, std::size_t size) noexcept {
        return std::realloc(data, size);
    }
    static void release(void* data) noexcept { std::free(data); }
};

template<class Allocator = SystemAllocator>
class BoundedMemoryOutputStream : public physx::PxOutputStream {
public:
    explicit BoundedMemoryOutputStream(physx::PxU32 limit = kMaxStreamBytes) noexcept
        : limit_(limit <= kMaxStreamBytes ? limit : kMaxStreamBytes),
          error_(limit > kMaxStreamBytes) {}
    ~BoundedMemoryOutputStream() override { Allocator::release(data_); }

    physx::PxU64 write(const void* src, physx::PxU64 count) override {
        if (error_ || count == 0) return 0;
        // Check in 64 bits before converting, allocating or touching source data.
        if (src == nullptr || count > static_cast<physx::PxU64>(limit_ - size_)) {
            error_ = true;
            return 0;
        }
        const auto n = static_cast<physx::PxU32>(count);
        bool internal = false;
        std::size_t offset = 0;
        if (data_ != nullptr) {
            const auto begin = reinterpret_cast<std::uintptr_t>(data_);
            const auto address = reinterpret_cast<std::uintptr_t>(src);
            if (address >= begin && address - begin < capacity_) {
                internal = true;
                offset = static_cast<std::size_t>(address - begin);
                // Do not copy uninitialized bytes from the allocation's spare capacity.
                if (offset > size_ || count > size_ - offset) {
                    error_ = true;
                    return 0;
                }
            }
        }
        const physx::PxU32 required = size_ + n; // bounded above by limit_
        if (required > capacity_) {
            physx::PxU64 grown = capacity_ == 0 ? 256U
                : static_cast<physx::PxU64>(capacity_) + capacity_ / 2U;
            if (grown < required) grown = required;
            if (grown > limit_) grown = limit_;
            void* replacement = Allocator::resize(data_, static_cast<std::size_t>(grown));
            if (replacement == nullptr) {
                // realloc failure leaves the old allocation and payload intact.
                error_ = true;
                return 0;
            }
            data_ = static_cast<physx::PxU8*>(replacement);
            capacity_ = static_cast<physx::PxU32>(grown);
        }
        if (internal) src = data_ + offset; // realloc may have moved the buffer
        std::memmove(data_ + size_, src, static_cast<std::size_t>(n));
        size_ = required;
        return count;
    }

    // Preserve the old WebIDL write() return type (void). isValid() exposes a
    // sticky error; callers must check it before consuming cooking/serialization.
    void write32(const void* src, physx::PxU32 count) { (void)write(src, count); }
    physx::PxU32 getSize() const noexcept { return size_; }
    physx::PxU8* getData() const noexcept { return data_; }
    bool isValid() const noexcept { return !error_; }

    BoundedMemoryOutputStream(const BoundedMemoryOutputStream&) = delete;
    BoundedMemoryOutputStream& operator=(const BoundedMemoryOutputStream&) = delete;
private:
    physx::PxU8* data_ = nullptr;
    physx::PxU32 size_ = 0;
    physx::PxU32 capacity_ = 0;
    physx::PxU32 limit_;
    bool error_ = false;
};

class PxDefaultMemoryOutputStream final : public BoundedMemoryOutputStream<> {
public:
    PxDefaultMemoryOutputStream() noexcept = default;
};

} // namespace pr_web
#endif
