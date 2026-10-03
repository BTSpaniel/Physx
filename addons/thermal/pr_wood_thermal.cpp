// SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) <https://github.com/BTSpaniel>
// SPDX-License-Identifier: MIT
// Ordered f64 execution of Engine WoodCombustion's explicit outer-step loop.
// The caller owns every byte; no native context or pointer survives a call.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#ifdef __EMSCRIPTEN__
#include <emscripten/emscripten.h>
#include <emscripten/heap.h>
#define PR_EXPORT EMSCRIPTEN_KEEPALIVE
#else
#define PR_EXPORT
#endif
#ifdef PR_THERMAL_PROFILE
#define PR_TRACE __attribute__((noinline))
#else
#define PR_TRACE
#endif

namespace {
constexpr uint32_t magic = 0x50525754u, abi = 1, headerBytes = 96;
constexpr uint32_t layerStride = 12, edgeStride = 6, boundaryStride = 9, cellStride = 12, workStride = 24;
enum Status : uint32_t { OK = 0, HEADER = 1, SPAN = 2, DOMAIN = 3, TIMESTEP = 4, BUDGET = 5, NONFINITE = 6 };
enum Layer : uint32_t { CELL, INITIAL, WOOD, CHAR, ASH, WATER, TEMPERATURE, ENERGY, OXYGEN, VOLATILE };
enum Cell : uint32_t { GAS, STEAM, CO2, GAS_HEAT, GAS_SENSIBLE, STEAM_LATENT, FACE_HEAT };
enum Ledger : uint32_t { INITIAL_MASS, INITIAL_ENERGY, VOLATILE_KG, STEAM_KG, CO2_KG, OXYGEN_KG,
    CONVECTION_J, RADIATION_J, PYROLYSIS_J, CHAR_J, GAS_SENSIBLE_J, GAS_REACTION_J, STEAM_LATENT_J };
enum Work : uint32_t { POWER, CONDUCTANCE, CAPACITY, CONDUCTIVITY, OLD_T, CUBED, FOURTH, OX_FACTOR,
    CHAR_FACTOR, CACHE_WOOD, CACHE_CHAR, CACHE_ASH, CACHE_WATER, CACHE_E, CACHE_T, VALID_KNOTS, KNOT_CP };
bool isFinite(double value) { return std::isfinite(value); }
bool index(double value, uint32_t count) { return value >= 0 && value < count && std::floor(value) == value; }
// Match JS Math.min/max even when finite inputs overflow during arithmetic.
// std::min/max can hide an intermediate NaN by returning the other operand.
double low(double a, double b) {
    if (std::isnan(a) || std::isnan(b)) return std::numeric_limits<double>::quiet_NaN();
    if (a == b) return std::signbit(a) ? a : b;
    return a < b ? a : b;
}
double high(double a, double b) {
    if (std::isnan(a) || std::isnan(b)) return std::numeric_limits<double>::quiet_NaN();
    if (a == b) return std::signbit(a) ? b : a;
    return a > b ? a : b;
}

struct Kernel {
    uint32_t layerCount, edgeCount, boundaryCount, cellCount;
    double *layers, *edges, *boundaries, *cells, *ledger, *material, *scratch;
    double* reactionExtents = nullptr;
    double* layer(uint32_t i) const { return layers + i * layerStride; }
    double* work(uint32_t i) const { return scratch + i * workStride; }
    double* cell(const double* l) const { return cells + uint32_t(l[CELL]) * cellStride; }
    double ramp(const double* values, double temperature) const {
        const double* knots = material + 17;
        if (temperature <= knots[0]) return values[0];
        for (uint32_t i = 1; i < 5; ++i) if (temperature < knots[i])
            return values[i - 1] + (values[i] - values[i - 1]) * (temperature - knots[i - 1]) / (knots[i] - knots[i - 1]);
        return values[4];
    }
    PR_TRACE double capacity(const double* l, double temperature) const {
        return l[WATER] * material[5] + l[WOOD] * ramp(material + 22, temperature)
            + l[CHAR] * ramp(material + 27, temperature) + l[ASH] * ramp(material + 32, temperature);
    }
    double capacityAt(const double* l, uint32_t knot) const {
        return l[WATER] * material[5] + l[WOOD] * material[22 + knot]
            + l[CHAR] * material[27 + knot] + l[ASH] * material[32 + knot];
    }
    double specificEnergy(const double* values, double temperature) const {
        const double* knots = material + 17;
        if (temperature <= knots[0]) return values[0] * (temperature - knots[0]);
        double energy = 0;
        for (uint32_t i = 1; i < 5; ++i) {
            const double delta = low(temperature, knots[i]) - knots[i - 1];
            energy += delta * (values[i - 1] + .5 * (values[i] - values[i - 1]) * delta / (knots[i] - knots[i - 1]));
            if (temperature <= knots[i]) return energy;
        }
        return energy + values[4] * (temperature - knots[4]);
    }
    double energyAt(const double* l, double temperature) const {
        double energy = l[WATER] * material[5] * (temperature - material[2]);
        energy += l[WOOD] * specificEnergy(material + 22, temperature);
        energy += l[CHAR] * specificEnergy(material + 27, temperature);
        energy += l[ASH] * specificEnergy(material + 32, temperature);
        return energy;
    }
    PR_TRACE double temperatureAt(const double* l, double* w) const {
        if (w[CACHE_WOOD] != l[WOOD] || w[CACHE_CHAR] != l[CHAR]
            || w[CACHE_ASH] != l[ASH] || w[CACHE_WATER] != l[WATER]) {
            w[CACHE_WOOD] = l[WOOD]; w[CACHE_CHAR] = l[CHAR]; w[CACHE_ASH] = l[ASH]; w[CACHE_WATER] = l[WATER];
            w[VALID_KNOTS] = 0; w[CACHE_E] = std::numeric_limits<double>::quiet_NaN();
        }
        if (w[CACHE_E] == l[ENERGY]) return w[CACHE_T];
        if (w[VALID_KNOTS] == 0) { w[KNOT_CP] = capacityAt(l, 0); w[VALID_KNOTS] = 1; }
        double remaining = l[ENERGY], lo = material[17], cp = w[KNOT_CP], temperature = 0;
        if (remaining <= 0) temperature = lo + remaining / cp;
        else {
            bool found = false;
            for (uint32_t i = 1; i < 5; ++i) {
                if (w[VALID_KNOTS] <= i) { w[KNOT_CP + i] = capacityAt(l, i); w[VALID_KNOTS] = i + 1; }
                const double hi = material[17 + i], cpHi = w[KNOT_CP + i], width = hi - lo;
                const double intervalEnergy = .5 * (cp + cpHi) * width;
                if (remaining <= intervalEnergy) {
                    const double slope = (cpHi - cp) / width;
                    temperature = lo + 2 * remaining / (cp + std::sqrt(cp * cp + 2 * slope * remaining));
                    found = true; break;
                }
                remaining -= intervalEnergy; lo = hi; cp = cpHi;
            }
            if (!found) temperature = lo + remaining / cp;
        }
        w[CACHE_E] = l[ENERGY]; w[CACHE_T] = temperature;
        return temperature;
    }
    PR_TRACE double conductivity(const double* l) const {
        const double solid = l[WOOD] + l[CHAR] + l[ASH];
        return (0 + l[WOOD] * ramp(material + 37, l[TEMPERATURE])
            + l[CHAR] * ramp(material + 42, l[TEMPERATURE]) + l[ASH] * ramp(material + 47, l[TEMPERATURE])) / solid;
    }
    PR_TRACE double depletion(double mass, double reference, const double* reaction, double temperature, double dt, double oxygenFactor) const {
        if (!(mass > 0)) return 0;
        const double k = reaction[0] * std::exp(-reaction[1] / (material[0] * temperature)) * oxygenFactor;
        if (k == 0) return 0;
        const double fraction = mass / reference, power = 1 - reaction[2];
#ifdef PR_THERMAL_STABLE_DEPLETION
        // Equivalent frozen-temperature power-law loss, without subtracting
        // two nearly equal masses. The finite-time extinction branch is exact.
        const double fractionPower = std::pow(fraction, power);
        const double decrement = power * k * dt;
        if (decrement >= fractionPower) return mass;
        const double logRatio = std::log1p(-decrement / fractionPower) / power;
        return -mass * std::expm1(logRatio);
#else
        const double remainder = high(0, std::pow(fraction, power) - power * k * dt);
        if (mass == reference && remainder == 1) return 0;
        return high(0, low(mass, mass - reference * std::pow(remainder, 1 / power)));
#endif
    }
    PR_TRACE double reactionRate(double mass, double reference, const double* reaction, double temperature, double oxygenFactor) const {
        return mass > 0 ? reference * reaction[0] * std::exp(-reaction[1] / (material[0] * temperature))
            * std::pow(mass / reference, reaction[2]) * oxygenFactor : 0;
    }
    PR_TRACE void react(double* l, double* w, double dt) const {
        double* c = cell(l); const double reference = material[2];
        if (l[ENERGY] <= 0 && l[CHAR] == 0) return;
        if (l[WATER] > 0 && l[TEMPERATURE] > material[6]) {
            const double evaporated = low(l[WATER], high(0, l[ENERGY] - energyAt(l, material[6])) / material[7]);
            const double sensible = evaporated * material[5] * (material[6] - reference);
            l[WATER] -= evaporated; l[ENERGY] -= sensible + evaporated * material[7];
            ledger[STEAM_KG] += evaporated; c[STEAM] += evaporated;
            if (reactionExtents) reactionExtents[2] += evaporated;
            ledger[GAS_SENSIBLE_J] += sensible; ledger[STEAM_LATENT_J] += evaporated * material[7];
            c[GAS_SENSIBLE] += sensible; c[STEAM_LATENT] += evaporated * material[7];
            l[TEMPERATURE] = temperatureAt(l, w);
        }
        const double temperature = l[TEMPERATURE];
        double dry = depletion(l[WOOD], l[INITIAL], material + 52, temperature, dt, 1);
        double oxidative = depletion(l[WOOD], l[INITIAL], material + 56, temperature, dt, w[OX_FACTOR]);
        const double proposed = dry + oxidative, oxygen = oxidative * material[16];
        const double gas = proposed * (1 - material[10]) + oxygen;
        const double sensible = gas * material[8] * high(0, temperature - reference);
        const double demand = proposed * material[12] + sensible;
        const double scale = proposed > 0 ? low(low(1, l[WOOD] / proposed), demand > 0 ? high(0, l[ENERGY]) / demand : 1) : 0;
        dry *= scale; oxidative *= scale;
        const double converted = dry + oxidative, consumedOxygen = oxidative * material[16];
        if (reactionExtents) reactionExtents[0] += converted;
        const double volatileMass = converted * (1 - material[10]) + consumedOxygen;
        l[WOOD] = high(0, l[WOOD] - converted); l[CHAR] += converted * material[10]; l[ENERGY] -= demand * scale;
        ledger[PYROLYSIS_J] += converted * material[12]; ledger[GAS_SENSIBLE_J] += sensible * scale;
        c[GAS_SENSIBLE] += sensible * scale;
        ledger[OXYGEN_KG] += consumedOxygen; ledger[VOLATILE_KG] += volatileMass;
        c[GAS] += volatileMass; l[VOLATILE] += volatileMass;
        l[TEMPERATURE] = temperatureAt(l, w);
        const double oxidized = low(l[CHAR], depletion(l[CHAR], l[INITIAL] * material[10], material + 60,
            l[TEMPERATURE], dt, w[CHAR_FACTOR]));
        const double released = oxidized * material[13], co2 = oxidized * material[14];
        if (reactionExtents) reactionExtents[1] += oxidized;
        const double gasSensible = co2 * material[9] * high(0, l[TEMPERATURE] - reference);
        const double exportSensible = low(gasSensible, high(0, l[ENERGY]) + released * material[4]);
        const double gasHeat = released * (1 - material[4]);
        l[CHAR] -= oxidized; l[ASH] += oxidized * material[11]; l[ENERGY] += released - gasHeat - exportSensible;
        ledger[CHAR_J] += released; ledger[GAS_REACTION_J] += gasHeat; ledger[GAS_SENSIBLE_J] += exportSensible;
        c[GAS_SENSIBLE] += exportSensible;
        ledger[OXYGEN_KG] += oxidized * material[15]; ledger[CO2_KG] += co2;
        c[CO2] += co2; c[GAS_HEAT] += gasHeat;
    }
    uint32_t run(double dt, uint32_t& substeps) const {
        std::fill(cells, cells + uint64_t(cellCount) * cellStride, 0.);
        std::fill(scratch, scratch + uint64_t(layerCount) * workStride + boundaryCount, 0.);
        double* radiantFourth = scratch + uint64_t(layerCount) * workStride;
        for (uint32_t i = 0; i < layerCount; ++i) {
            double* l = layer(i); double* w = work(i); l[VOLATILE] = 0; w[CACHE_WOOD] = -1;
            w[OX_FACTOR] = std::pow(l[OXYGEN], material[59]); w[CHAR_FACTOR] = std::pow(l[OXYGEN], material[63]);
        }
        for (uint32_t i = 0; i < boundaryCount; ++i) {
            const double temperature = boundaries[i * boundaryStride + 5];
#ifdef PR_THERMAL_INTEGER_POWERS
            const double squared = temperature * temperature;
            radiantFourth[i] = squared * squared;
#else
            radiantFourth[i] = std::pow(temperature, 4.);
#endif
        }
        double remaining = dt; substeps = 0;
        while (remaining > dt * 1e-13) {
            if (++substeps > 200000) return BUDGET;
            double convectionW = 0, radiationW = 0, subDt = low(remaining, .05);
            for (uint32_t i = 0; i < layerCount; ++i) {
                double* l = layer(i); double* w = work(i); w[POWER] = 0; w[CONDUCTANCE] = 0;
                w[CAPACITY] = capacity(l, l[TEMPERATURE]); w[CONDUCTIVITY] = conductivity(l); w[OLD_T] = l[TEMPERATURE];
#ifdef PR_THERMAL_INTEGER_POWERS
                // Same integer powers, deliberately different f64 rounding
                // from libm pow. This opt-in candidate needs full-state and
                // conservation evidence; it never enables global fast math.
                const double squared = l[TEMPERATURE] * l[TEMPERATURE];
                w[CUBED] = squared * l[TEMPERATURE]; w[FOURTH] = squared * squared;
#else
                w[CUBED] = std::pow(l[TEMPERATURE], 3.); w[FOURTH] = std::pow(l[TEMPERATURE], 4.);
#endif
            }
            for (uint32_t i = 0; i < edgeCount; ++i) {
                const double* e = edges + i * edgeStride; double* a = work(uint32_t(e[0])); double* b = work(uint32_t(e[1]));
                const double g = e[2] / (e[3] / a[CONDUCTIVITY] + e[4] / b[CONDUCTIVITY] + e[5]);
                const double flow = g * (b[OLD_T] - a[OLD_T]);
                a[POWER] += flow; b[POWER] -= flow; a[CONDUCTANCE] += g; b[CONDUCTANCE] += g;
            }
            for (uint32_t i = 0; i < boundaryCount; ++i) {
                double* b = boundaries + i * boundaryStride; double* w = work(uint32_t(b[0])); const double t = layer(uint32_t(b[0]))[TEMPERATURE];
                const double h = b[3] > 0 ? 1 / (1 / b[3] + b[7] / w[CONDUCTIVITY]) : 0;
                const double convection = b[2] * h * (b[4] - t);
                const double radiation = b[2] * material[3] * (b[6] + material[1] * (radiantFourth[i] - w[FOURTH]));
                b[8] = convection + radiation; w[POWER] += convection + radiation; convectionW += convection; radiationW += radiation;
                w[CONDUCTANCE] += b[2] * (h + 4 * material[3] * material[1] * w[CUBED]);
            }
            for (uint32_t i = 0; i < layerCount; ++i) {
                double* l = layer(i); double* w = work(i); const double cp = w[CAPACITY];
                if (w[CONDUCTANCE] > 0) subDt = low(subDt, .2 * cp / w[CONDUCTANCE]);
                if (w[POWER] != 0) subDt = low(subDt, 20 * cp / std::abs(w[POWER]));
                const double charRate = reactionRate(l[CHAR], l[INITIAL] * material[10], material + 60, l[TEMPERATURE], w[CHAR_FACTOR]);
                if (!isFinite(charRate)) return NONFINITE;
                if (charRate > 0) subDt = low(subDt, 20 * cp / (charRate * material[13] * high(.001, material[4])));
            }
            if (!(subDt > 0) || !isFinite(subDt)) return NONFINITE;
            ledger[CONVECTION_J] += convectionW * subDt; ledger[RADIATION_J] += radiationW * subDt;
            for (uint32_t i = 0; i < boundaryCount; ++i) {
                const double* b = boundaries + i * boundaryStride;
                cell(layer(uint32_t(b[0])))[FACE_HEAT + uint32_t(b[1])] += b[8] * subDt;
            }
            for (uint32_t i = 0; i < layerCount; ++i) {
                double* l = layer(i); double* w = work(i); l[ENERGY] += w[POWER] * subDt;
                l[TEMPERATURE] = temperatureAt(l, w); react(l, w, subDt); l[TEMPERATURE] = temperatureAt(l, w);
                if (!(l[TEMPERATURE] > 0) || !isFinite(l[TEMPERATURE])) return NONFINITE;
            }
            remaining -= subDt;
        }
        for (uint32_t i = 0; i < 13; ++i) if (!isFinite(ledger[i])) return NONFINITE;
        for (uint64_t i = 0; i < uint64_t(cellCount) * cellStride; ++i) if (!isFinite(cells[i])) return NONFINITE;
        return OK;
    }
};

uint32_t scratchBytes(uint32_t layers, uint32_t boundaries) {
    const uint64_t bytes = (uint64_t(layers) * workStride + boundaries) * sizeof(double);
    return layers && bytes <= uint64_t(INT32_MAX) ? uint32_t(bytes) : 0;
}

uint32_t validate(Kernel& k) {
    const double* m = k.material;
    for (uint32_t i = 0; i < 64; ++i) if (!isFinite(m[i])) return DOMAIN;
    if (!(m[0] > 0) || m[1] < 0 || !(m[2] > 0) || m[3] < 0 || m[3] > 1 || m[4] < 0 || m[4] > 1
        || !(m[5] > 0) || !(m[6] > m[2]) || !(m[7] > 0) || m[8] < 0 || m[9] < 0
        || !(m[10] > 0) || m[10] > 1 || m[11] < 0 || m[11] > 1) return DOMAIN;
    for (uint32_t i = 12; i < 17; ++i) if (m[i] < 0) return DOMAIN;
    if (m[17] != m[2]) return DOMAIN;
    for (uint32_t i = 18; i < 22; ++i) if (!(m[i] > m[i - 1])) return DOMAIN;
    for (uint32_t i = 22; i < 52; ++i) if (!(m[i] > 0)) return DOMAIN;
    for (uint32_t i = 52; i < 64; i += 4)
        if (m[i] < 0 || m[i + 1] < 0 || !(m[i + 2] > 0) || !(m[i + 2] < 1) || m[i + 3] < 0) return DOMAIN;
    if (m[55] != 0) return DOMAIN;
    for (uint32_t i = 0; i < k.layerCount; ++i) {
        const double* l = k.layer(i);
        for (uint32_t j = 0; j < layerStride; ++j) if (!isFinite(l[j])) return DOMAIN;
        if (!index(l[CELL], k.cellCount) || !(l[INITIAL] > 0) || l[WOOD] < 0 || l[CHAR] < 0 || l[ASH] < 0 || l[WATER] < 0
            || !(l[WOOD] + l[CHAR] + l[ASH] > 0) || !(l[TEMPERATURE] > 0) || l[OXYGEN] < 0 || l[OXYGEN] > 1
            || l[10] != 0 || l[11] != 0) return DOMAIN;
    }
    for (uint32_t i = 0; i < k.edgeCount; ++i) {
        const double* e = k.edges + i * edgeStride;
        for (uint32_t j = 0; j < edgeStride; ++j) if (!isFinite(e[j])) return DOMAIN;
        if (!index(e[0], k.layerCount) || !index(e[1], k.layerCount) || e[0] == e[1]
            || !(e[2] > 0) || !(e[3] > 0) || !(e[4] > 0) || e[5] < 0) return DOMAIN;
    }
    for (uint32_t i = 0; i < k.boundaryCount; ++i) {
        const double* b = k.boundaries + i * boundaryStride;
        for (uint32_t j = 0; j < boundaryStride; ++j) if (!isFinite(b[j])) return DOMAIN;
        if (!index(b[0], k.layerCount) || !index(b[1], 6) || b[2] < 0 || b[3] < 0 || !(b[4] > 0) || !(b[5] > 0) || b[6] < 0 || b[7] < 0) return DOMAIN;
    }
    for (uint32_t i = 0; i < 13; ++i) if (!isFinite(k.ledger[i])) return DOMAIN;
    return OK;
}
}

extern "C" {
PR_EXPORT uint32_t pr_wood_thermal_abi() { return abi; }
PR_EXPORT uint32_t pr_wood_thermal_scratch_bytes(uint32_t layerCount, uint32_t boundaryCount) { return scratchBytes(layerCount, boundaryCount); }
PR_EXPORT uint32_t pr_wood_thermal_step(void* arena, uint32_t byteLength, double dt) {
    const uintptr_t address = reinterpret_cast<uintptr_t>(arena);
    if (!arena || (address & 7) || byteLength < headerBytes) return SPAN;
#ifdef __EMSCRIPTEN__
    if (uint64_t(address) + byteLength > emscripten_get_heap_size()) return SPAN;
#endif
    const uint32_t* h = static_cast<const uint32_t*>(arena);
    if (h[0] != magic || h[1] != abi || h[2] != byteLength || !h[3] || !h[6]) return HEADER;
    for (uint32_t i = 16; i < 24; ++i) if (h[i] != 0) return HEADER;
    if (!isFinite(dt) || !(dt > 0) || dt > 60) return TIMESTEP;
    const uint32_t scratchSize = scratchBytes(h[3], h[5]);
    if (!scratchSize || h[14] != scratchSize) return SPAN;
    const uint64_t sizes[] = {uint64_t(h[3]) * layerStride * 8, uint64_t(h[4]) * edgeStride * 8,
        uint64_t(h[5]) * boundaryStride * 8, uint64_t(h[6]) * cellStride * 8, 13 * 8, 64 * 8, scratchSize};
    for (uint32_t i = 0; i < 7; ++i) {
        const uint64_t begin = h[7 + i], end = begin + sizes[i];
        if (begin < headerBytes || (begin & 7) || end > byteLength) return SPAN;
        for (uint32_t j = 0; j < i; ++j)
            if (sizes[i] && sizes[j] && begin < uint64_t(h[7 + j]) + sizes[j] && h[7 + j] < end) return SPAN;
    }
    auto region = [arena, h](uint32_t offset) { return reinterpret_cast<double*>(static_cast<uint8_t*>(arena) + h[offset]); };
    Kernel k{h[3], h[4], h[5], h[6], region(7), region(8), region(9), region(10), region(11), region(12), region(13)};
    const uint32_t valid = validate(k); if (valid != OK) return valid;
    uint32_t substeps = 0; const uint32_t result = k.run(dt, substeps);
    static_cast<uint32_t*>(arena)[15] = substeps;
    return result;
}
}
