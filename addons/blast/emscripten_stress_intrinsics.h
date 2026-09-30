// SPDX-License-Identifier: MIT
#pragma once
#if !defined(__EMSCRIPTEN__)
#error "This adapter is pinned to Emscripten's portable intrinsic headers"
#endif
#include <immintrin.h>
#include <cmath>

// WASM has fixed IEEE floating-point semantics and no writable x86 CSR.
inline void _mm_setcsr(unsigned int) {}

// Emscripten 4.0.19 parses AVX through portable vectors but does not declare
// these FMA intrinsics. Preserve fused semantics lane by lane. NVIDIA's scalar
// stress backend is selected independently; these template branches are unused.
inline __m256 pr_stress_fma(__m256 a, __m256 b, __m256 c, bool negateA, bool negateC)
{
    float av[8], bv[8], cv[8], result[8];
    _mm256_storeu_ps(av, a); _mm256_storeu_ps(bv, b); _mm256_storeu_ps(cv, c);
    for (unsigned i = 0; i < 8; ++i)
        result[i] = std::fma(negateA ? -av[i] : av[i], bv[i], negateC ? -cv[i] : cv[i]);
    return _mm256_loadu_ps(result);
}
inline __m256 _mm256_fmadd_ps(__m256 a, __m256 b, __m256 c)
{ return pr_stress_fma(a, b, c, false, false); }
inline __m256 _mm256_fnmadd_ps(__m256 a, __m256 b, __m256 c)
{ return pr_stress_fma(a, b, c, true, false); }
inline __m256 _mm256_fmsub_ps(__m256 a, __m256 b, __m256 c)
{ return pr_stress_fma(a, b, c, false, true); }
