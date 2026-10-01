# SPDX-License-Identifier: MIT
"""Checked opt-in extension of pinned NVIDIA translation units, never upstream edits.

Defaults preserve the original operator. Each solver owns its options; the bridge
enables actual masses, volume-derived sphere inertia and authored bond positions
before the first solve. Original copyright and license remain in generated files.
"""


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError('Pinned physical stress extension anchor changed: ' + old[:100])
    return source.replace(old, new, 1)


def extend(source: str, tolerance: float = 1e-6) -> str:
    if tolerance not in (1e-5, 1e-6):
        raise ValueError('Physical precision candidate must use the declared 1e-5 or 1e-6 tolerance')
    source = replace_once(source, 'params.centerBonds = true;', 'params.centerBonds = m_centerBonds;')
    source = replace_once(source, 'params.equalizeMasses = true;', 'params.equalizeMasses = m_equalizeMasses;')
    source = replace_once(source, 'params.tolerance = 0.001f;', 'params.tolerance = m_tolerance;')
    source = replace_once(source, '    void initialize()\n    {\n        StressProcessor::DataParams params;', '''    void enablePhysicalOperator()
    {
        m_centerBonds = false;
        m_equalizeMasses = false;
        m_tolerance = PHYSICAL_TOLERANCE;
        m_forceColdStart = true;
    }

    float physicalTolerance() const { return m_tolerance; }

    void initialize()
    {
        StressProcessor::DataParams params;''')
    source = replace_once(source, '    bool                        m_inputsChanged;\n', '''    bool                        m_inputsChanged;
    bool                        m_centerBonds = true;
    bool                        m_equalizeMasses = true;
    float                       m_tolerance = 0.001f;
''')
    source = replace_once(source, '    const NodeData& getNodeData(uint32_t node) const\n', '''    void enablePhysicalOperator()
    {
        m_solver.enablePhysicalOperator();
        m_nodesDirty = true;
        // No bond stress has been evaluated before the first physical update.
        m_overstressedBondCount = 0;
    }

    float physicalTolerance() const { return m_solver.physicalTolerance(); }

    bool physicalBondImpulse(uint32_t blastBondIndex, uint32_t firstNode, NvVec3& linear, NvVec3& angular)
    {
        const uint32_t internal = getInternalBondIndex(blastBondIndex);
        if (isInvalidIndex(internal)) return false;
        const BondData& bond = m_bondsData[internal];
        const uint32_t a = m_nodesData[bond.node0].solverNode;
        const uint32_t b = m_nodesData[bond.node1].solverNode;
        if (a == b) return false;
        auto entry = m_solverBondsMap.find(BondKey(a, b));
        if (!entry) return false;
        uint32_t solverA, solverB;
        m_solver.getBondNodes(entry->second, solverA, solverB);
        m_solver.getBondImpulses(entry->second, linear, angular);
        const float sign = m_nodesData[firstNode].solverNode == solverA ? 1.f : -1.f;
        linear *= sign;
        angular *= sign;
        return true;
    }

    const NodeData& getNodeData(uint32_t node) const
''')
    source = replace_once(source, '    bool                                    valid() { return m_valid; }', '''    bool                                    valid() { return m_valid; }
    void enablePhysicalOperator() { m_graphProcessor->enablePhysicalOperator(); }
    float physicalTolerance() const { return m_graphProcessor->physicalTolerance(); }
    bool physicalBondImpulse(uint32_t bond, uint32_t firstNode, NvVec3& linear, NvVec3& angular)
    { return m_graphProcessor->physicalBondImpulse(bond, firstNode, linear, angular); }
    bool physicalBondStress(uint32_t bond, float& compression, float& tension, float& shear)
    { return m_graphProcessor->getBondStress(bond, compression, tension, shear); }''')
    source += '''
// First-party opt-in bridge hooks. The caller owns a solver created by this TU.
extern "C" void pr_blast_enable_physical_stress(Nv::Blast::ExtStressSolver* solver)
{
    static_cast<Nv::Blast::ExtStressSolverImpl*>(solver)->enablePhysicalOperator();
}
extern "C" float pr_blast_physical_solver_tolerance(Nv::Blast::ExtStressSolver* solver)
{
    return static_cast<Nv::Blast::ExtStressSolverImpl*>(solver)->physicalTolerance();
}
extern "C" bool pr_blast_physical_bond_impulse(Nv::Blast::ExtStressSolver* solver,
    uint32_t bond, uint32_t firstNode, float* output)
{
    nvidia::NvVec3 linear, angular;
    if (!static_cast<Nv::Blast::ExtStressSolverImpl*>(solver)->physicalBondImpulse(bond, firstNode, linear, angular))
        return false;
    // Coupling uses +linear and -r cross linear on node0. Physical couple has
    // the opposite sign to the solver's angular coordinate.
    output[0] = linear.x; output[1] = linear.y; output[2] = linear.z;
    output[3] = -angular.x; output[4] = -angular.y; output[5] = -angular.z;
    return true;
}
extern "C" bool pr_blast_physical_bond_stress(Nv::Blast::ExtStressSolver* solver,
    uint32_t bond, float* output)
{
    return static_cast<Nv::Blast::ExtStressSolverImpl*>(solver)->physicalBondStress(
        bond, output[0], output[1], output[2]);
}
'''
    return replace_once(source, 'PHYSICAL_TOLERANCE', format(tolerance, '.8g') + 'f')
