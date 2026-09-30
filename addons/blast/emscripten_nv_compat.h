// SPDX-License-Identifier: MIT
// Map Emscripten to Blast's closest supported generic 32-bit platform.
#ifndef PR_BLAST_EMSCRIPTEN_NV_COMPAT_H
#define PR_BLAST_EMSCRIPTEN_NV_COMPAT_H

#if !defined(__EMSCRIPTEN__)
#error "This compatibility header is only valid for Emscripten"
#endif

// NvPreprocessor.h predates WebAssembly and otherwise stops at #error. Include
// it once with Linux/POSIX services and a 32-bit non-NEON CPU family selected.
#define __linux__ 1
#define __arm__ 1
#include "NvPreprocessor.h"
#undef __arm__
#undef __linux__

#endif
