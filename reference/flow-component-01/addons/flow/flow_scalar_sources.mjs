// SPDX-License-Identifier: MIT
/** Own and validate ABI1 additive volume sources before any native mutation. */
export function packScalarSources(records, layers) {
    if (!Array.isArray(records) || records.length > 4096) throw new RangeError('Flow accepts at most4096 additive sources');
    const packet = new ArrayBuffer(records.length * 80), words = new Uint32Array(packet), floats = new Float32Array(packet);
    const ids = new Set(), sourceLayers = new Map();
    const allowed = new Set(['id', 'layer', 'type', 'enabled', 'position', 'quaternion', 'halfSize', 'totalRates']);
    const vector = (value, length, label) => {
        if (!value || value.length !== length || typeof value.every !== 'function') throw new RangeError(`Invalid scalar source ${label}`);
        return Array.from(value, item => {
            if (!Number.isFinite(item) || !Number.isFinite(Math.fround(item))) throw new RangeError(`Invalid scalar source ${label}`);
            return Math.fround(item);
        });
    };
    records.forEach((record, index) => {
        if (!record || typeof record !== 'object' || Object.keys(record).some(key => !allowed.has(key)))
            throw new RangeError('Invalid scalar source record');
        const { id, layer = 0, type = 'box', enabled = true, position = [0, 0, 0], quaternion = [0, 0, 0, 1], halfSize, totalRates } = record;
        if (!Number.isInteger(id) || id <= 0 || id > 0xffffffff || ids.has(id)) throw new RangeError('Scalar source IDs must be unique nonzero u32 values');
        if (!Number.isInteger(layer) || !layers.has(layer) || type !== 'box' || typeof enabled !== 'boolean') throw new RangeError('Invalid scalar source layer, type or enabled state');
        const p = vector(position, 3, 'position'), q = vector(quaternion, 4, 'quaternion');
        const h = vector(halfSize, 3, 'half size'), rate = vector(totalRates, 4, 'integrated rates');
        const norm = Math.hypot(...q);
        if (!(norm > 0) || h.some(value => value <= 0) || rate.slice(1).some(value => value < 0))
            throw new RangeError('Only temperature rates may be signed; box dimensions and quaternion norm must be positive');
        if (!h.every((value, axis) => Number.isFinite(Math.fround(Math.abs(p[axis]) + Math.hypot(...h)))))
            throw new RangeError('Scalar source world extent exceeds f32 geometry');
        const offset = index * 20;
        words.set([id, layer, Number(enabled), 0], offset);
        floats.set(p, offset + 4); floats.set(q.map(value => value / norm), offset + 8);
        floats.set(h, offset + 12); floats.set(rate, offset + 16);
        ids.add(id); sourceLayers.set(id, layer);
    });
    return { packet, sourceLayers };
}
