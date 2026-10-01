# SPDX-License-Identifier: MIT
"""Add opt-in numerical revision 3 without changing revision 1/2 algorithms."""
from physical_stress_extension import replace_once


def extend(source: str) -> str:
    source = replace_once(source, '#include "pr_blast_section_solver.h"',
        '#include "pr_blast_section_solver.h"\n#include "pr_blast_section_accurate.h"')
    source = replace_once(source,
        '    void enableSections() { m_sectionsEnabled = true; m_sectionDirty = true; m_forceColdStart = true; }',
        '''    void enableSections() { m_sectionsEnabled = true; m_sectionDirty = true; m_forceColdStart = true; }
    void enableSectionsV3() { enableSections(); m_sectionsV3 = true; }
    void setPreciseSectionNode(uint32_t node,double volume,const std::array<double,3>& load)
    {
        if(m_sectionVolumes.size()!=m_nodes.size())
        { m_sectionVolumes.assign(m_nodes.size(),0); m_sectionLoads.assign(m_nodes.size(),{}); }
        if(m_sectionVolumes[node]!=volume) { m_sectionVolumes[node]=volume; m_sectionDirty=true; }
        if(m_sectionLoads[node]!=load) { m_sectionLoads[node]=load; m_inputsChanged=true; }
    }''')
    source = replace_once(source,
        '    { return m_sectionsEnabled && m_sectionProcessor.progress(out); }',
        '''    { return m_sectionsEnabled && (m_sectionsV3 ? m_sectionAccurate.progress(out) : m_sectionProcessor.progress(out)); }
    bool sectionWorkV3(double* out) const { return m_sectionsV3 && m_sectionAccurate.work(out); }''')
    source = replace_once(source,
        '                m_sectionProcessor.prepareSections(m_nodes.begin(),m_nodes.size(),m_bonds.begin(),m_bonds.size(),m_sectionK);',
        '''                if (m_sectionsV3)
                    m_sectionAccurate.prepareSections(m_nodes.begin(),m_nodes.size(),m_bonds.begin(),m_bonds.size(),m_sectionK,m_sectionVolumes);
                else m_sectionProcessor.prepareSections(m_nodes.begin(),m_nodes.size(),m_bonds.begin(),m_bonds.size(),m_sectionK);''')
    source = replace_once(source,
        '''            m_converged = m_sectionProcessor.solveSections(m_impulses.data(),m_velocities.data(),
                iterationCount,m_tolerance,m_inputsChanged,m_error_sq);''',
        '''            if (m_sectionsV3)
                m_converged = m_sectionAccurate.solveSections(m_impulses.data(),m_sectionLoads,
                    iterationCount,m_tolerance,m_inputsChanged,m_error_sq);
            else m_converged = m_sectionProcessor.solveSections(m_impulses.data(),m_velocities.data(),
                iterationCount,m_tolerance,m_inputsChanged,m_error_sq);''')
    source = replace_once(source, '    PrSectionStressProcessor    m_sectionProcessor;',
        '''    PrSectionStressProcessor    m_sectionProcessor;
    bool                        m_sectionsV3 = false;
    PrSectionAccurateProcessor  m_sectionAccurate;
    std::vector<double> m_sectionVolumes;
    std::vector<std::array<double,3>> m_sectionLoads;''')
    source = replace_once(source,
        '    bool physicalProgress(double* out) const { return m_solver.physicalProgress(out); }',
        '''    void enableSectionsV3(const double* values,uint32_t count)
    {
        enableSections(values,count); m_solver.enableSectionsV3(); m_sectionsPrecise=true;
        // The original-load handoff precedes the first upstream update, where
        // this otherwise uninitialized setting is normally synchronized.
        setGraphReductionLevel(0);
    }
    bool setSectionLoadsV3(const float* forces,const uint32_t* nodes,uint32_t count)
    {
        if(!m_sectionsPrecise || m_graphReductionLevel!=0) return false;
        for(uint32_t i=0;i<count;++i) if(nodes[i]>=m_nodesData.size()) return false;
        m_originalSectionLoads.assign(m_nodesData.size(),{});
        for(uint32_t i=0;i<count;++i) for(unsigned axis=0;axis<3;++axis)
            m_originalSectionLoads[nodes[i]][axis]=forces[3*i+axis];
        return true;
    }
    bool physicalProgress(double* out) const { return m_solver.physicalProgress(out); }
    bool sectionWorkV3(double* out) const { return m_solver.sectionWorkV3(out); }''')
    source = replace_once(source,
        '    bool physicalProgress(double* out) const { return m_graphProcessor->physicalProgress(out); }',
        '''    void enableSectionsV3(const double* values,uint32_t count) { m_graphProcessor->enableSectionsV3(values,count); }
    bool physicalProgress(double* out) const { return m_graphProcessor->physicalProgress(out); }
    bool sectionWorkV3(double* out) const { return m_graphProcessor->sectionWorkV3(out); }''')
    source += '''
extern "C" void pr_blast_enable_section_stress_v3(Nv::Blast::ExtStressSolver* solver,const double* values,uint32_t count)
{ static_cast<Nv::Blast::ExtStressSolverImpl*>(solver)->enableSectionsV3(values,count); }
extern "C" bool pr_blast_section_v3_work(Nv::Blast::ExtStressSolver* solver,double* output)
{ return static_cast<Nv::Blast::ExtStressSolverImpl*>(solver)->sectionWorkV3(output); }
'''
    source = replace_once(source, '    bool m_sectionsEnabled = false;',
        '    bool m_sectionsEnabled = false;\n    bool m_sectionsPrecise = false;\n    std::vector<std::array<double,3>> m_originalSectionLoads;')
    source = replace_once(source, '        m_solver.solve(settings.maxSolverIterationsPerFrame, warmStart);',
        '        if(m_sectionsPrecise) for(uint32_t i=0;i<m_nodesData.size();++i)\n'
        '            m_solver.setPreciseSectionNode(m_nodesData[i].solverNode,m_nodesData[i].volume,m_originalSectionLoads[i]);\n'
        '        m_solver.solve(settings.maxSolverIterationsPerFrame, warmStart);')
    source = replace_once(source,
        '    bool sectionWorkV3(double* out) const { return m_graphProcessor->sectionWorkV3(out); }',
        '    bool sectionWorkV3(double* out) const { return m_graphProcessor->sectionWorkV3(out); }\n'
        '    bool setSectionLoadsV3(const float* forces,const uint32_t* nodes,uint32_t count)\n'
        '    { return m_graphProcessor->setSectionLoadsV3(forces,nodes,count); }')
    source += '\nextern "C" bool pr_blast_section_v3_loads(Nv::Blast::ExtStressSolver* solver,const float* forces,const uint32_t* nodes,uint32_t count)\n'
    source += '{ return static_cast<Nv::Blast::ExtStressSolverImpl*>(solver)->setSectionLoadsV3(forces,nodes,count); }\n'
    return source
