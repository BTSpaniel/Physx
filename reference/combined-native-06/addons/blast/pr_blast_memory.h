// SPDX-License-Identifier: MIT
#pragma once
#include <emscripten/heap.h>
#include <cstdint>
template<class T> bool validSpan(const T* pointer, const uint64_t count)
{
    const uint64_t address = reinterpret_cast<uintptr_t>(pointer);
    const uint64_t heapSize = emscripten_get_heap_size();
    return pointer && address % alignof(T) == 0 && address <= heapSize &&
        count <= (heapSize - address) / sizeof(T);
}
