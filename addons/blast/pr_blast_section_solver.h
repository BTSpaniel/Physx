// SPDX-License-Identifier: MIT
// First-party complementary-energy interface operator over NVIDIA's unchanged
// coupling and inertia matrix operations. The generated CGNR adaptation keeps
// its recurrence, adding original-operator stopping, fresh residuals and guards.
#pragma once
#include "stress.h"
#include "pr_blast_section_matrix.h"
#include <vector>
#include <limits>
#include <cstring>

inline float prSectionGet(const AngLin6& v, unsigned i)
{
    const NvcVec3& a = i < 3 ? v.ang : v.lin;
    const unsigned axis = i < 3 ? i : i-3;
    return axis == 0 ? a.x : axis == 1 ? a.y : a.z;
}
inline void prSectionSet(AngLin6& v, unsigned i, float x)
{
    NvcVec3& a = i < 3 ? v.ang : v.lin;
    const unsigned axis = i < 3 ? i : i-3;
    if (axis == 0) a.x = x; else if (axis == 1) a.y = x; else a.z = x;
}

struct PrSectionOperator
{
    const BondMatrixS* base = nullptr;
    const std::vector<std::array<float,36>>* factor = nullptr;
    AngLin6* bondScratch = nullptr;
    AngLin6* gradient = nullptr;
    AngLin6* nodeScratch = nullptr;
    mutable AngLin6ErrorSq originalError{};
    mutable bool valid = true;
    mutable uint32_t updates = 0, freshChecks = 0;
    mutable bool freshAccepted = false;

    void right(AngLin6* out, const AngLin6* in, bool transpose) const
    {
        for (uint32_t b = 0; b < base->N; ++b)
        {
            AngLin6 value{};
            for (unsigned i = 0; i < 6; ++i)
            {
                double sum = 0;
                for (unsigned j = 0; j < 6; ++j)
                    sum += double((*factor)[b][transpose ? j*6+i : i*6+j])*prSectionGet(in[b],j);
                const float v = float(sum);
                valid = valid && std::isfinite(v);
                prSectionSet(value,i,v);
            }
            out[b] = value;
        }
    }
    float stoppingError(AngLin6ErrorSq& out) const
    { out = originalError; return originalError.ang + originalError.lin; }
    bool fresh(const AngLin6* x, const AngLin6* b, AngLin6* r, AngLin6* z, AngLin6ErrorSq& error, float limit) const;
};

struct PrSectionOperatorOps
{
    void rmul(AngLin6* y, const PrSectionOperator& a, const AngLin6* x, uint32_t m, uint32_t n) const
    {
        a.right(a.bondScratch,x,false);
        BondMatrixOpsS<float>().rmul(y,*a.base,a.bondScratch,m,n);
    }
    void lmul(AngLin6* y, const AngLin6* x, const PrSectionOperator& a, uint32_t m, uint32_t n) const
    {
        BondMatrixOpsS<float>().lmul(a.gradient,x,*a.base,m,n);
        const float e = AngLin6Ops<float>().calculate_error(a.originalError,a.gradient,n);
        a.valid = a.valid && std::isfinite(e);
        a.right(y,a.gradient,true);
    }
};

inline bool PrSectionOperator::fresh(const AngLin6* x, const AngLin6* b, AngLin6* r,
    AngLin6* z, AngLin6ErrorSq& error, float limit) const
{
    ++freshChecks;
    PrSectionOperatorOps().rmul(nodeScratch,*this,x,base->M,base->N);
    AngLin6Ops<float>().vsub(r,b,nodeScratch,base->M);
    PrSectionOperatorOps().lmul(z,r,*this,base->M,base->N);
    const float e = stoppingError(error);
    freshAccepted = valid && std::isfinite(e) && e <= limit;
    return freshAccepted;
}

#include "pr_section_cgnr.h"

class PrSectionStressProcessor : public StressProcessor
{
    using Solver = PrSectionCGNR<AngLin6,AngLin6Ops<float>,PrSectionOperator,PrSectionOperatorOps,float,AngLin6ErrorSq>;
    std::vector<std::array<float,36>> m_factors;
    POD_Buffer<AngLin6> m_y, m_bondWork, m_gradientWork, m_nodeWork;
    PrSectionOperator m_operator;
    bool m_ready = false, m_solutionValid = false, m_pending = false, m_valid = true;
    double m_progress[8] = {0,0,0,0,0,0,0,2};

public:
    bool progress(double* out) const
    {
        if (!m_valid) return false;
        for (unsigned i = 0; i < 8; ++i) out[i] = m_progress[i];
        return true;
    }

    bool prepareSections(const SolverNodeS* nodes, uint32_t nodeCount,
        const SolverBond* bonds, uint32_t bondCount, const std::vector<PrSectionMatrix>& k)
    {
        m_ready = m_solutionValid = m_pending = false;
        if (!m_valid || k.size() != bondCount) return m_valid = false;
        StressProcessor::DataParams params;
        params.centerBonds = params.equalizeMasses = false;
        StressProcessor::prepare(nodes,nodeCount,bonds,bondCount,params);
        if (!std::isfinite(m_mass_scale) || m_mass_scale <= 0 ||
            !std::isfinite(m_length_scale) || m_length_scale <= 0) return m_valid = false;
        const double linearScale = double(m_mass_scale)*m_length_scale;
        const double angularScale = linearScale*m_length_scale;
        if (!std::isfinite(linearScale) || !std::isfinite(angularScale) ||
            linearScale <= 0 || angularScale <= 0) return m_valid = false;
        std::vector<PrSectionMatrix> unscaled(bondCount);
        double common = 0;
        for (uint32_t b = 0; b < bondCount; ++b)
        {
            double lower[36];
            if (!prSectionCholesky(k[b].data(),lower)) return m_valid = false;
            // Physical [F,M] -> native [-M,F], then native mass/length scales.
            for (unsigned i = 0; i < 6; ++i) for (unsigned j = 0; j < 6; ++j)
            {
                const double v = i < 3 ? -lower[(i+3)*6+j]/angularScale : lower[(i-3)*6+j]/linearScale;
                if (!std::isfinite(v)) return m_valid = false;
                unscaled[b][i*6+j] = v;
                common = std::max(common,std::abs(v));
            }
        }
        // One global factor is numerical normalization only, never a per-bond
        // constitutive change. Uniform scaling of every K preserves load shares.
        if (!bondCount) common = 1;
        if (!std::isfinite(common) || common <= 0) return m_valid = false;
        m_factors.resize(bondCount);
        for (uint32_t b = 0; b < bondCount; ++b) for (unsigned i = 0; i < 36; ++i)
        {
            const double value = unscaled[b][i]/common;
            const float narrowed = float(value);
            // Undo the physical [F,M] -> native [-M,F] row permutation.
            // A triangular Cholesky factor keeps full rank exactly when all
            // its diagonal modes survive. Tiny off-diagonal coefficients may
            // round to zero normally; rejecting them invents an input floor.
            const unsigned lowerRow = (i/6+3)%6, lowerColumn = i%6;
            if (!std::isfinite(narrowed) || (lowerRow == lowerColumn && narrowed == 0)) return m_valid = false;
            m_factors[b][i] = narrowed;
        }
        m_y.resize(bondCount); m_bondWork.resize(bondCount); m_gradientWork.resize(bondCount);
        m_nodeWork.resize(nodeCount);
        m_operator.base = &m_B; m_operator.factor = &m_factors;
        m_operator.bondScratch = m_bondWork.data(); m_operator.gradient = m_gradientWork.data();
        m_operator.nodeScratch = m_nodeWork.data();
        m_operator.valid = true;
        m_ready = true;
        return true;
    }

    bool solveSections(AngLin6* output, const AngLin6* velocities,
        uint32_t iterations, float tolerance, bool changed, AngLin6ErrorSq& error)
    {
        auto fail = [&]() { m_valid = false; m_pending = false;
            error.ang = error.lin = std::numeric_limits<float>::quiet_NaN(); return false; };
        if (!m_valid || !m_ready || !iterations) return fail();
        const uint32_t nodes = getNodeCount(), bonds = getBondCount();
        const float reciprocalLength = 1.f/m_length_scale;
        for (uint32_t i = 0; i < nodes; ++i)
        {
            const InertiaS& inertia = m_recip_sqrt_I[i];
            const AngLin6& v = velocities[i];
            m_rhs[i].ang = v.ang/(-(inertia.I > 0 ? inertia.I : 1.f));
            m_rhs[i].lin = (-reciprocalLength/(inertia.m > 0 ? inertia.m : 1.f))*v.lin;
        }
        const bool resumed = m_solutionValid && m_pending && !changed;
        const unsigned warmth = resumed ? 2 : m_solutionValid ? 1 : 0;
        m_operator.updates = m_operator.freshChecks = 0;
        m_operator.freshAccepted = false;
        const int result = Solver().solve(m_y.data(),m_operator,m_rhs.data(),nodes,bonds,
            m_solver_cache.data(),&error,tolerance,iterations,warmth);
        m_solutionValid = true;
        m_pending = result < 0;
        m_progress[0] = m_operator.updates; m_progress[1] = resumed ? 1 : 0;
        m_progress[2] = m_operator.freshChecks; m_progress[3] = m_operator.freshAccepted ? 1 : 0;
        const double increments[3] = {double(m_operator.updates),resumed ? 1.0 : 0.0,double(m_operator.freshChecks)};
        for (unsigned i = 0; i < 3; ++i)
        {
            if (m_progress[4+i] > 9007199254740991.0-increments[i]) return fail();
            m_progress[4+i] += increments[i];
        }
        if (!m_operator.valid || m_operator.updates > iterations) return fail();
        // Keep the private normalized solution and Krylov cache untouched.
        // Only this separate presentation buffer is converted to physical units.
        m_operator.right(output,m_y.data(),false);
        const float linearScale = m_length_scale*m_mass_scale;
        const float angularScale = m_length_scale*linearScale;
        for (uint32_t b = 0; b < bonds; ++b)
        {
            output[b].ang *= angularScale; output[b].lin *= linearScale;
            for (unsigned i = 0; i < 6; ++i) if (!std::isfinite(prSectionGet(output[b],i))) return fail();
        }
        if (!m_operator.valid || !std::isfinite(error.ang) || !std::isfinite(error.lin)) return fail();
        return result >= 0 && m_operator.freshAccepted;
    }
};
