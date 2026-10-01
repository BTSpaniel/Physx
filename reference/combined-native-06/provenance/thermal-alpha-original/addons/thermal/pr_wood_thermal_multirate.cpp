// SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) <https://github.com/BTSpaniel>
// SPDX-License-Identifier: LicenseRef-ParticleRealms-Alpha
// Isolated numerical ABI2. Reuse the unchanged material/reaction implementation;
// the ABI1 entry points remain separate and are never called by this solver.
#include "pr_wood_thermal.cpp"
#include <cstring>

namespace mr {
constexpr uint32_t version = 9, configCount = 16, diagnosticCount = 32, stateCount = 4;
enum Config { RELATIVE, TEMPERATURE_ABS, MASS_ABS_FRACTION, ENERGY_ABS_K, NONLINEAR_FRACTION,
    LINEAR_FRACTION, LOCAL_BUDGET, MACRO_BUDGET, NONLINEAR_BUDGET, LINEAR_BUDGET, LOCAL_ERROR_FRACTION };
enum Result : uint32_t { SUCCESS = 0, BAD_HEADER = 1, BAD_SPAN = 2, BAD_DOMAIN = 3, BAD_DT = 4,
    EXHAUSTED = 5, INVALID_STATE = 6, NO_CONVERGENCE = 7, CONSERVATION = 8 };
enum Diagnostic { NUMERICS, REQUESTED_DT, ADVANCED_DT, MACRO_ACCEPTED, MACRO_REJECTED,
    LOCAL_ACCEPTED, LOCAL_REJECTED, NONLINEAR_ITERATIONS, LINEAR_ITERATIONS, MAX_LOCAL_ERROR,
    MAX_MACRO_ERROR, MAX_TRANSPORT_RESIDUAL, PEAK_T, PEAK_LAYER, PEAK_TIME, MIN_LOCAL_DT,
    MIN_MACRO_DT, INITIAL_MASS, FINAL_MASS, MASS_RESIDUAL, ENERGY_RESIDUAL, LAYER_EVALUATIONS,
    EDGE_EVALUATIONS, BOUNDARY_EVALUATIONS, LOCAL_ERROR_SUM, INTERNAL_PEAK_T, INTERNAL_PEAK_LAYER, INTERNAL_PEAK_TIME,
    SUBCYCLED_TRIALS, SUBCYCLED_ACCEPTED };
bool positiveFinite(double value) { return value > 0 && isFinite(value); }
bool constitutive(const Kernel& input, const double* layer) {
    const double cp = input.capacity(layer, layer[TEMPERATURE]);
    const double reconstructed = input.energyAt(layer, layer[TEMPERATURE]);
    constexpr double roundoff = 64 * std::numeric_limits<double>::epsilon();
    const double tolerance = cp * (1e-8 + roundoff * std::abs(layer[TEMPERATURE]))
        + roundoff * high(std::abs(layer[ENERGY]), std::abs(reconstructed));
    return positiveFinite(cp) && isFinite(reconstructed) && positiveFinite(tolerance)
        && std::abs(reconstructed - layer[ENERGY]) <= tolerance;
}

struct State {
    double *layers, *ledgers, *cells, *edgeHeat, *peaks;
    uint64_t doubles;
    double* layer(uint32_t i) const { return layers + uint64_t(i) * 12; }
    double* ledger(uint32_t i) const { return ledgers + uint64_t(i) * 13; }
    double* cell(uint32_t i) const { return cells + uint64_t(i) * 12; }
    void copy(const State& source) const { std::memcpy(layers, source.layers, doubles * 8); }
};
struct Cursor {
    uint8_t* pointer;
    double* f64(uint64_t count) { auto* result = reinterpret_cast<double*>(pointer); pointer += count * 8; return result; }
    uint32_t* u32(uint64_t count) { auto* result = reinterpret_cast<uint32_t*>(pointer); pointer += ((count * 4 + 7) & ~uint64_t(7)); return result; }
};
uint32_t workspaceBytes(uint32_t layers, uint32_t edges, uint32_t boundaries, uint32_t cells) {
    if (!layers || !cells) return 0;
    const uint64_t stateDoubles = uint64_t(layers) * 39 + edges;
    const uint64_t bytes = stateCount * stateDoubles * 8 + (uint64_t(layers) * 32 + uint64_t(edges) * 4 + uint64_t(cells) * 12 + boundaries + 64) * 8
        + ((uint64_t(layers + uint64_t(1)) * 4 + 7) & ~uint64_t(7))
        + ((uint64_t(boundaries) * 4 + 7) & ~uint64_t(7)) + ((uint64_t(layers) * 4 + 7) & ~uint64_t(7));
    return bytes <= uint64_t(INT32_MAX) ? uint32_t(bytes) : 0;
}
struct Packet {
    Kernel input{};
    double *config = nullptr, *diagnostics = nullptr;
    uint32_t requiredWorkspace = 0;
    uint32_t admit(void* arena, uint32_t bytes, void* workspace, uint32_t workspaceSize, double dt) {
        const uintptr_t address = reinterpret_cast<uintptr_t>(arena), scratchAddress = reinterpret_cast<uintptr_t>(workspace);
        if (!arena || !workspace || (address & 7) || (scratchAddress & 7) || bytes < 96) return BAD_SPAN;
        const uint64_t end = uint64_t(address) + bytes, scratchEnd = uint64_t(scratchAddress) + workspaceSize;
#ifdef __EMSCRIPTEN__
        if (end > emscripten_get_heap_size() || scratchEnd > emscripten_get_heap_size()) return BAD_SPAN;
#endif
        if (uint64_t(address) < scratchEnd && uint64_t(scratchAddress) < end) return BAD_SPAN;
        const auto* h = static_cast<const uint32_t*>(arena);
        if (h[0] != magic || h[1] != 2 || h[2] != bytes || !h[3] || !h[6] || h[15] != version) return BAD_HEADER;
        for (uint32_t i = 16; i < 24; ++i) if (h[i]) return BAD_HEADER;
        if (!(dt > 0) || dt > 60 || !isFinite(dt)) return BAD_DT;
        requiredWorkspace = workspaceBytes(h[3], h[4], h[5], h[6]);
        if (!requiredWorkspace || workspaceSize != requiredWorkspace) return BAD_SPAN;
        const uint64_t sizes[] = {uint64_t(h[3]) * 12 * 8, uint64_t(h[4]) * 6 * 8, uint64_t(h[5]) * 9 * 8,
            uint64_t(h[6]) * 12 * 8, 13 * 8, 64 * 8, configCount * 8, diagnosticCount * 8};
        for (uint32_t i = 0; i < 8; ++i) {
            const uint64_t start = h[7 + i], finish = start + sizes[i];
            if (start < 96 || (start & 7) || finish > bytes) return BAD_SPAN;
            for (uint32_t j = 0; j < i; ++j)
                if (sizes[i] && sizes[j] && start < uint64_t(h[7 + j]) + sizes[j] && h[7 + j] < finish) return BAD_SPAN;
        }
        auto region = [arena, h](uint32_t i) { return reinterpret_cast<double*>(static_cast<uint8_t*>(arena) + h[i]); };
        input = Kernel{h[3], h[4], h[5], h[6], region(7), region(8), region(9), region(10), region(11), region(12), nullptr};
        config = region(13); diagnostics = region(14);
        for (uint32_t i = 0; i < configCount; ++i) if (!isFinite(config[i])) return BAD_DOMAIN;
        for (uint32_t i = 0; i < 6; ++i) if (!(config[i] > 0)) return BAD_DOMAIN;
        if (config[RELATIVE] > .1 || config[NONLINEAR_FRACTION] > .25 || config[LINEAR_FRACTION] > config[NONLINEAR_FRACTION]) return BAD_DOMAIN;
        for (uint32_t i = 6; i < 10; ++i) if (!index(config[i] - 1, 1000000000u)) return BAD_DOMAIN;
        if (!(config[LOCAL_ERROR_FRACTION] > 0) || config[LOCAL_ERROR_FRACTION] > 1) return BAD_DOMAIN;
        for (uint32_t i = 11; i < configCount; ++i) if (config[i] != 0) return BAD_DOMAIN;
        const uint32_t valid = validate(input); if (valid != SUCCESS) return valid;
        for (uint32_t i = 0; i < input.layerCount; ++i) {
            const double* l = input.layer(i); const double cp = input.capacity(l, l[TEMPERATURE]);
            if (!constitutive(input, l)) return BAD_DOMAIN;
            // Admit both the outer controls and the tighter local allocation;
            // a finite but underflowed scale must not enter retry loops.
            const double fractions[] = {1, config[LOCAL_ERROR_FRACTION]};
            for (const double fraction : fractions) {
                const double relative = config[RELATIVE] * fraction, temperature = config[TEMPERATURE_ABS] * fraction;
                const double mass = config[MASS_ABS_FRACTION] * fraction, energy = config[ENERGY_ABS_K] * fraction;
                if (!positiveFinite(relative) || !positiveFinite(temperature) || !positiveFinite(mass) || !positiveFinite(energy)
                    || !positiveFinite(l[INITIAL] * mass)
                    || !positiveFinite(temperature + relative * l[TEMPERATURE])
                    || !positiveFinite(cp * energy + relative * high(std::abs(l[ENERGY]), cp * input.material[2]))) return BAD_DOMAIN;
                for (uint32_t j = WOOD; j <= WATER; ++j)
                    if (!positiveFinite(l[INITIAL] * mass + relative * l[j])) return BAD_DOMAIN;
            }
            // Forward reconstruction alone cannot detect overflow in the
            // quadratic inverse. Exercise the actual inverse before trials,
            // so shrinking the timestep never retries an invalid input state.
            double inverse[12], inverseWork[24]{};
            std::copy(l, l + 12, inverse); inverseWork[CACHE_WOOD] = -1;
            inverse[TEMPERATURE] = input.temperatureAt(inverse, inverseWork);
            if (!positiveFinite(inverse[TEMPERATURE]) || !constitutive(input, inverse)
                || std::abs(inverse[TEMPERATURE] - l[TEMPERATURE]) > 1e-8 + 64 * std::numeric_limits<double>::epsilon() * l[TEMPERATURE]) return BAD_DOMAIN;
        }
        return SUCCESS;
    }
};

struct Local {
    double layer[12], cell[12], ledger[13], work[24], extents[3]{}, edgeEnergy = 0, peak, peakTime;
    Kernel kernel(const Kernel& input) { return Kernel{1, 0, 0, 1, layer, nullptr, nullptr, cell, ledger, input.material, work, extents}; }
    void load(const State& state, uint32_t i, const Kernel& input) {
        std::memcpy(layer, state.layer(i), sizeof(layer)); std::memcpy(cell, state.cell(i), sizeof(cell));
        std::memcpy(ledger, state.ledger(i), sizeof(ledger)); std::fill(work, work + 24, 0.);
        layer[CELL] = 0; work[CACHE_WOOD] = -1;
        work[OX_FACTOR] = std::pow(layer[OXYGEN], input.material[59]); work[CHAR_FACTOR] = std::pow(layer[OXYGEN], input.material[63]);
        peak = state.peaks[i * 2]; peakTime = state.peaks[i * 2 + 1];
    }
    void save(const State& state, uint32_t i, double cellId) const {
        std::memcpy(state.layer(i), layer, sizeof(layer)); state.layer(i)[CELL] = cellId;
        std::memcpy(state.cell(i), cell, sizeof(cell)); std::memcpy(state.ledger(i), ledger, sizeof(ledger));
        state.peaks[i * 2] = peak; state.peaks[i * 2 + 1] = peakTime;
    }
};

struct Solver {
    Packet& packet;
    Kernel& input;
    const double* config;
    double localConfig[configCount];
    State states[stateCount];
    double *vectors, *edgeWork, *outputCells, *outputBoundaryHeat, *metrics;
    uint32_t *boundaryStarts, *boundaryIds, *boundaryCursor;
    uint64_t localTrials = 0, macroTrials = 0;
    bool exhausted = false;
    bool subcycledMacro = false;
    const double* forcedPower = nullptr;
    Solver(Packet& p, void* workspace) : packet(p), input(p.input), config(p.config) {
        std::copy(config, config + configCount, localConfig);
        for (uint32_t i = 0; i < 4; ++i) localConfig[i] *= config[LOCAL_ERROR_FRACTION];
        Cursor c{static_cast<uint8_t*>(workspace)};
        for (auto& state : states) {
            state.doubles = uint64_t(input.layerCount) * 39 + input.edgeCount;
            state.layers = c.f64(uint64_t(input.layerCount) * 12);
            state.ledgers = c.f64(uint64_t(input.layerCount) * 13);
            state.cells = c.f64(uint64_t(input.layerCount) * 12);
            state.edgeHeat = c.f64(input.edgeCount); state.peaks = c.f64(uint64_t(input.layerCount) * 2);
        }
        vectors = c.f64(uint64_t(input.layerCount) * 32); edgeWork = c.f64(uint64_t(input.edgeCount) * 4);
        outputCells = c.f64(uint64_t(input.cellCount) * 12); outputBoundaryHeat = c.f64(input.boundaryCount); metrics = c.f64(64);
        boundaryStarts = c.u32(uint64_t(input.layerCount) + 1); boundaryIds = c.u32(input.boundaryCount); boundaryCursor = c.u32(input.layerCount);
        std::fill(metrics, metrics + 64, 0.); metrics[MIN_LOCAL_DT] = std::numeric_limits<double>::infinity(); metrics[MIN_MACRO_DT] = metrics[MIN_LOCAL_DT];
        std::fill(boundaryStarts, boundaryStarts + uint64_t(input.layerCount) + 1, 0u);
        for (uint32_t b = 0; b < input.boundaryCount; ++b) ++boundaryStarts[uint32_t(input.boundaries[b * 9]) + 1];
        for (uint32_t i = 1; i <= input.layerCount; ++i) boundaryStarts[i] += boundaryStarts[i - 1];
        std::copy(boundaryStarts, boundaryStarts + input.layerCount, boundaryCursor);
        for (uint32_t b = 0; b < input.boundaryCount; ++b) boundaryIds[boundaryCursor[uint32_t(input.boundaries[b * 9])]++] = b;
        const State& initial = states[0]; std::fill(initial.layers, initial.layers + initial.doubles, 0.);
        std::copy(input.layers, input.layers + uint64_t(input.layerCount) * 12, initial.layers);
        for (uint32_t i = 0; i < input.layerCount; ++i) { initial.layer(i)[VOLATILE] = 0; initial.peaks[i * 2] = initial.layer(i)[TEMPERATURE]; }
        sampleCoupledEndpoints(initial, 0);
    }
    double* v(uint32_t slot) const { return vectors + uint64_t(slot) * input.layerCount; }
    void count(uint32_t slot, double amount = 1) {
        constexpr double maximumExactInteger = 9007199254740991.;
        if (!(amount >= 0) || metrics[slot] > maximumExactInteger - amount) { exhausted = true; return; }
        metrics[slot] += amount;
    }
    double energyScale(const double* a, const double* b) const {
        const double cp = low(input.capacity(a, a[TEMPERATURE]), input.capacity(b, b[TEMPERATURE]));
        const double scale = cp * config[ENERGY_ABS_K] + config[RELATIVE] * high(high(std::abs(a[ENERGY]), std::abs(b[ENERGY])), cp * input.material[2]);
        return positiveFinite(cp) && positiveFinite(scale) ? scale : std::numeric_limits<double>::quiet_NaN();
    }
    double massScale(const double* start, double a, double b) const {
        const double scale = start[INITIAL] * config[MASS_ABS_FRACTION] + config[RELATIVE] * high(std::abs(a), std::abs(b));
        return positiveFinite(scale) ? scale : std::numeric_limits<double>::quiet_NaN();
    }
    double temperatureError(double a, double b) const {
        const double scale = config[TEMPERATURE_ABS] + config[RELATIVE] * high(std::abs(a), std::abs(b));
        return positiveFinite(scale) ? std::abs(a - b) / scale : std::numeric_limits<double>::quiet_NaN();
    }
    double error(const double* start, const double* a, const double* b, const double* ca, const double* cb,
            const double* la, const double* lb, const double* commonCell = nullptr,
            const double* commonLedger = nullptr) const {
        double result = temperatureError(a[TEMPERATURE], b[TEMPERATURE]);
        const double eScale = energyScale(a, b);
        result = high(result, std::abs(a[ENERGY] - b[ENERGY]) / eScale);
        for (uint32_t j = WOOD; j <= WATER; ++j) result = high(result, std::abs(a[j] - b[j]) / massScale(start, a[j], b[j]));
        // Exported products are additional per-layer ODE coordinates. Scale
        // their local error by that coordinate's accumulated in-call value,
        // not just the increment over h (which shrinks the denominator as h
        // shrinks). Neither old public ledger history nor other layers enter.
        for (uint32_t j = 0; j < 12; ++j) {
            const double common = commonCell ? commonCell[j] : 0;
            const double scale = j < 3 ? massScale(start, common + ca[j], common + cb[j]) : eScale;
            result = high(result, std::abs(ca[j] - cb[j]) / scale);
        }
        for (uint32_t j = 2; j < 13; ++j) {
            const double common = commonLedger ? commonLedger[j] : 0;
            const double scale = j < 6 ? massScale(start, common + la[j], common + lb[j]) : eScale;
            result = high(result, std::abs(la[j] - lb[j]) / scale);
        }
        return result;
    }
    bool physical(const Local& value) const {
        for (const double number : value.layer) if (!isFinite(number)) return false;
        for (const double number : value.cell) if (!isFinite(number)) return false;
        for (const double number : value.ledger) if (!isFinite(number)) return false;
        if (!(value.layer[TEMPERATURE] > 0) || !(value.layer[WOOD] + value.layer[CHAR] + value.layer[ASH] > 0)) return false;
        for (uint32_t j = WOOD; j <= WATER; ++j) if (value.layer[j] < 0) return false;
        return isFinite(value.edgeEnergy) && constitutive(input, value.layer);
    }
    bool localStep(Local& target, const Local& source, uint32_t i, double dt, double time) {
        target = source; auto kernel = target.kernel(input); double power = 0;
        const double t = source.layer[TEMPERATURE], k = input.conductivity(source.layer);
        for (uint32_t j = boundaryStarts[i]; j < boundaryStarts[i + 1]; ++j) {
            const double* b = input.boundaries + uint64_t(boundaryIds[j]) * 9;
            const double h = b[3] > 0 ? 1 / (1 / b[3] + b[7] / k) : 0;
            const double conv = b[2] * h * (b[4] - t);
            const double rad = b[2] * input.material[3] * (b[6] + input.material[1] * (std::pow(b[5], 4.) - std::pow(t, 4.)));
            power += conv + rad; target.ledger[CONVECTION_J] += conv * dt; target.ledger[RADIATION_J] += rad * dt;
            target.cell[FACE_HEAT + uint32_t(b[1])] += (conv + rad) * dt;
        }
        count(BOUNDARY_EVALUATIONS, boundaryStarts[i + 1] - boundaryStarts[i]);
        const double edgeHeat = forcedPower ? forcedPower[i] * dt : 0;
        if (!isFinite(edgeHeat)) return false;
        target.edgeEnergy += edgeHeat;
        target.layer[ENERGY] += power * dt + edgeHeat; target.layer[TEMPERATURE] = kernel.temperatureAt(target.layer, target.work);
        if (!(target.layer[TEMPERATURE] > 0) || !isFinite(target.layer[TEMPERATURE])) return false;
        kernel.react(target.layer, target.work, dt); target.layer[TEMPERATURE] = kernel.temperatureAt(target.layer, target.work);
        count(LAYER_EVALUATIONS);
        if (!physical(target)) return false;
        if (target.layer[TEMPERATURE] > target.peak) { target.peak = target.layer[TEMPERATURE]; target.peakTime = time + dt; }
        return true;
    }
    // Richardson extrapolation cancels the leading local splitting error.
    // Enthalpy and every species/product transfer use the same linear map;
    // temperature is reconstructed from those masses and enthalpy. A trial
    // outside the physical domain is rejected, never clipped or thresholded.
    bool extrapolate(Local& target, const Local& source, const Local& coarse, const Local& fine, double time) {
        target = fine;
        // Trial ledgers contain increments with a zero common origin. Rebuild
        // species and enthalpy from the same extrapolated reaction transfers,
        // instead of differencing already-rounded large absolute stores.
        for (uint32_t field = 0; field < 12; ++field)
            target.cell[field] = fine.cell[field] + (fine.cell[field] - coarse.cell[field]);
        for (uint32_t field = 2; field < 13; ++field)
            target.ledger[field] = fine.ledger[field] + (fine.ledger[field] - coarse.ledger[field]);
        for (uint32_t field = 0; field < 3; ++field)
            target.extents[field] = fine.extents[field] + (fine.extents[field] - coarse.extents[field]);
        target.edgeEnergy = fine.edgeEnergy + (fine.edgeEnergy - coarse.edgeEnergy);
        const double converted = target.extents[0];
        const double oxidized = target.extents[1];
        const double evaporated = target.extents[2];
        if (!isFinite(converted) || !isFinite(oxidized) || !isFinite(evaporated)
            || converted < 0 || oxidized < 0 || evaporated < 0) return false;
        target.layer[WOOD] = source.layer[WOOD] - converted;
        target.layer[CHAR] = source.layer[CHAR] + converted * input.material[10] - oxidized;
        target.layer[ASH] = source.layer[ASH] + oxidized * input.material[11];
        target.layer[WATER] = source.layer[WATER] - evaporated;
        target.layer[VOLATILE] = source.layer[VOLATILE] + target.ledger[VOLATILE_KG];
        target.layer[ENERGY] = source.layer[ENERGY] + target.ledger[CONVECTION_J] + target.ledger[RADIATION_J]
            + target.ledger[CHAR_J] - target.ledger[PYROLYSIS_J] - target.ledger[GAS_SENSIBLE_J]
            - target.ledger[GAS_REACTION_J] - target.ledger[STEAM_LATENT_J] + target.edgeEnergy;
        target.work[CACHE_WOOD] = -1;
        auto kernel = target.kernel(input);
        target.layer[TEMPERATURE] = kernel.temperatureAt(target.layer, target.work);
        if (!physical(target)) return false;
        if (target.layer[WOOD] > source.layer[WOOD] || target.layer[WATER] > source.layer[WATER]
            || target.layer[ASH] < source.layer[ASH] || target.layer[VOLATILE] < source.layer[VOLATILE]) return false;
        // Signed boundary/face heat and reversible char storage are excluded.
        // Positivity alone would permit a trial to undo exported products.
        for (uint32_t field = 0; field < 6; ++field)
            if (target.cell[field] < source.cell[field]) return false;
        for (const uint32_t field : {VOLATILE_KG, STEAM_KG, CO2_KG, OXYGEN_KG,
                PYROLYSIS_J, CHAR_J, GAS_SENSIBLE_J, GAS_REACTION_J, STEAM_LATENT_J})
            if (target.ledger[field] < source.ledger[field]) return false;
        if (target.layer[TEMPERATURE] > target.peak) {
            target.peak = target.layer[TEMPERATURE]; target.peakTime = time;
        }
        return true;
    }
    bool localStage(const State& state, double duration, double time) {
        for (uint32_t i = 0; i < input.layerCount; ++i) {
            Local current{}, coarse{}, half{}, fine{}, next{}, fourth{}, lower{}, higher{};
            current.load(state, i, input);
            const Local initial = current;
            double elapsed = 0, h = duration;
            while (elapsed < duration) {
                if (++localTrials > config[LOCAL_BUDGET]) { exhausted = true; return false; }
                h = low(h, duration - elapsed); if (!(h * .25 > 0) || elapsed + h == elapsed) return false;
                Local trialSource = current;
                std::fill(trialSource.cell, trialSource.cell + 12, 0.);
                std::fill(trialSource.ledger, trialSource.ledger + 13, 0.);
                std::fill(trialSource.extents, trialSource.extents + 3, 0.);
                trialSource.edgeEnergy = 0;
                bool valid = localStep(coarse, trialSource, i, h, time + elapsed)
                    && localStep(half, trialSource, i, .5 * h, time + elapsed)
                    && localStep(fine, half, i, .5 * h, time + elapsed + .5 * h);
                fourth = trialSource;
                for (uint32_t part = 0; valid && part < 4; ++part) {
                    valid = localStep(next, fourth, i, .25 * h, time + elapsed + .25 * h * part);
                    fourth = next;
                }
                const bool extrapolated = valid && extrapolate(lower, trialSource, coarse, fine, time + elapsed + h)
                    && extrapolate(higher, trialSource, fine, fourth, time + elapsed + h);
                // At a kink, extrapolated positive transfers can turn negative.
                // Use the actual four-substep trajectory with its first-order
                // step-doubling estimate. No store or transfer is clipped.
                const bool subcycled = valid && !extrapolated;
                double e = std::numeric_limits<double>::infinity();
                if (extrapolated) {
                    e = error(current.layer, lower.layer, higher.layer, lower.cell, higher.cell,
                        lower.ledger, higher.ledger, current.cell, current.ledger);
                } else if (subcycled) {
                    lower = fine; higher = fourth;
                    e = high(error(current.layer, coarse.layer, fine.layer, coarse.cell, fine.cell,
                            coarse.ledger, fine.ledger, current.cell, current.ledger),
                        error(current.layer, fine.layer, fourth.layer, fine.cell, fourth.cell,
                            fine.ledger, fourth.ledger, current.cell, current.ledger));
                    count(SUBCYCLED_TRIALS);
                }
                if (!valid || !isFinite(e) || e > 1) {
                    count(LOCAL_REJECTED);
                    h *= isFinite(e) ? high(.1, low(.5, .8 / (subcycled ? std::sqrt(e) : std::cbrt(e)))) : .5;
                    continue;
                }
                for (uint32_t field = 0; field < 12; ++field) higher.cell[field] += current.cell[field];
                for (uint32_t field = 0; field < 13; ++field) higher.ledger[field] += current.ledger[field];
                higher.edgeEnergy += current.edgeEnergy;
                if (!physical(higher)) return false;
                if (subcycled) { count(SUBCYCLED_ACCEPTED); subcycledMacro = true; }
                current = higher; elapsed = h == duration - elapsed ? duration : elapsed + h;
                count(LOCAL_ACCEPTED); metrics[MAX_LOCAL_ERROR] = high(metrics[MAX_LOCAL_ERROR], e);
                metrics[LOCAL_ERROR_SUM] += e; metrics[MIN_LOCAL_DT] = low(metrics[MIN_LOCAL_DT], .25 * h);
                h *= e > 0 ? high(.2, low(2., .9 / (subcycled ? std::sqrt(e) : std::cbrt(e)))) : 2.;
            }
            // Each layer integrates exactly the same constant signed edge
            // forcing whose paired integral is retained in State.edgeHeat.
            // Verify closure; never repair energy by adding a residual.
            const double edgeHeat = forcedPower ? forcedPower[i] * duration : 0;
            const double edgeTolerance = 1e-12 + 128 * std::numeric_limits<double>::epsilon()
                * high(std::abs(edgeHeat), std::abs(current.edgeEnergy));
            const auto delta = [&](uint32_t j) { return current.ledger[j] - initial.ledger[j]; };
            const double expected = initial.layer[ENERGY] + delta(CONVECTION_J) + delta(RADIATION_J) + delta(CHAR_J)
                - delta(PYROLYSIS_J) - delta(GAS_SENSIBLE_J) - delta(GAS_REACTION_J) - delta(STEAM_LATENT_J) + edgeHeat;
            const double budget = std::abs(initial.layer[ENERGY]) + std::abs(delta(CONVECTION_J))
                + std::abs(delta(RADIATION_J)) + std::abs(delta(CHAR_J)) + std::abs(edgeHeat);
            const double tolerance = 1e-7 + 2e-12 * budget;
            if (!isFinite(expected) || !isFinite(budget) || !positiveFinite(tolerance)
                || !positiveFinite(edgeTolerance) || std::abs(current.edgeEnergy - edgeHeat) > edgeTolerance
                || std::abs(expected - current.layer[ENERGY]) > tolerance) return false;
            current.save(state, i, input.layer(i)[CELL]);
        }
        return true;
    }
    // Evaluate the nonlinear backward-Euler enthalpy residual. A positive
    // property capacity plus a symmetric edge Laplacian defines the PCG matrix.
    double transportResidual(const State& state, double h, const double* temperature, double* residual, double* conductance,
            double* cp, double* diagonal, double* scales) {
        auto* k = v(18);
        for (uint32_t i = 0; i < input.layerCount; ++i) {
            double l[12]; std::copy(state.layer(i), state.layer(i) + 12, l); l[TEMPERATURE] = temperature[i];
            if (!(temperature[i] > 0) || !isFinite(temperature[i])) return std::numeric_limits<double>::infinity();
            l[ENERGY] = input.energyAt(l, temperature[i]); residual[i] = l[ENERGY] - state.layer(i)[ENERGY];
            cp[i] = input.capacity(l, temperature[i]); k[i] = input.conductivity(l); diagonal[i] = cp[i]; scales[i] = energyScale(state.layer(i), l);
            if (!positiveFinite(cp[i]) || !positiveFinite(k[i]) || !positiveFinite(scales[i]) || !isFinite(residual[i])) return std::numeric_limits<double>::infinity();
        }
        count(LAYER_EVALUATIONS, input.layerCount);
        for (uint32_t i = 0; i < input.edgeCount; ++i) {
            const double* e = input.edges + uint64_t(i) * 6; const uint32_t a = uint32_t(e[0]), b = uint32_t(e[1]);
            const double g = e[2] / (e[3] / k[a] + e[4] / k[b] + e[5]); conductance[i] = g;
            const double q = h * g * (temperature[b] - temperature[a]);
            if (!(g > 0) || !isFinite(g) || !isFinite(q)) return std::numeric_limits<double>::infinity();
            residual[a] -= q; residual[b] += q; diagonal[a] += h * g; diagonal[b] += h * g;
        }
        count(EDGE_EVALUATIONS, input.edgeCount);
        double result = 0;
        for (uint32_t i = 0; i < input.layerCount; ++i) result = high(result, std::abs(residual[i]) / scales[i]);
        return result;
    }
    void multiply(double h, const double* x, const double* cp, const double* g, double* out) {
        for (uint32_t i = 0; i < input.layerCount; ++i) out[i] = cp[i] * x[i];
        for (uint32_t i = 0; i < input.edgeCount; ++i) {
            const double* e = input.edges + uint64_t(i) * 6; const uint32_t a = uint32_t(e[0]), b = uint32_t(e[1]);
            const double q = h * g[i] * (x[a] - x[b]); out[a] += q; out[b] -= q;
        }
        count(EDGE_EVALUATIONS, input.edgeCount);
    }
    bool linear(double h, const double* residual, const double* cp, const double* diagonal, const double* scales, const double* g, double* delta) {
        double *r = v(7), *z = v(8), *p = v(9), *ap = v(10); double rz = 0;
        for (uint32_t i = 0; i < input.layerCount; ++i) {
            if (!(diagonal[i] > 0) || !isFinite(diagonal[i])) return false;
            delta[i] = 0; r[i] = -residual[i]; z[i] = r[i] / diagonal[i]; p[i] = z[i]; rz += r[i] * z[i];
        }
        for (uint32_t iteration = 0; iteration < config[LINEAR_BUDGET]; ++iteration) {
            double norm = 0; for (uint32_t i = 0; i < input.layerCount; ++i) norm = high(norm, std::abs(r[i]) / scales[i]);
            if (norm <= config[LINEAR_FRACTION]) return true;
            multiply(h, p, cp, g, ap); double denominator = 0;
            for (uint32_t i = 0; i < input.layerCount; ++i) denominator += p[i] * ap[i];
            if (!(denominator > 0) || !isFinite(denominator) || !isFinite(rz)) return false;
            const double alpha = rz / denominator; double next = 0;
            for (uint32_t i = 0; i < input.layerCount; ++i) { delta[i] += alpha * p[i]; r[i] -= alpha * ap[i]; z[i] = r[i] / diagonal[i]; next += r[i] * z[i]; }
            const double beta = next / rz;
            for (uint32_t i = 0; i < input.layerCount; ++i) p[i] = z[i] + beta * p[i];
            rz = next; count(LINEAR_ITERATIONS);
        }
        double norm = 0; for (uint32_t i = 0; i < input.layerCount; ++i) norm = high(norm, std::abs(r[i]) / scales[i]);
        return norm <= config[LINEAR_FRACTION];
    }
    bool transportStage(const State& state, double h, double time, double edgeWeight) {
        if (!input.edgeCount) return true;
        double *t = v(0), *r = v(1), *cp = v(2), *diagonal = v(3), *scales = v(4), *delta = v(5), *trial = v(6);
        double *rt = v(11), *ct = v(12), *dt = v(13), *st = v(14), *newEnergy = v(15), *newT = v(16);
        double *g = edgeWork, *gt = edgeWork + input.edgeCount, *q = edgeWork + uint64_t(input.edgeCount) * 2;
        for (uint32_t i = 0; i < input.layerCount; ++i) t[i] = state.layer(i)[TEMPERATURE];
        double norm = transportResidual(state, h, t, r, g, cp, diagonal, scales);
        for (uint32_t iteration = 0; iteration < config[NONLINEAR_BUDGET]; ++iteration) {
            count(NONLINEAR_ITERATIONS);
            if (!isFinite(norm)) return false;
            if (norm <= config[NONLINEAR_FRACTION]) {
                for (uint32_t i = 0; i < input.layerCount; ++i) newEnergy[i] = state.layer(i)[ENERGY];
                for (uint32_t i = 0; i < input.edgeCount; ++i) {
                    const double* e = input.edges + uint64_t(i) * 6; const uint32_t a = uint32_t(e[0]), b = uint32_t(e[1]);
                    q[i] = h * g[i] * (t[b] - t[a]); newEnergy[a] += q[i]; newEnergy[b] -= q[i];
                }
                for (uint32_t i = 0; i < input.layerCount; ++i) {
                    double l[12], w[24]{}; std::copy(state.layer(i), state.layer(i) + 12, l); l[ENERGY] = newEnergy[i]; w[CACHE_WOOD] = -1;
                    newT[i] = input.temperatureAt(l, w);
                    l[TEMPERATURE] = newT[i]; if (!constitutive(input, l)) return false;
                }
                const double conservativeNorm = transportResidual(state, h, newT, rt, gt, ct, dt, st);
                if (isFinite(conservativeNorm) && conservativeNorm <= config[NONLINEAR_FRACTION]) {
                    for (uint32_t i = 0; i < input.layerCount; ++i) {
                        state.layer(i)[ENERGY] = newEnergy[i]; state.layer(i)[TEMPERATURE] = newT[i];
                        if (newT[i] > state.peaks[i * 2]) { state.peaks[i * 2] = newT[i]; state.peaks[i * 2 + 1] = time; }
                    }
                    for (uint32_t i = 0; i < input.edgeCount; ++i) state.edgeHeat[i] += edgeWeight * q[i];
                    metrics[MAX_TRANSPORT_RESIDUAL] = high(metrics[MAX_TRANSPORT_RESIDUAL], conservativeNorm); return true;
                }
                if (isFinite(conservativeNorm)) {
                    std::copy(newT, newT + input.layerCount, t); std::copy(rt, rt + input.layerCount, r);
                    std::copy(ct, ct + input.layerCount, cp); std::copy(dt, dt + input.layerCount, diagonal);
                    std::copy(st, st + input.layerCount, scales); std::copy(gt, gt + input.edgeCount, g); norm = conservativeNorm;
                }
            }
            if (!linear(h, r, cp, diagonal, scales, g, delta)) return false;
            bool accepted = false; double alpha = 1;
            for (uint32_t backtrack = 0; backtrack < 24; ++backtrack) {
                for (uint32_t i = 0; i < input.layerCount; ++i) trial[i] = t[i] + alpha * delta[i];
                const double candidate = transportResidual(state, h, trial, rt, gt, ct, dt, st);
                if (isFinite(candidate) && (candidate < norm * (1 - .0001 * alpha) || candidate <= config[NONLINEAR_FRACTION])) {
                    std::copy(trial, trial + input.layerCount, t); std::copy(rt, rt + input.layerCount, r);
                    std::copy(ct, ct + input.layerCount, cp); std::copy(dt, dt + input.layerCount, diagonal);
                    std::copy(st, st + input.layerCount, scales); std::copy(gt, gt + input.edgeCount, g);
                    norm = candidate; accepted = true; break;
                }
                alpha *= .5;
            }
            if (!accepted) return false;
        }
        return false;
    }
    // Two-stage L-stable SDIRK transport. The unchanged implicit stage owns
    // symmetric edge fluxes; both stages contribute to the same paired ledger.
    // Species/reaction/boundary evolution and all acceptance budgets stay fixed.
    bool transport(const State& state, double h, double time) {
        if (!input.edgeCount) return true;
        const double gamma = 1 - 1 / std::sqrt(2.);
        const double weight = (1 - gamma) / gamma;
        double* initialEnergy = v(21);
        double* firstFlux = edgeWork + uint64_t(input.edgeCount) * 3;
        for (uint32_t i = 0; i < input.layerCount; ++i) initialEnergy[i] = state.layer(i)[ENERGY];
        if (!transportStage(state, gamma * h, time, weight)) return false;
        std::copy(edgeWork + uint64_t(input.edgeCount) * 2,
            edgeWork + uint64_t(input.edgeCount) * 3, firstFlux);
        // This affine stage right-hand side is not an intermediate physical
        // enthalpy. Its temperature is the first stage's positive predictor;
        // only the converged second-stage state may reach the caller.
        for (uint32_t i = 0; i < input.layerCount; ++i) state.layer(i)[ENERGY] = initialEnergy[i];
        for (uint32_t e = 0; e < input.edgeCount; ++e) {
            const uint32_t a = uint32_t(input.edges[e * 6]), b = uint32_t(input.edges[e * 6 + 1]);
            const double heat = weight * firstFlux[e];
            state.layer(a)[ENERGY] += heat; state.layer(b)[ENERGY] -= heat;
        }
        return transportStage(state, gamma * h, time, 1);
    }
// SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) <https://github.com/BTSpaniel>
// SPDX-License-Identifier: LicenseRef-ParticleRealms-Alpha
    // MRI-GARK-ERK22a, Sandu2019 Example2.8. Physical stage durations H/2
    // turn its scaled-time coefficients into Q0 and 2*Qmid-Q0 forcing.
    // Only the conductive RHS is slow; each local fast IVP includes its
    // constant paired edge power throughout reaction and boundary evolution.
    bool edgePower(const State& state, double* power) {
        auto* conductivity = v(18);
        for (uint32_t i = 0; i < input.layerCount; ++i) {
            conductivity[i] = input.conductivity(state.layer(i));
            if (!positiveFinite(conductivity[i])) return false;
        }
        for (uint32_t e = 0; e < input.edgeCount; ++e) {
            const double* edge = input.edges + uint64_t(e) * 6;
            const uint32_t a = uint32_t(edge[0]), b = uint32_t(edge[1]);
            const double g = edge[2] / (edge[3] / conductivity[a] + edge[4] / conductivity[b] + edge[5]);
            power[e] = g * (state.layer(b)[TEMPERATURE] - state.layer(a)[TEMPERATURE]);
            if (!positiveFinite(g) || !isFinite(power[e])) return false;
        }
        count(LAYER_EVALUATIONS, input.layerCount); count(EDGE_EVALUATIONS, input.edgeCount);
        return !exhausted;
    }
    bool forcedStage(const State& state, const double* power, double duration, double time) {
        double* incoming = v(22);
        std::fill(incoming, incoming + input.layerCount, 0.);
        for (uint32_t e = 0; e < input.edgeCount; ++e) {
            const double* edge = input.edges + uint64_t(e) * 6;
            incoming[uint32_t(edge[0])] += power[e]; incoming[uint32_t(edge[1])] -= power[e];
        }
        for (uint32_t i = 0; i < input.layerCount; ++i) if (!isFinite(incoming[i])) return false;
        forcedPower = incoming;
        const double* outer = config; config = localConfig;
        const bool valid = localStage(state, duration, time);
        config = outer; forcedPower = nullptr;
        if (!valid) return false;
        for (uint32_t e = 0; e < input.edgeCount; ++e) {
            const double heat = duration * power[e];
            const double total = state.edgeHeat[e] + heat;
            if (!isFinite(heat) || !isFinite(total)) return false;
            state.edgeHeat[e] = total;
        }
        return true;
    }
    bool macro(const State& target, const State& source, double h, double time) {
        if (!(h * .5 > 0)) return false;
        target.copy(source);
        double* firstPower = edgeWork;
        double* secondPower = edgeWork + input.edgeCount;
        if (!edgePower(source, firstPower) || !forcedStage(target, firstPower, h * .5, time)
            || !edgePower(target, secondPower)) return false;
        for (uint32_t e = 0; e < input.edgeCount; ++e) {
            secondPower[e] = 2 * secondPower[e] - firstPower[e];
            if (!isFinite(secondPower[e])) return false;
        }
        return forcedStage(target, secondPower, h * .5, time + h * .5);
    }
    double macroError(const State& start, const State& a, const State& b) {
        double result = 0; auto* edgeError = v(20); std::fill(edgeError, edgeError + input.layerCount, 0.);
        for (uint32_t e = 0; e < input.edgeCount; ++e) {
            const double delta = std::abs(a.edgeHeat[e] - b.edgeHeat[e]);
            edgeError[uint32_t(input.edges[e * 6])] += delta; edgeError[uint32_t(input.edges[e * 6 + 1])] += delta;
        }
        for (uint32_t i = 0; i < input.layerCount; ++i) {
            result = high(result, error(start.layer(i), a.layer(i), b.layer(i), a.cell(i), b.cell(i), a.ledger(i), b.ledger(i)));
            // Subflow stage maxima are not matched-time coupled states. They
            // remain separate diagnostics; only composed endpoint/transfer
            // errors control this macro interval.
            result = high(result, edgeError[i] / energyScale(a.layer(i), b.layer(i)));
        }
        return result;
    }
#ifdef PR_THERMAL_MR_DIAGNOSTICS
    // Diagnostic-only observer. It does not replace the production reduction,
    // change admission, or write public output. Recomputed attribution must
    // match the original macroError exactly; mismatches are counted explicitly.
    void diagnoseMacro(const State& start, const State& a, const State& b, double original, double h, double time) {
        double maximum = -1, difference = 0, denominator = 1;
        uint32_t kind = 0, layer = 0, component = 0;
        auto observe = [&](uint32_t k, uint32_t i, uint32_t j, double absolute, double scale) {
            const double value = absolute / scale;
            if (value > maximum) { maximum = value; difference = absolute; denominator = scale; kind = k; layer = i; component = j; }
        };
        const double* edgeError = v(20); // Produced by the unchanged macroError.
        for (uint32_t i = 0; i < input.layerCount; ++i) {
            const double *s = start.layer(i), *la = a.layer(i), *lb = b.layer(i);
            const double eScale = energyScale(la, lb);
            observe(0, i, 0, std::abs(la[TEMPERATURE] - lb[TEMPERATURE]),
                config[TEMPERATURE_ABS] + config[RELATIVE] * high(std::abs(la[TEMPERATURE]), std::abs(lb[TEMPERATURE])));
            observe(1, i, ENERGY, std::abs(la[ENERGY] - lb[ENERGY]), eScale);
            for (uint32_t j = WOOD; j <= WATER; ++j)
                observe(2, i, j, std::abs(la[j] - lb[j]), massScale(s, la[j], lb[j]));
            for (uint32_t j = 0; j < 12; ++j)
                observe(j < 3 ? 3 : 5, i, 100 + j, std::abs(a.cell(i)[j] - b.cell(i)[j]),
                    j < 3 ? massScale(s, a.cell(i)[j], b.cell(i)[j]) : eScale);
            for (uint32_t j = 2; j < 13; ++j)
                observe(j < 6 ? 4 : 5, i, 200 + j, std::abs(a.ledger(i)[j] - b.ledger(i)[j]),
                    j < 6 ? massScale(s, a.ledger(i)[j], b.ledger(i)[j]) : eScale);
            observe(6, i, 0, edgeError[i], eScale);
        }
        metrics[32] = 1; // Diagnostic schema, independent of numerical version.
        if (maximum != original) ++metrics[33];
        const bool accepted = isFinite(original) && original <= 1;
        double* record = metrics + (accepted ? 34 : 42);
        record[0] = kind; record[1] = layer; record[2] = component; record[3] = difference;
        record[4] = denominator; record[5] = maximum; record[6] = h; record[7] = time + h;
        ++metrics[(accepted ? 50 : 57) + kind];
    }
#endif
    void sampleCoupledEndpoints(const State& state, double time) {
        for (uint32_t i = 0; i < input.layerCount; ++i) {
            const double temperature = state.layer(i)[TEMPERATURE];
            if (temperature > metrics[PEAK_T]) {
                metrics[PEAK_T] = temperature; metrics[PEAK_LAYER] = i; metrics[PEAK_TIME] = time;
            }
        }
    }
    uint32_t run(double duration) {
        metrics[NUMERICS] = version; metrics[REQUESTED_DT] = duration;
        double elapsed = 0, h = duration;
        while (elapsed < duration) {
            if (++macroTrials > config[MACRO_BUDGET]) return EXHAUSTED;
            h = low(h, duration - elapsed); if (!(h > 0) || elapsed + h == elapsed) return NO_CONVERGENCE;
            subcycledMacro = false;
            const bool valid = macro(states[1], states[0], h, elapsed) && macro(states[3], states[0], h * .5, elapsed)
                && macro(states[2], states[3], h * .5, elapsed + h * .5);
            if (exhausted) return EXHAUSTED;
            const double e = valid ? macroError(states[0], states[1], states[2]) : std::numeric_limits<double>::infinity();
#ifdef PR_THERMAL_MR_DIAGNOSTICS
            if (valid) diagnoseMacro(states[0], states[1], states[2], e, h, elapsed);
#endif
            if (!valid || !isFinite(e) || e > 1) {
                count(MACRO_REJECTED);
                h *= isFinite(e) ? high(.1, low(.5, .8 / (subcycledMacro ? std::sqrt(e) : std::cbrt(e)))) : .5;
                continue;
            }
            // Only the accepted fine trajectory owns coupled physical samples.
            // Neither rejected/coarse work nor local/conduction subflow states
            // contribute to this endpoint peak.
            sampleCoupledEndpoints(states[3], elapsed + .5 * h);
            sampleCoupledEndpoints(states[2], h == duration - elapsed ? duration : elapsed + h);
            states[0].copy(states[2]); elapsed = h == duration - elapsed ? duration : elapsed + h;
            count(MACRO_ACCEPTED); metrics[MAX_MACRO_ERROR] = high(metrics[MAX_MACRO_ERROR], e); metrics[MIN_MACRO_DT] = low(metrics[MIN_MACRO_DT], .5 * h);
            h *= e > 0 ? high(.2, low(2., .9 / (subcycledMacro ? std::sqrt(e) : std::cbrt(e)))) : 2.;
        }
        metrics[ADVANCED_DT] = duration;
        return finish();
    }
    uint32_t finish() {
        const State& state = states[0]; double initialMass = 0, finalMass = 0, initialEnergy = 0, finalEnergy = 0, increments[13]{};
        for (uint32_t i = 0; i < input.layerCount; ++i) {
            const double* before = input.layer(i); const double* after = state.layer(i);
            for (uint32_t j = WOOD; j <= WATER; ++j) { initialMass += before[j]; finalMass += after[j]; }
            initialEnergy += before[ENERGY]; finalEnergy += after[ENERGY];
            for (uint32_t j = 0; j < 13; ++j) increments[j] += state.ledger(i)[j];
            if (state.peaks[i * 2] > metrics[INTERNAL_PEAK_T]) {
                metrics[INTERNAL_PEAK_T] = state.peaks[i * 2]; metrics[INTERNAL_PEAK_LAYER] = i; metrics[INTERNAL_PEAK_TIME] = state.peaks[i * 2 + 1];
            }
        }
        metrics[INITIAL_MASS] = initialMass; metrics[FINAL_MASS] = finalMass;
        metrics[MASS_RESIDUAL] = initialMass + increments[OXYGEN_KG] - finalMass - increments[VOLATILE_KG] - increments[STEAM_KG] - increments[CO2_KG];
        const double expected = initialEnergy + increments[CONVECTION_J] + increments[RADIATION_J] + increments[CHAR_J]
            - increments[PYROLYSIS_J] - increments[GAS_SENSIBLE_J] - increments[GAS_REACTION_J] - increments[STEAM_LATENT_J];
        metrics[ENERGY_RESIDUAL] = expected - finalEnergy;
        const double energyBudget = std::abs(initialEnergy) + std::abs(increments[CONVECTION_J]) + std::abs(increments[RADIATION_J]) + increments[CHAR_J];
        const double massTolerance = 1e-12 + initialMass * 1e-10, energyTolerance = 1e-7 + energyBudget * 2e-12;
        if (!isFinite(energyBudget) || !positiveFinite(massTolerance) || !positiveFinite(energyTolerance)) return INVALID_STATE;
        for (uint32_t i = 0; i < diagnosticCount; ++i) if (!isFinite(metrics[i])) return INVALID_STATE;
        if (std::abs(metrics[MASS_RESIDUAL]) > massTolerance || std::abs(metrics[ENERGY_RESIDUAL]) > energyTolerance) return CONSERVATION;
        // Stage complete public outputs before committing any byte. Even a
        // finite input can overflow while cells or final boundary heat sum.
        std::fill(outputCells, outputCells + uint64_t(input.cellCount) * 12, 0.);
        for (uint32_t i = 0; i < input.layerCount; ++i) {
            double* target = outputCells + uint64_t(uint32_t(state.layer(i)[CELL])) * 12;
            for (uint32_t j = 0; j < 12; ++j) target[j] += state.cell(i)[j];
        }
        double finalLedger[13];
        for (uint32_t j = 0; j < 13; ++j) { finalLedger[j] = input.ledger[j] + increments[j]; if (!isFinite(finalLedger[j])) return INVALID_STATE; }
        for (uint64_t j = 0; j < uint64_t(input.cellCount) * 12; ++j) if (!isFinite(outputCells[j])) return INVALID_STATE;
        for (uint32_t i = 0; i < input.boundaryCount; ++i) {
            const double* b = input.boundaries + uint64_t(i) * 9; const double* l = state.layer(uint32_t(b[0]));
            const double h = b[3] > 0 ? 1 / (1 / b[3] + b[7] / input.conductivity(l)) : 0;
            outputBoundaryHeat[i] = b[2] * h * (b[4] - l[TEMPERATURE]) + b[2] * input.material[3]
                * (b[6] + input.material[1] * (std::pow(b[5], 4.) - std::pow(l[TEMPERATURE], 4.)));
            if (!isFinite(outputBoundaryHeat[i])) return INVALID_STATE;
        }
        // Source geometry, material, configuration and public header are never
        // modified. There are no fallible operations after this commit starts.
        std::copy(state.layers, state.layers + uint64_t(input.layerCount) * 12, input.layers);
        std::copy(outputCells, outputCells + uint64_t(input.cellCount) * 12, input.cells);
        std::copy(finalLedger, finalLedger + 13, input.ledger);
        for (uint32_t i = 0; i < input.boundaryCount; ++i) input.boundaries[uint64_t(i) * 9 + 8] = outputBoundaryHeat[i];
        std::copy(metrics, metrics + diagnosticCount, packet.diagnostics);
        return SUCCESS;
    }
};
}

extern "C" {
PR_EXPORT uint32_t pr_wood_thermal_mr_abi() { return 2; }
PR_EXPORT uint32_t pr_wood_thermal_mr_numerics() { return mr::version; }
PR_EXPORT uint32_t pr_wood_thermal_mr_workspace_bytes(uint32_t layers, uint32_t edges, uint32_t boundaries, uint32_t cells) {
    return mr::workspaceBytes(layers, edges, boundaries, cells);
}
PR_EXPORT uint32_t pr_wood_thermal_mr_step(void* arena, uint32_t bytes, void* workspace, uint32_t workspaceSize, double dt) {
    mr::Packet packet; const uint32_t admitted = packet.admit(arena, bytes, workspace, workspaceSize, dt);
    if (admitted != mr::SUCCESS) return admitted;
    mr::Solver solver(packet, workspace); return solver.run(dt);
}
}
