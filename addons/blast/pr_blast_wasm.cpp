// SPDX-License-Identifier: MIT
// Minimal browser-facing validation boundary for the pinned NVIDIA Blast core.
#include <emscripten/emscripten.h>
#include <emscripten/heap.h>
#include "pr_blast_memory.h"

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
    auto command = family->bonds[bondIndex]; command.health = damage;
    const NvBlastFractureBuffers commands{1, 0, &command, nullptr};
    for (auto* actor : family->actors) NvBlastActorApplyFracture(nullptr, actor, &commands, nullptr, nullptr);
    return splitFamily(*family);
}

extern "C" EMSCRIPTEN_KEEPALIVE uint32_t pr_blast_stress_abi() { return 1; }
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

// Forces are newtons in authored local axes, not impulses. One native update
// clears the applied loads; its damage law is per-update and has no dt input.
extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_stress_update(
    uintptr_t handle, const float* forcesN, uint32_t chunkCount)
{
    auto* family = checkedFamily(handle);
    if (!family || !family->stress || chunkCount != family->chunkCount ||
        !validSpan(forcesN, uint64_t(chunkCount) * 3)) return -1;
    for (uint32_t i = 0; i < 3 * chunkCount; ++i) if (!std::isfinite(forcesN[i])) return -1;
    for (uint32_t i = 0; i < chunkCount; ++i)
        if (family->massesKg[i] > 0) for (uint32_t axis = 0; axis < 3; ++axis)
            if (!std::isfinite(forcesN[3 * i + axis] / family->massesKg[i])) return -1;
    for (uint32_t i = 0; i < chunkCount; ++i)
        family->stress->addForce(family->chunkNodes[i], {forcesN[3*i], forcesN[3*i+1], forcesN[3*i+2]}, Nv::Blast::ExtForceMode::FORCE);
    family->stress->update();
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
        const NvBlastFractureBuffers commands{static_cast<uint32_t>(damage.size()), 0, damage.data(), nullptr};
        for (auto* actor : family->actors) NvBlastActorApplyFracture(nullptr, actor, &commands, nullptr, nullptr);
    }
    return splitFamily(*family);
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
    output[4] = family->stress->converged() ? 1 : 0;
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
