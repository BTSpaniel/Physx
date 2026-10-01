// SPDX-License-Identifier: MIT
// Minimal browser-facing validation boundary for the pinned NVIDIA Blast core.
#include <emscripten/emscripten.h>
#include <emscripten/heap.h>
#include "pr_blast_memory.h"
#include "pr_blast_section_matrix.h"

#include "NvBlast.h"
#include "NvBlastExtStressSolver.h"

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <algorithm>
#include <cmath>
#include <set>
#include <limits>

extern "C" void pr_blast_enable_physical_stress(Nv::Blast::ExtStressSolver* solver);
extern "C" float pr_blast_physical_solver_tolerance(Nv::Blast::ExtStressSolver* solver);
extern "C" bool pr_blast_physical_bond_impulse(Nv::Blast::ExtStressSolver* solver,
    uint32_t bond, uint32_t firstNode, float* output);
extern "C" bool pr_blast_physical_bond_stress(Nv::Blast::ExtStressSolver* solver,
    uint32_t bond, float* output);
extern "C" void pr_blast_enable_section_stress(Nv::Blast::ExtStressSolver*,const double*,uint32_t);
extern "C" void pr_blast_enable_section_stress_v3(Nv::Blast::ExtStressSolver*,const double*,uint32_t);
extern "C" bool pr_blast_physical_solver_progress(Nv::Blast::ExtStressSolver*,double*);
extern "C" bool pr_blast_section_v3_work(Nv::Blast::ExtStressSolver*,double*);
extern "C" bool pr_blast_section_v3_loads(Nv::Blast::ExtStressSolver*,const float*,const uint32_t*,uint32_t);

namespace
{
void* allocateZeroed16(const size_t bytes)
{
    const size_t allocationSize = (bytes + 15u) & ~size_t(15u);
    void* memory = std::aligned_alloc(16, allocationSize == 0 ? 16 : allocationSize);
    if (memory != nullptr)
        std::memset(memory, 0, allocationSize == 0 ? 16 : allocationSize);
    return memory;
}
}

namespace
{
struct BrowserBlastFamily
{
    void* assetMemory = nullptr;
    void* familyMemory = nullptr;
    NvBlastAsset* asset = nullptr;
    NvBlastFamily* family = nullptr;
    uint32_t chunkCount = 0;
    std::vector<NvBlastActor*> actors;
    std::vector<uint32_t> chunkNodes;
    std::vector<NvBlastBondFractureData> bonds;
    std::vector<uint8_t> scratch;
    std::vector<uint32_t> assetBondIndices;
    std::vector<float> massesKg;
    Nv::Blast::ExtStressSolver* stress = nullptr;
    bool physicalAreas = false;
    bool physicalStress = false;
    bool physicalNumericalFailure = false;
    std::vector<float> lastBondWrenches;
    std::vector<uint8_t> lastBondWrenchValid;
    bool sectionStress = false;
    uint32_t sectionRevision = 0;
    bool sectionResultsValid = false;
    std::vector<uint32_t> sectionOffsets;
    std::vector<double> sectionSamples;
    std::vector<double> sectionResults;

    ~BrowserBlastFamily()
    {
        if (stress) stress->release();
        for (NvBlastActor* actor : actors) NvBlastActorDeactivate(actor, nullptr);
        std::free(familyMemory);
        std::free(assetMemory);
    }
};
std::set<BrowserBlastFamily*> browserFamilies;
BrowserBlastFamily* checkedFamily(uintptr_t handle)
{
    auto* family = reinterpret_cast<BrowserBlastFamily*>(handle);
    return browserFamilies.count(family) ? family : nullptr;
}

int32_t splitFamily(BrowserBlastFamily& family)
{
    std::vector<NvBlastActor*> next;
    std::vector<NvBlastActor*> children(family.chunkCount);
    for (auto* actor : family.actors)
    {
        if (!NvBlastActorIsSplitRequired(actor, nullptr)) { next.push_back(actor); continue; }
        family.scratch.resize(NvBlastActorGetRequiredScratchForSplit(actor, nullptr));
        if (family.stress) family.stress->notifyActorDestroyed(*actor);
        NvBlastActorSplitEvent split{nullptr, children.data()};
        const uint32_t count = NvBlastActorSplit(&split, actor, family.chunkCount, family.scratch.data(), nullptr, nullptr);
        if (count)
        {
            next.insert(next.end(), children.begin(), children.begin() + count);
            if (family.stress) for (uint32_t i = 0; i < count; ++i) family.stress->notifyActorCreated(*children[i]);
        }
        else
        {
            next.push_back(actor);
            if (family.stress) family.stress->notifyActorCreated(*actor);
        }
    }
    family.actors = std::move(next);
    return static_cast<int32_t>(family.actors.size());
}
}

// Prefractured support chunks: float4(center.xyz, volume), uint2 bond endpoints.
// The native family owns real persistent health and actor splitting state.
static uintptr_t createFamily(
    uint32_t count, const float* chunkData, uint32_t bondCount,
    const uint32_t* bondData, float initialHealth, const float* areas,
    const float* centroids = nullptr, const float* normals = nullptr)
{
    if (count < 2 || count > 4096 || bondCount > 65536 || !chunkData ||
        (bondCount && !bondData) || !std::isfinite(initialHealth) || initialHealth <= 0) return 0;
    if (!validSpan(chunkData, uint64_t(count) * 4) ||
        (bondCount && !validSpan(bondData, uint64_t(bondCount) * 2)) ||
        (areas && !validSpan(areas, bondCount)) ||
        ((centroids || normals) && (!areas || !validSpan(centroids, uint64_t(bondCount) * 3)
                                  || !validSpan(normals, uint64_t(bondCount) * 3)))) return 0;
    std::vector<NvBlastChunkDesc> chunks(count + 1);
    chunks[0].parentChunkDescIndex = UINT32_MAX;
    chunks[0].flags = NvBlastChunkDesc::NoFlags;
    double totalVolume = 0, weightedCenter[3] = {};
    for (uint32_t i = 0; i < count; ++i)
    {
        for (uint32_t j = 0; j < 4; ++j) if (!std::isfinite(chunkData[4 * i + j])) return 0;
        if (chunkData[4 * i + 3] <= 0) return 0;
        auto& chunk = chunks[i + 1];
        for (uint32_t j = 0; j < 3; ++j) chunk.centroid[j] = chunkData[4 * i + j];
        chunk.volume = chunkData[4 * i + 3];
        chunk.parentChunkDescIndex = 0;
        chunk.flags = NvBlastChunkDesc::SupportFlag;
        chunk.userData = i;
        totalVolume += chunk.volume;
        for (uint32_t j = 0; j < 3; ++j) weightedCenter[j] += double(chunk.centroid[j]) * chunk.volume;
    }
    if (!std::isfinite(totalVolume) || totalVolume > std::numeric_limits<float>::max()) return 0;
    chunks[0].volume = static_cast<float>(totalVolume);
    for (uint32_t j = 0; j < 3; ++j)
    {
        chunks[0].centroid[j] = static_cast<float>(weightedCenter[j] / totalVolume);
        if (!std::isfinite(chunks[0].centroid[j])) return 0;
    }
    std::vector<NvBlastBondDesc> bonds(bondCount);
    std::set<uint64_t> pairs;
    for (uint32_t i = 0; i < bondCount; ++i)
    {
        uint32_t a = bondData[i * 2], b = bondData[i * 2 + 1];
        if (a >= count || b >= count || a == b) return 0;
        if (a > b) std::swap(a, b);
        if (!pairs.insert((uint64_t(a) << 32) | b).second) return 0;
        auto& bond = bonds[i];
        bond.chunkIndices[0] = a + 1; bond.chunkIndices[1] = b + 1;
        if (areas && (!std::isfinite(areas[i]) || areas[i] <= 0 ||
            !std::isfinite(areas[i] * initialHealth) || areas[i] * initialHealth <= 0)) return 0;
        bond.bond.userData = i; bond.bond.area = areas ? areas[i] : 1.f;
        double length2 = 0, delta[3];
        for (uint32_t j = 0; j < 3; ++j)
        {
            bond.bond.centroid[j] = static_cast<float>((double(chunks[a + 1].centroid[j]) + chunks[b + 1].centroid[j]) * .5);
            delta[j] = double(chunks[b + 1].centroid[j]) - chunks[a + 1].centroid[j];
            length2 += delta[j] * delta[j];
        }
        // The upstream solver stores displacement norms in float. Reject only
        // genuine native underflow/overflow, not an arbitrary world-size cap.
        if (!std::isfinite(length2) || !std::isfinite(static_cast<float>(length2)) ||
            static_cast<float>(length2) <= 0) return 0;
        const double length = std::sqrt(length2);
        for (uint32_t j = 0; j < 3; ++j) bond.bond.normal[j] = static_cast<float>(delta[j] / length);
        if (centroids)
        {
            double normalLength2 = 0, direction = 0, arms2[2] = {};
            const float orientation = bondData[i * 2] == a ? 1.f : -1.f;
            for (uint32_t j = 0; j < 3; ++j)
            {
                const float n = normals[3 * i + j];
                const float c = centroids[3 * i + j];
                if (!std::isfinite(n) || !std::isfinite(c)) return 0;
                normalLength2 += double(n) * n;
                direction += double(n) * orientation * delta[j];
                bond.bond.centroid[j] = c;
                bond.bond.normal[j] = n * orientation;
                const double armA=double(c)-chunks[a+1].centroid[j], armB=double(c)-chunks[b+1].centroid[j];
                arms2[0]+=armA*armA; arms2[1]+=armB*armB;
            }
            if (std::abs(normalLength2 - 1.0) > 2e-4 || direction <= 0 ||
                !std::isfinite(float(arms2[0])) || !std::isfinite(float(arms2[1]))) return 0;
        }
    }
    auto* result = new BrowserBlastFamily();
    const NvBlastAssetDesc descriptor = {count + 1, chunks.data(), bondCount, bonds.data()};
    const size_t bytes = NvBlastGetAssetMemorySize(&descriptor, nullptr);
    if (!bytes) { delete result; return 0; }
    result->assetMemory = allocateZeroed16(bytes);
    result->scratch.resize(NvBlastGetRequiredScratchForCreateAsset(&descriptor, nullptr));
    if (!result->assetMemory) { delete result; return 0; }
    result->asset = NvBlastCreateAsset(result->assetMemory, &descriptor, result->scratch.data(), nullptr);
    if (!result->asset) { delete result; return 0; }
    result->familyMemory = allocateZeroed16(NvBlastAssetGetFamilyMemorySize(result->asset, nullptr));
    if (!result->familyMemory) { delete result; return 0; }
    result->family = NvBlastAssetCreateFamily(result->familyMemory, result->asset, nullptr);
    if (!result->family) { delete result; return 0; }
    NvBlastActorDesc actorDescriptor{};
    actorDescriptor.uniformInitialBondHealth = initialHealth;
    actorDescriptor.uniformInitialLowerSupportChunkHealth = initialHealth;
    result->physicalAreas = areas != nullptr;
    result->assetBondIndices.resize(bondCount);
    std::vector<float> healths(bondCount);
    const NvBlastBond* assetBonds = NvBlastAssetGetBonds(result->asset, nullptr);
    for (uint32_t i = 0; i < bondCount; ++i)
    {
        const uint32_t authoredIndex = assetBonds[i].userData;
        if (authoredIndex >= bondCount) { delete result; return 0; }
        result->assetBondIndices[authoredIndex] = i;
        healths[i] = areas ? areas[authoredIndex] * initialHealth : initialHealth;
    }
    if (areas) actorDescriptor.initialBondHealths = healths.data();
    result->scratch.resize(NvBlastFamilyGetRequiredScratchForCreateFirstActor(result->family, nullptr));
    auto* actor = NvBlastFamilyCreateFirstActor(result->family, &actorDescriptor, result->scratch.data(), nullptr);
    if (!actor) { delete result; return 0; }
    result->actors.push_back(actor);
    result->chunkCount = count;
    result->chunkNodes.resize(count, UINT32_MAX);
    const auto graph = NvBlastAssetGetSupportGraph(result->asset, nullptr);
    for (uint32_t i = 0; i < graph.nodeCount; ++i)
        if (graph.chunkIndices[i] > 0 && graph.chunkIndices[i] <= count)
            result->chunkNodes[graph.chunkIndices[i] - 1] = i;
    for (uint32_t i = 0; i < bondCount; ++i)
        result->bonds.push_back({i, result->chunkNodes[bondData[i * 2]], result->chunkNodes[bondData[i * 2 + 1]], 0.f});
    browserFamilies.insert(result);
    return reinterpret_cast<uintptr_t>(result);
}

extern "C" EMSCRIPTEN_KEEPALIVE uintptr_t pr_blast_family_create(
    uint32_t count, const float* chunks, uint32_t bondCount, const uint32_t* bonds, float health)
{ return createFamily(count, chunks, bondCount, bonds, health, nullptr); }

// Physical opt-in: health is remaining-area fraction, never a material stress.
extern "C" EMSCRIPTEN_KEEPALIVE uintptr_t pr_blast_family_create_physical(
    uint32_t count, const float* chunks, uint32_t bondCount, const uint32_t* bonds,
    float healthFraction, const float* bondAreasM2)
{
    if (!bondAreasM2 || !std::isfinite(healthFraction) || healthFraction <= 0 || healthFraction > 1) return 0;
    return createFamily(count, chunks, bondCount, bonds, healthFraction, bondAreasM2);
}

// Authored physical interfaces preserve the actual cut plane, not center-line
// approximations. Input normals point from the first endpoint to the second.
extern "C" EMSCRIPTEN_KEEPALIVE uintptr_t pr_blast_family_create_authored(
    uint32_t count, const float* chunks, uint32_t bondCount, const uint32_t* bonds,
    float healthFraction, const float* areas, const float* centroids, const float* normals)
{
    if (!areas || !centroids || !normals || !std::isfinite(healthFraction) ||
        healthFraction <= 0 || healthFraction > 1) return 0;
    return createFamily(count, chunks, bondCount, bonds, healthFraction, areas, centroids, normals);
}

// Original bond order; normal is in native canonical endpoint order.
extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_family_bond_geometry(
    uintptr_t handle, float* output, uint32_t capacity)
{
    auto* family = checkedFamily(handle);
    if (!family || capacity < family->bonds.size() ||
        !validSpan(output, uint64_t(family->bonds.size()) * 7)) return -1;
    const auto* bonds = NvBlastAssetGetBonds(family->asset, nullptr);
    for (uint32_t i = 0; i < family->bonds.size(); ++i)
    {
        const auto& bond = bonds[family->assetBondIndices[i]];
        output[7 * i] = bond.area;
        for (uint32_t axis = 0; axis < 3; ++axis)
        {
            output[7 * i + 1 + axis] = bond.centroid[axis];
            output[7 * i + 4 + axis] = bond.normal[axis];
        }
    }
    return static_cast<int32_t>(family->bonds.size());
}

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_family_damage(uintptr_t handle, uint32_t bondIndex, float damage)
{
    auto* family = checkedFamily(handle);
    if (!family || bondIndex >= family->bonds.size() || !std::isfinite(damage) || damage < 0) return -1;
    family->lastBondWrenchValid.clear();
    family->sectionResultsValid = false;
    auto command = family->bonds[bondIndex]; command.health = damage;
    const NvBlastFractureBuffers commands{1, 0, &command, nullptr};
    for (auto* actor : family->actors) NvBlastActorApplyFracture(nullptr, actor, &commands, nullptr, nullptr);
    return splitFamily(*family);
}

extern "C" EMSCRIPTEN_KEEPALIVE uint32_t pr_blast_stress_abi() { return 1; }
extern "C" EMSCRIPTEN_KEEPALIVE uint32_t pr_blast_stress_physical_abi() { return 1; }
extern "C" EMSCRIPTEN_KEEPALIVE uint32_t pr_blast_stress_sections_abi() { return 1; }
extern "C" EMSCRIPTEN_KEEPALIVE uint32_t pr_blast_stress_sections_v3_abi() { return 1; }
// Original caller f32 geometry/mass/load datums prepared in binary64. Distinct
// from earlier revision3 builds which rounded preparation intermediates to f32.
extern "C" EMSCRIPTEN_KEEPALIVE uint32_t pr_blast_stress_sections_v3_preparation_abi() { return 1; }
// 0 = pinned NVIDIA scalar solver, CPU/WASM; not a GPU or native AVX backend.
extern "C" EMSCRIPTEN_KEEPALIVE uint32_t pr_blast_stress_backend() { return 0; }

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_family_bond_healths(
    uintptr_t handle, float* output, uint32_t capacity)
{
    auto* family = checkedFamily(handle);
    if (!family || capacity < family->bonds.size() || family->actors.empty() ||
        !validSpan(output, family->bonds.size())) return -1;
    const float* healths = NvBlastActorGetBondHealths(family->actors.front(), nullptr);
    if (!healths) return -1;
    for (uint32_t i = 0; i < family->bonds.size(); ++i) output[i] = healths[family->assetBondIndices[i]];
    return static_cast<int32_t>(family->bonds.size());
}

// Immutable solver setup. Node mass0 means externally anchored support.
// Limits are compression/tension/shear (elastic,fatal), all in pascals.
extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_configure(
    uintptr_t handle, const float* massesKg, const float* limitsPa, uint32_t iterations)
{
    auto* family = checkedFamily(handle);
    if (!family || !family->physicalAreas || family->stress || !iterations ||
        !validSpan(massesKg, family->chunkCount) || !validSpan(limitsPa, 6)) return -1;
    for (uint32_t i = 0; i < family->chunkCount; ++i)
        if (!std::isfinite(massesKg[i]) || massesKg[i] < 0) return -1;
    for (uint32_t i = 0; i < 6; i += 2)
        if (!std::isfinite(limitsPa[i]) || !std::isfinite(limitsPa[i + 1]) ||
            limitsPa[i] < 0 || limitsPa[i + 1] <= limitsPa[i]) return -1;
    const auto graph = NvBlastAssetGetSupportGraph(family->asset, nullptr);
    const NvBlastChunk* chunks = NvBlastAssetGetChunks(family->asset, nullptr);
    for (uint32_t i = 0; i < family->chunkCount; ++i)
    {
        if (massesKg[i] == 0) continue;
        const float volume = chunks[graph.chunkIndices[family->chunkNodes[i]]].volume;
        // Match the upstream float sphere-inertia approximation before setup.
        const float radius = std::pow(volume * 3.f * 0.31830988618379067154f / 4.f, 1.f / 3.f);
        const float inertia = massesKg[i] * (radius * radius * .4f);
        if (!std::isfinite(radius) || !std::isfinite(inertia) || inertia <= 0) return -1;
    }
    Nv::Blast::ExtStressSolverSettings settings;
    settings.maxSolverIterationsPerFrame = iterations;
    settings.graphReductionLevel = 0;
    settings.compressionElasticLimit = limitsPa[0]; settings.compressionFatalLimit = limitsPa[1];
    settings.tensionElasticLimit = limitsPa[2]; settings.tensionFatalLimit = limitsPa[3];
    settings.shearElasticLimit = limitsPa[4]; settings.shearFatalLimit = limitsPa[5];
    auto* solver = Nv::Blast::ExtStressSolver::create(*family->family, settings);
    if (!solver) return -1;
    for (uint32_t i = 0; i < family->chunkCount; ++i)
    {
        const uint32_t node = family->chunkNodes[i];
        const auto& chunk = chunks[graph.chunkIndices[node]];
        solver->setNodeInfo(node, massesKg[i], chunk.volume,
            {chunk.centroid[0], chunk.centroid[1], chunk.centroid[2]});
    }
    for (auto* actor : family->actors) solver->notifyActorCreated(*actor);
    family->massesKg.assign(massesKg, massesKg + family->chunkCount);
    family->stress = solver;
    return 0;
}

// Opt-in operator preserves actual node masses, upstream volume-derived sphere
// inertia, and authored bond centroids. The legacy operator remains the default.
extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_configure_physical(
    uintptr_t handle, const float* massesKg, const float* limitsPa, uint32_t iterations)
{
    if (pr_blast_stress_configure(handle, massesKg, limitsPa, iterations) != 0) return -1;
    auto* family = checkedFamily(handle);
    pr_blast_enable_physical_stress(family->stress);
    family->physicalStress = true;
    return 0;
}

extern "C" EMSCRIPTEN_KEEPALIVE uint32_t pr_blast_stress_mass_abi() { return 1; }

// Supports and authored geometry are immutable. Upstream setNodeInfo marks the
// existing graph dirty, so the next update refreshes mass/inertia without
// replacing actors, resetting its frame, or modifying any remaining bond area.
extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_set_masses(
    uintptr_t handle, const float* massesKg, uint32_t chunkCount)
{
    auto* family = checkedFamily(handle);
    if (!family || !family->stress || chunkCount != family->chunkCount ||
        family->massesKg.size() != chunkCount || !validSpan(massesKg, chunkCount)) return -1;
    const auto graph = NvBlastAssetGetSupportGraph(family->asset, nullptr);
    const NvBlastChunk* chunks = NvBlastAssetGetChunks(family->asset, nullptr);
    for (uint32_t i = 0; i < chunkCount; ++i)
    {
        const float mass = massesKg[i];
        if (!std::isfinite(mass) || mass < 0 || (mass == 0) != (family->massesKg[i] == 0)) return -1;
        if (mass == 0) continue;
        const auto& chunk = chunks[graph.chunkIndices[family->chunkNodes[i]]];
        const float radius = std::pow(chunk.volume * 3.f * 0.31830988618379067154f / 4.f, 1.f / 3.f);
        const float inertia = mass * (radius * radius * .4f);
        if (!std::isfinite(radius) || !std::isfinite(inertia) || inertia <= 0) return -1;
    }
    if(family->physicalNumericalFailure) return -1;
    bool changed=false;
    for(uint32_t i=0;i<chunkCount;++i) changed=changed || massesKg[i]!=family->massesKg[i];
    if(!changed) return 0;
    family->sectionResultsValid=false;
    family->lastBondWrenchValid.clear();
    // No allocation or fallible operation follows the first native mutation.
    for (uint32_t i = 0; i < chunkCount; ++i)
    {
        if (massesKg[i] == family->massesKg[i]) continue;
        const uint32_t node = family->chunkNodes[i];
        const auto& chunk = chunks[graph.chunkIndices[node]];
        family->stress->setNodeInfo(node, massesKg[i], chunk.volume,
            {chunk.centroid[0], chunk.centroid[1], chunk.centroid[2]});
        family->massesKg[i] = massesKg[i];
    }
    return 0;
}

// Relative normal-equation gradient tolerance, not an absolute force/torque
// tolerance. The opt-in per-solver value is declared in the build manifest.
extern "C" EMSCRIPTEN_KEEPALIVE float pr_blast_stress_physical_tolerance(uintptr_t handle)
{
    auto* family = checkedFamily(handle);
    if (!family || !family->stress || !family->physicalStress) return -1.f;
    return pr_blast_physical_solver_tolerance(family->stress);
}

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_physical_revision(uintptr_t handle)
{
    const auto* family = checkedFamily(handle);
    if (!family || !family->physicalStress || !family->stress) return -1;
    return family->sectionStress ? int32_t(family->sectionRevision) : 1;
}

// Immutable physical full-section operator. K is row-major [F,M] versus [u,theta].
// Each sample is 18 traction-map coefficients followed by six Pa capacities.
// This auxiliary solver NEVER applies damage; the authoritative fine graph owns it.
static int32_t configureSections(uintptr_t handle,
    const float* massesKg,uint32_t iterations,uint32_t bondCount,const double* stiffness,
    const uint32_t* offsets,const double* samples,uint32_t sampleCount,uint32_t revision)
{
    auto* family = checkedFamily(handle);
    if (!family || family->stress || !iterations || iterations > INT32_MAX || bondCount != family->bonds.size() || sampleCount > INT32_MAX ||
        !validSpan(stiffness,uint64_t(bondCount)*36) || !validSpan(offsets,uint64_t(bondCount)+1) ||
        !validSpan(samples,uint64_t(sampleCount)*24) || offsets[0] != 0 || offsets[bondCount] != sampleCount) return -1;
    for (uint32_t b = 0; b < bondCount; ++b)
    {
        if (offsets[b] >= offsets[b+1] || offsets[b+1] > sampleCount) return -1;
        double lower[36];
        if (!prSectionCholesky(stiffness+uint64_t(b)*36,lower)) return -1;
    }
    for (uint32_t s = 0; s < sampleCount; ++s)
    {
        const double* item = samples+uint64_t(s)*24;
        for (unsigned j = 0; j < 24; ++j) if (!std::isfinite(item[j])) return -1;
        for (unsigned j = 18; j < 24; j += 2)
            if (item[j] < 0 || item[j+1] <= item[j] || !std::isfinite(item[j+1]-item[j])) return -1;
    }
    // Stage all caller data before creating the solver. No retained heap views.
    std::vector<double> ordered(uint64_t(bondCount)*36), copiedSamples(samples,samples+uint64_t(sampleCount)*24);
    std::vector<uint32_t> copiedOffsets(offsets,offsets+uint64_t(bondCount)+1);
    for (uint32_t b = 0; b < bondCount; ++b)
        std::copy_n(stiffness+uint64_t(b)*36,36,ordered.data()+uint64_t(family->assetBondIndices[b])*36);
    const float unusedLimits[6] = {0,1,0,1,0,1};
    if (pr_blast_stress_configure_physical(handle,massesKg,unusedLimits,iterations) != 0) return -1;
    if (revision == 3) pr_blast_enable_section_stress_v3(family->stress,ordered.data(),bondCount);
    else pr_blast_enable_section_stress(family->stress,ordered.data(),bondCount);
    family->sectionStress = true;
    family->sectionRevision = revision;
    family->sectionOffsets = std::move(copiedOffsets);
    family->sectionSamples = std::move(copiedSamples);
    family->sectionResults.resize(sampleCount);
    return 0;
}

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_configure_sections(uintptr_t handle,
    const float* massesKg,uint32_t iterations,uint32_t bondCount,const double* stiffness,
    const uint32_t* offsets,const double* samples,uint32_t sampleCount)
{ return configureSections(handle,massesKg,iterations,bondCount,stiffness,offsets,samples,sampleCount,2); }

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_configure_sections_v3(uintptr_t handle,
    const float* massesKg,uint32_t iterations,uint32_t bondCount,const double* stiffness,
    const uint32_t* offsets,const double* samples,uint32_t sampleCount)
{ return configureSections(handle,massesKg,iterations,bondCount,stiffness,offsets,samples,sampleCount,3); }

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_physical_progress(uintptr_t handle,double* output,uint32_t capacity)
{
    auto* family = checkedFamily(handle);
    if (!family || !family->sectionStress || capacity < 8 || !validSpan(output,8)) return -1;
    double staged[8];
    if (!pr_blast_physical_solver_progress(family->stress,staged)) return -1;
    std::copy_n(staged,8,output);
    return 0;
}

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_sections_v3_work(uintptr_t handle,double* output,uint32_t capacity)
{
    auto* family=checkedFamily(handle);
    if(!family || !family->sectionStress || family->sectionRevision!=3 || capacity<12 || !validSpan(output,12)) return -1;
    double staged[12];
    if(!pr_blast_section_v3_work(family->stress,staged)) return -1;
    std::copy_n(staged,12,output);
    return 0;
}

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_section_results(uintptr_t handle,double* output,uint32_t capacity)
{
    auto* family = checkedFamily(handle);
    if (!family || !family->sectionStress || !family->sectionResultsValid || family->physicalNumericalFailure ||
        capacity < family->sectionResults.size() || !validSpan(output,family->sectionResults.size())) return -1;
    std::copy(family->sectionResults.begin(),family->sectionResults.end(),output);
    return int32_t(family->sectionResults.size());
}

// Forces are newtons in authored local axes, not impulses. One native update
// clears the applied loads; its damage law is per-update and has no dt input.
static int32_t updateStress(
    uintptr_t handle, const float* forcesN, uint32_t chunkCount, bool physical)
{
    auto* family = checkedFamily(handle);
    if (!family || !family->stress || family->physicalStress != physical || chunkCount != family->chunkCount ||
        !validSpan(forcesN, uint64_t(chunkCount) * 3)) return -1;
    if (family->physicalNumericalFailure) return -2;
    for (uint32_t i = 0; i < 3 * chunkCount; ++i) if (!std::isfinite(forcesN[i])) return -1;
    for (uint32_t i = 0; i < chunkCount; ++i)
        if (family->massesKg[i] > 0) for (uint32_t axis = 0; axis < 3; ++axis)
            if (!std::isfinite(forcesN[3 * i + axis] / family->massesKg[i])) return -1;
    if(family->sectionRevision==3 && !pr_blast_section_v3_loads(family->stress,forcesN,family->chunkNodes.data(),chunkCount)) return -1;
    family->sectionResultsValid = false;
    for (uint32_t i = 0; i < chunkCount; ++i)
        family->stress->addForce(family->chunkNodes[i], {forcesN[3*i], forcesN[3*i+1], forcesN[3*i+2]}, Nv::Blast::ExtForceMode::FORCE);
    family->stress->update();
    if (physical)
    {
        bool finiteSolve = std::isfinite(family->stress->getStressErrorLinear()) &&
            std::isfinite(family->stress->getStressErrorAngular());
        family->lastBondWrenches.resize(family->bonds.size() * 6);
        family->lastBondWrenchValid.assign(family->bonds.size(), 0);
        for (uint32_t i = 0; i < family->bonds.size(); ++i)
        {
            float* result = family->lastBondWrenches.data() + i * 6;
            if (pr_blast_physical_bond_impulse(family->stress, family->assetBondIndices[i],
                family->bonds[i].nodeIndex0, result))
            {
                bool finite = true;
                for (uint32_t j = 0; j < 6; ++j) finite = finite && std::isfinite(result[j]);
                if (!family->sectionStress)
                {
                    float stresses[3]{};
                    finite = finite && pr_blast_physical_bond_stress(family->stress,family->assetBondIndices[i],stresses);
                    for (float value : stresses) finite = finite && std::isfinite(value) && value >= 0;
                }
                family->lastBondWrenchValid[i] = finite ? 1 : 0;
                finiteSolve = finiteSolve && finite;
            }
        }
        if (!finiteSolve)
        {
            // Finite inputs can overflow the iterative operator. Never treat
            // Inf<=Inf inside its convergence test as an admitted solution.
            // This warm-start state is unusable and must be disposed/recreated.
            family->physicalNumericalFailure = true;
            family->lastBondWrenchValid.clear();
            return -2;
        }
        // No health or actor mutation is allowed from a provisional iterate.
        // The solver retains its warm start; the caller holds physical time.
        if (!family->stress->converged()) return 0;
    }
    if (family->sectionStress)
    {
        const float* health = family->actors.empty() ? nullptr : NvBlastActorGetBondHealths(family->actors.front(),nullptr);
        bool valid = health != nullptr;
        for (uint32_t b = 0; b < family->bonds.size() && valid; ++b)
        {
            const bool live = health[family->assetBondIndices[b]] > 0;
            if (live && !family->lastBondWrenchValid[b]) { valid = false; break; }
            for (uint32_t s = family->sectionOffsets[b]; s < family->sectionOffsets[b+1]; ++s)
            {
                if (!live) { family->sectionResults[s] = 0; continue; }
                const double* sample = family->sectionSamples.data()+uint64_t(s)*24;
                const float* wrench = family->lastBondWrenches.data()+uint64_t(b)*6;
                double t[3]{};
                for (unsigned row = 0; row < 3; ++row) for (unsigned j = 0; j < 6; ++j)
                    t[row] += sample[row*6+j]*double(wrench[j]);
                const double shear = std::hypot(t[1],t[2]);
                const unsigned normal = t[0] <= 0 ? 18 : 20;
                const double pressure = std::abs(t[0]);
                double severity = 0;
                if (pressure > sample[normal]) severity += (pressure-sample[normal])/(sample[normal+1]-sample[normal]);
                if (shear > sample[22]) severity += (shear-sample[22])/(sample[23]-sample[22]);
                valid = valid && std::isfinite(t[0]) && std::isfinite(t[1]) && std::isfinite(t[2]) &&
                    std::isfinite(shear) && std::isfinite(severity) && severity >= 0;
                family->sectionResults[s] = severity;
            }
        }
        if (!valid)
        {
            family->physicalNumericalFailure = true; family->lastBondWrenchValid.clear(); return -2;
        }
        family->sectionResultsValid = true;
        return int32_t(family->actors.size());
    }
    // All solver-owned command spans must be copied before another generate.
    std::vector<NvBlastBondFractureData> damage;
    for (auto* actor : family->actors)
    {
        NvBlastFractureBuffers commands{};
        family->stress->generateFractureCommands(*actor, commands);
        if (commands.bondFractureCount)
            damage.insert(damage.end(), commands.bondFractures, commands.bondFractures + commands.bondFractureCount);
    }
    if (!damage.empty())
    {
        if (physical && std::any_of(damage.begin(), damage.end(), [](const NvBlastBondFractureData& item) {
            return !std::isfinite(item.health) || item.health < 0;
        }))
        {
            family->physicalNumericalFailure = true;
            family->lastBondWrenchValid.clear();
            return -2;
        }
        const NvBlastFractureBuffers commands{static_cast<uint32_t>(damage.size()), 0, damage.data(), nullptr};
        for (auto* actor : family->actors) NvBlastActorApplyFracture(nullptr, actor, &commands, nullptr, nullptr);
    }
    return splitFamily(*family);
}

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_update(
    uintptr_t handle, const float* forcesN, uint32_t chunkCount)
{
    return updateStress(handle, forcesN, chunkCount, false);
}

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_update_physical(
    uintptr_t handle, const float* forcesN, uint32_t chunkCount)
{
    return updateStress(handle, forcesN, chunkCount, true);
}

// Latest pre-fracture physical-solve force(N) and couple(Nm), on the first
// authored bond chunk and about its authored interface centroid. Pending
// iterates may be inspected, but are not accepted physical damage evidence.
extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_bond_wrench(
    uintptr_t handle, uint32_t bondIndex, float* output, uint32_t capacity)
{
    auto* family = checkedFamily(handle);
    if (!family || !family->physicalStress || bondIndex >= family->bonds.size() ||
        capacity < 6 || !validSpan(output, 6)) return -1;
    if (bondIndex >= family->lastBondWrenchValid.size() || !family->lastBondWrenchValid[bondIndex]) return 0;
    std::copy_n(family->lastBondWrenches.data() + bondIndex * 6, 6, output);
    return 1;
}

// Double receipt avoids losing the uint32 frame count in an f32 transport.
extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_state(uintptr_t handle, double* output, uint32_t capacity)
{
    auto* family = checkedFamily(handle);
    if (!family || !family->stress || capacity < 6 || !validSpan(output, 6)) return -1;
    output[0] = family->stress->getFrameCount();
    output[1] = family->stress->getOverstressedBondCount();
    output[2] = family->stress->getStressErrorLinear();
    output[3] = family->stress->getStressErrorAngular();
    output[4] = (family->sectionStress ? family->sectionResultsValid : family->stress->converged()) ? 1 : 0;
    output[5] = family->stress->getBondCount();
    return 0;
}

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_family_groups(uintptr_t handle, uint32_t* groups, uint32_t capacity)
{
    auto* family = checkedFamily(handle);
    if (!family || capacity < family->chunkCount || !validSpan(groups, family->chunkCount)) return -1;
    std::fill(groups, groups + family->chunkCount, UINT32_MAX);
    std::vector<uint32_t> nodes(family->chunkCount);
    const auto graph = NvBlastAssetGetSupportGraph(family->asset, nullptr);
    for (auto* actor : family->actors)
    {
        const uint32_t actorIndex = NvBlastActorGetIndex(actor, nullptr);
        const uint32_t count = NvBlastActorGetGraphNodeIndices(nodes.data(), family->chunkCount, actor, nullptr);
        for (uint32_t i = 0; i < count; ++i)
        {
            const uint32_t chunk = graph.chunkIndices[nodes[i]];
            if (chunk > 0 && chunk <= family->chunkCount) groups[chunk - 1] = actorIndex;
        }
    }
    return static_cast<int32_t>(family->actors.size());
}

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_family_destroy(uintptr_t handle)
{
    auto* family = checkedFamily(handle);
    if (!family) return -1;
    browserFamilies.erase(family); delete family; return 0;
}

extern "C" EMSCRIPTEN_KEEPALIVE uint32_t pr_blast_scene_abi() { return 1; }
extern "C" EMSCRIPTEN_KEEPALIVE uint32_t pr_blast_live_families() { return static_cast<uint32_t>(browserFamilies.size()); }

extern "C" EMSCRIPTEN_KEEPALIVE uint32_t pr_blast_version()
{
    return 50006u;
}

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_smoke()
{
    const NvBlastChunkDesc chunks[3] = {
        {{0.0f, 0.0f, 0.0f}, 2.0f, UINT32_MAX, NvBlastChunkDesc::NoFlags, 0},
        {{-0.5f, 0.0f, 0.0f}, 1.0f, 0, NvBlastChunkDesc::SupportFlag, 1},
        {{0.5f, 0.0f, 0.0f}, 1.0f, 0, NvBlastChunkDesc::SupportFlag, 2},
    };
    const NvBlastBondDesc bonds[1] = {
        {{{1.0f, 0.0f, 0.0f}, 1.0f, {0.0f, 0.0f, 0.0f}, 7}, {1, 2}},
    };
    const NvBlastAssetDesc assetDesc = {3, chunks, 1, bonds};

    const size_t assetBytes = NvBlastGetAssetMemorySize(&assetDesc, nullptr);
    const size_t createScratchBytes = NvBlastGetRequiredScratchForCreateAsset(&assetDesc, nullptr);
    if (assetBytes == 0 || createScratchBytes == 0)
        return -1;

    void* assetMemory = allocateZeroed16(assetBytes);
    std::vector<uint8_t> scratch(createScratchBytes);
    if (assetMemory == nullptr)
        return -2;
    NvBlastAsset* asset = NvBlastCreateAsset(assetMemory, &assetDesc, scratch.data(), nullptr);
    if (asset == nullptr)
    {
        std::free(assetMemory);
        return -3;
    }

    void* familyMemory = allocateZeroed16(NvBlastAssetGetFamilyMemorySize(asset, nullptr));
    if (familyMemory == nullptr)
    {
        std::free(assetMemory);
        return -4;
    }
    NvBlastFamily* family = NvBlastAssetCreateFamily(familyMemory, asset, nullptr);
    if (family == nullptr)
    {
        std::free(familyMemory);
        std::free(assetMemory);
        return -5;
    }

    NvBlastActorDesc actorDesc{};
    actorDesc.uniformInitialBondHealth = 1.0f;
    actorDesc.uniformInitialLowerSupportChunkHealth = 1.0f;
    scratch.resize(NvBlastFamilyGetRequiredScratchForCreateFirstActor(family, nullptr));
    NvBlastActor* actor = NvBlastFamilyCreateFirstActor(family, &actorDesc, scratch.data(), nullptr);
    if (actor == nullptr)
    {
        std::free(familyMemory);
        std::free(assetMemory);
        return -6;
    }

    NvBlastBondFractureData command{7, 0, 1, 2.0f};
    NvBlastBondFractureData event{};
    const NvBlastFractureBuffers commands{1, 0, &command, nullptr};
    NvBlastFractureBuffers events{1, 0, &event, nullptr};
    NvBlastActorApplyFracture(&events, actor, &commands, nullptr, nullptr);
    if (events.bondFractureCount != 1 || event.userdata != 7 ||
        !NvBlastActorIsSplitRequired(actor, nullptr))
    {
        NvBlastActorDeactivate(actor, nullptr);
        std::free(familyMemory);
        std::free(assetMemory);
        return -7;
    }

    NvBlastActor* children[2] = {nullptr, nullptr};
    NvBlastActorSplitEvent split{nullptr, children};
    scratch.resize(NvBlastActorGetRequiredScratchForSplit(actor, nullptr));
    const uint32_t childCount = NvBlastActorSplit(
        &split, actor, 2, scratch.data(), nullptr, nullptr);
    const bool splitValid = childCount == 2 && split.deletedActor == actor &&
        children[0] != nullptr && children[1] != nullptr;
    for (uint32_t index = 0; index < childCount && index < 2; ++index)
        NvBlastActorDeactivate(children[index], nullptr);
    std::free(familyMemory);
    std::free(assetMemory);
    return splitValid ? 0 : -8;
}
