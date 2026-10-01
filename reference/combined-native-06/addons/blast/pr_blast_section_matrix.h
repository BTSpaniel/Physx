// SPDX-License-Identifier: MIT
// First-party physical interface matrix validation. No stiffness regularization.
#pragma once
#include <array>
#include <cmath>

using PrSectionMatrix = std::array<double, 36>;

// Diagonal equilibration keeps force/rotation units out of the SPD test.
// The exact authored symmetric matrix is retained; no epsilon diagonal is added.
inline bool prSectionCholesky(const double* k, double* lower)
{
    double scale[6];
    for (unsigned i = 0; i < 6; ++i)
    {
        if (!std::isfinite(k[i*6+i]) || k[i*6+i] <= 0) return false;
        scale[i] = std::sqrt(k[i*6+i]);
    }
    double normalized[36]{};
    for (unsigned i = 0; i < 6; ++i) for (unsigned j = 0; j < 6; ++j)
    {
        if (!std::isfinite(k[i*6+j]) || k[i*6+j] != k[j*6+i]) return false;
        normalized[i*6+j] = (k[i*6+j] / scale[i]) / scale[j];
        if (!std::isfinite(normalized[i*6+j])) return false;
        lower[i*6+j] = 0;
    }
    for (unsigned i = 0; i < 6; ++i) for (unsigned j = 0; j <= i; ++j)
    {
        double v = normalized[i*6+j];
        for (unsigned q = 0; q < j; ++q) v -= lower[i*6+q]*lower[j*6+q];
        if (!std::isfinite(v)) return false;
        if (i == j)
        {
            if (v <= 0) return false;
            lower[i*6+j] = std::sqrt(v);
        }
        else lower[i*6+j] = v / lower[j*6+j];
        if (!std::isfinite(lower[i*6+j])) return false;
    }
    for (unsigned i = 0; i < 6; ++i) for (unsigned j = 0; j <= i; ++j)
    {
        lower[i*6+j] *= scale[i];
        if (!std::isfinite(lower[i*6+j])) return false;
    }
    return true;
}
