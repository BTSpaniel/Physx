# SPDX-License-Identifier: MIT
"""Checked physical-section adaptations; selected revision1 sources stay intact."""
from physical_stress_extension import replace_once


def extend(source: str) -> str:
    source = replace_once(source, '#include "stress.h"', '#include "stress.h"\n#include "pr_blast_section_solver.h"')
    source = replace_once(source, '    float physicalTolerance() const { return m_tolerance; }', '''    float physicalTolerance() const { return m_tolerance; }
    void enableSections() { m_sectionsEnabled = true; m_sectionDirty = true; m_forceColdStart = true; }
    void setSectionStiffness(uint32_t index, const double* values)
    {
        if (m_sectionK.size() != m_bonds.size()) { m_sectionK.resize(m_bonds.size()); m_sectionDirty = true; }
        if (memcmp(m_sectionK[index].data(),values,36*sizeof(double)))
        { memcpy(m_sectionK[index].data(),values,36*sizeof(double)); m_sectionDirty = true; }
    }
    bool physicalProgress(double* out) const
    { return m_sectionsEnabled && m_sectionProcessor.progress(out); }''')
    source = replace_once(source, '''        v.ang = { velocityAngular.x, velocityAngular.y, velocityAngular.z };
        v.lin = { velocityLinear.x, velocityLinear.y, velocityLinear.z };
        m_inputsChanged = true;''', '''        const bool changed = memcmp(&v.ang,&velocityAngular,sizeof(NvcVec3)) != 0 ||
            memcmp(&v.lin,&velocityLinear,sizeof(NvcVec3)) != 0;
        v.ang = { velocityAngular.x, velocityAngular.y, velocityAngular.z };
        v.lin = { velocityLinear.x, velocityLinear.y, velocityLinear.z };
        m_inputsChanged = m_inputsChanged || !m_sectionsEnabled || changed;''')
    source = replace_once(source, '        m_bonds.replaceWithLast(bondIndex);', '''        m_bonds.replaceWithLast(bondIndex);
        if (m_sectionsEnabled)
        {
            m_sectionK.resize(m_bonds.size());
            m_sectionDirty = true;
            m_forceColdStart = true;
        }''')
    source = replace_once(source,
        '        m_converged = (m_stressProcessor.solve(m_impulses.data(), m_velocities.data(), params, &m_error_sq) >= 0);', '''        if (!m_sectionsEnabled)
            m_converged = (m_stressProcessor.solve(m_impulses.data(), m_velocities.data(), params, &m_error_sq) >= 0);
        else
        {
            if (m_sectionDirty || m_forceColdStart)
            {
                m_sectionProcessor.prepareSections(m_nodes.begin(),m_nodes.size(),m_bonds.begin(),m_bonds.size(),m_sectionK);
                m_sectionDirty = false;
            }
            m_impulses.resize(m_bonds.size());
            m_converged = m_sectionProcessor.solveSections(m_impulses.data(),m_velocities.data(),
                iterationCount,m_tolerance,m_inputsChanged,m_error_sq);
        }''')
    source = replace_once(source, '    float                       m_tolerance = 0.001f;', '''    float                       m_tolerance = 0.001f;
    bool                        m_sectionsEnabled = false;
    bool                        m_sectionDirty = true;
    std::vector<PrSectionMatrix> m_sectionK;
    PrSectionStressProcessor    m_sectionProcessor;''')
    source = replace_once(source, '    float physicalTolerance() const { return m_solver.physicalTolerance(); }', '''    float physicalTolerance() const { return m_solver.physicalTolerance(); }
    void enableSections(const double* values, uint32_t count)
    {
        m_sectionK.resize(count);
        for (uint32_t i = 0; i < count; ++i) memcpy(m_sectionK[i].data(),values+36*i,36*sizeof(double));
        m_sectionsEnabled = true;
        m_solver.enableSections();
    }
    bool physicalProgress(double* out) const { return m_solver.physicalProgress(out); }''')
    source = replace_once(source, '''    void solve(const ExtStressSolverSettings& settings, const float* bondHealth, const NvBlastBond* bonds, bool warmStart = true)
    {
        sync(bonds);''', '''    void solve(const ExtStressSolverSettings& settings, const float* bondHealth, const NvBlastBond* bonds, bool warmStart = true)
    {
        if (m_sectionsEnabled)
        {
            // External authoritative damage invalidates the topology before a
            // new solve. Section solves never generate auxiliary damage.
            for (uint32_t i = m_bondsData.size(); i > 0; --i)
                if (bondHealth[m_bondsData[i-1].blastBondIndex] <= 0)
                    removeBondIfExists(m_bondsData[i-1].blastBondIndex);
        }
        sync(bonds);
        if (m_sectionsEnabled)
        {
            for (uint32_t i = 0; i < m_solverBondsData.size(); ++i)
            {
                const auto& indices = m_solverBondsData[i].blastBondIndices;
                // Bridge authoring rejects duplicate node pairs and graph
                // reduction is disabled. Each native bond owns one full K.
                if (indices.size() != 1 || indices[0] >= m_sectionK.size())
                {
                    PrSectionMatrix invalid{};
                    m_solver.setSectionStiffness(i,invalid.data());
                }
                else m_solver.setSectionStiffness(i,m_sectionK[indices[0]].data());
            }
        }''')
    source = replace_once(source, '        updateBondStress(settings, bondHealth, bonds);', '''        if (!m_sectionsEnabled) updateBondStress(settings,bondHealth,bonds);
        else m_overstressedBondCount = 0;''')
    source = replace_once(source, 'private:\n\n    void resetVelocities()', '''private:
    bool m_sectionsEnabled = false;
    std::vector<PrSectionMatrix> m_sectionK;

    void resetVelocities()''')
    source = replace_once(source,
        '    float physicalTolerance() const { return m_graphProcessor->physicalTolerance(); }', '''    float physicalTolerance() const { return m_graphProcessor->physicalTolerance(); }
    void enableSections(const double* values,uint32_t count) { m_graphProcessor->enableSections(values,count); }
    bool physicalProgress(double* out) const { return m_graphProcessor->physicalProgress(out); }''')
    source += '''
extern "C" void pr_blast_enable_section_stress(Nv::Blast::ExtStressSolver* solver,const double* values,uint32_t count)
{ static_cast<Nv::Blast::ExtStressSolverImpl*>(solver)->enableSections(values,count); }
extern "C" bool pr_blast_physical_solver_progress(Nv::Blast::ExtStressSolver* solver,double* output)
{ return static_cast<Nv::Blast::ExtStressSolverImpl*>(solver)->physicalProgress(output); }
'''
    return source


def extend_cgnr(source: str) -> str:
    source = replace_once(source, 'struct CGNR\n', 'struct PrSectionCGNR\n')
    source = replace_once(source, '        Error error;', '''        Error error{};
        if (!std::isfinite(delta_sq) || delta_sq < 0)
        { A.valid = false; return -1; }''')
    source = replace_once(source, '''            const Scalar z_sq = ElemOps().calculate_error(error, z, N);                 // Calculate residual (of modified equation) length squared
            if (le(z_sq, delta_sq)) break;                                              // Terminate (convergence) if within tolerance''', '''            Scalar z_sq = ElemOps().calculate_error(error,z,N);
            const float stop_sq = A.stoppingError(error);
            if (!A.valid || !std::isfinite(z_sq) || !std::isfinite(stop_sq))
            { A.valid = false; return -1; }
            // This is the original, unweighted B^T r threshold. Right-factor
            // units and uniform material stiffness cannot loosen admission.
            if (stop_sq <= delta_sq)
            {
                if (A.fresh(x,b,r,z,error,delta_sq)) break;
                if (!A.valid) return -1;
                // A fresh residual replaced the recurrence residual. Restart
                // directions, preserving the range-valid normalized solution.
                z_sq = ElemOps().calculate_error(error,z,N);
                A.stoppingError(error);
                warmth = 0;
            }
            if (!(z_sq > 0) || !std::isfinite(z_sq)) { A.valid = false; return -1; }''')
    source = replace_once(source,
        '            const Scalar mu = div(z_sq, ElemOps().length_sq(s, M));                     // mu = |z|^2 / |A*p|^2', '''            const Scalar denominator = ElemOps().length_sq(s,M);
            const Scalar mu = div(z_sq,denominator);
            if (!(denominator > 0) || !std::isfinite(denominator) || !std::isfinite(mu))
            { A.valid = false; return -1; }''')
    source = replace_once(source,
        '            ElemOps().vnmadd(r, mu, s, r, M);                                           // r -= mu*s', '''            ElemOps().vnmadd(r, mu, s, r, M);                                           // r -= mu*s
            ++A.updates;''')
    return source
