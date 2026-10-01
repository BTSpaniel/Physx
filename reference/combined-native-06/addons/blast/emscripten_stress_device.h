// SPDX-License-Identifier: MIT
// WebAssembly has no x86 CPUID, XGETBV or native AVX register context.
// Select NVIDIA's existing scalar stress backend without changing its solver.
#pragma once
#if !defined(__EMSCRIPTEN__)
#error "The portable stress backend is only configured for Emscripten"
#endif
struct InstructionSet { enum Enum { SSE, FMA3, OSXSAVE, AVX }; };
inline bool device_supports_instruction_set(InstructionSet::Enum) { return false; }
inline bool os_supports_avx_restore() { return false; }
