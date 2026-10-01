// SPDX-License-Identifier: MIT
import { FlowHostWebGpu } from './flow_host_webgpu.mjs';
import { FlowHostWebGpu as PreviousHost } from '/baseline-flow.mjs';

const check = (condition, message) => { if (!condition) throw new Error(message); };
const shaders = '/dist/flow-wgsl';
function ownedMemory(host) {
    const buffers = new Set(), textures = new Set(), descriptors = new Map();
    let bufferBytes = 0, textureBytes = 0, pooledTextureBytes = 0;
    const records = [
        ...[...host.resources.values(), ...host.retired, ...(host.uploadChunks ?? []), ...(host.uploadPool ?? [])].map(value => [value, false]),
        ...[...(host.texturePool?.values() ?? [])].flat().map(value => [value, true]),
        ...[...(host.zeroTextures?.values() ?? [])].map(value => [value, false]),
    ];
    for (const [resource, pooled] of records) {
        if (resource.buffer && !buffers.has(resource.buffer)) {
            buffers.add(resource.buffer); bufferBytes += resource.buffer.size;
        }
        const texture = resource.texture;
        if (!texture || textures.has(texture)) continue;
        textures.add(texture);
        const size = [texture.width, texture.height, texture.depthOrArrayLayers];
        const texelBytes = texture.format.startsWith('rgba32') ? 16 : texture.format.startsWith('rg32') ? 8 : 4;
        let bytes = 0;
        for (let level = 0; level < texture.mipLevelCount; ++level) {
            const extent = size.map((value, axis) => axis === 2 && texture.dimension !== '3d' ? value : Math.max(1, value >> level));
            bytes += extent[0] * extent[1] * extent[2] * texelBytes;
        }
        textureBytes += bytes; if (pooled) pooledTextureBytes += bytes;
        const key = JSON.stringify([texture.format, texture.dimension, ...size, texture.mipLevelCount]);
        const row = descriptors.get(key) ?? { format: texture.format, dimension: texture.dimension, size,
            mips: texture.mipLevelCount, bytesEach: bytes, count: 0, pooledCount: 0 };
        ++row.count; if (pooled) ++row.pooledCount; descriptors.set(key, row);
    }
    check(bufferBytes + textureBytes === host.stats.allocatedBytes, 'Allocation ledger disagrees with actual GPU buffer/texture descriptors');
    if ('pooledTextureBytes' in host.stats) check(pooledTextureBytes === host.stats.pooledTextureBytes, 'Texture pool ledger disagrees with actual GPU descriptors');
    return { allocatedBytes: bufferBytes + textureBytes, bufferBytes, textureBytes, pooledTextureBytes,
        buffers: buffers.size, textures: textures.size, descriptors: [...descriptors.values()].sort((a, b) => b.bytesEach - a.bytesEach) };
}
async function snapshot(device, output, key, mipLevel = 0) {
    const resource = output[key];
    const extent = key === 'sparse' ? null : resource.size.map((value, axis) =>
        axis === 2 && resource.dimension !== '3d' ? value : Math.max(1, value >> mipLevel));
    const bufferSize = key === 'sparse' ? resource.size : Math.ceil(extent[0] * 16 / 256) * 256 * extent[1] * extent[2];
    const read = device.createBuffer({ size: bufferSize, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ });
    try {
        const encoder = device.createCommandEncoder();
        if (key === 'sparse') encoder.copyBufferToBuffer(resource.buffer, 0, read, 0, bufferSize);
        else encoder.copyTextureToBuffer({ texture: resource.texture, mipLevel }, { buffer: read,
            bytesPerRow: Math.ceil(extent[0] * 16 / 256) * 256, rowsPerImage: extent[1] }, extent);
        device.queue.submit([encoder.finish()]); await read.mapAsync(GPUMapMode.READ);
        return read.getMappedRange().slice(0);
    } finally { if (read.mapState === 'mapped') read.unmap(); read.destroy(); }
}

export async function verifyUploadStaging(module, device, { differential = true } = {}) {
    const cases = [], baselineContexts = module._pr_flow_host_live();
    const host = await FlowHostWebGpu.create(module, device, shaders, { maxBlocks: 16 });
    const pointer = module._malloc(70000);
    let growthPointer = 0, read, result;
    try {
        const uniform = host.call(1, 16, 1, 1, 0), large = host.call(1, 70000, 0, 1, 0);
        result = device.createBuffer({ size: 16, usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC });
        read = device.createBuffer({ size: 16, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ });
        const shader = device.createShaderModule({ code: `
            @group(0) @binding(0) var<uniform> input:vec4u;
            @group(0) @binding(1) var<storage,read_write> output:array<u32>;
            @compute @workgroup_size(1) fn main(){output[input.x]=input.y;}` });
        const pipeline = await device.createComputePipelineAsync({ layout: 'auto', compute: { module: shader, entryPoint: 'main' } });
        const group = device.createBindGroup({ layout: pipeline.getBindGroupLayout(0), entries: [
            { binding: 0, resource: { buffer: host._get(uniform).buffer } }, { binding: 1, resource: { buffer: result } },
        ] });
        const dispatch = (index, value) => {
            module.HEAPU32.set([index, value, 0, 0], pointer / 4); host.call(3, uniform, pointer, 16);
            const pass = host._encoder().beginComputePass(); pass.setPipeline(pipeline); pass.setBindGroup(0, group); pass.dispatchWorkgroups(1); pass.end();
        };
        dispatch(0, 11); dispatch(1, 29);
        module.HEAPU8.fill(123, pointer, pointer + 70000); host.call(3, large, pointer, 70000);
        dispatch(2, 43);
        const previousHeap = module.HEAPU8.buffer, oldHeapBytes = previousHeap.byteLength;
        growthPointer = module._malloc(oldHeapBytes + 65536);
        check(growthPointer && module.HEAPU8.buffer !== previousHeap, 'Fixture did not exercise actual WASM memory growth');
        module.HEAPU8.fill(255, pointer, pointer + 70000); dispatch(3, 71);
        module.HEAPU8.fill(255, pointer, pointer + 16);
        host._encoder().copyBufferToBuffer(result, 0, read, 0, 16);
        host._flush(); await read.mapAsync(GPUMapMode.READ);
        const values = Array.from(new Uint32Array(read.getMappedRange())); read.unmap();
        check(values.join(',') === '11,29,43,71', `Per-dispatch upload versions changed: ${values}`);
        const largeBytes = new Uint8Array(await snapshot(device, { sparse: host._get(large) }, 'sparse'));
        check(largeBytes.length === 70000 && largeBytes.every(value => value === 123), 'Oversize chunk did not preserve its source snapshot');
        check(host.stats.queueSubmissions === 1 && host.stats.uploadChunks >= 3, 'Uploads were not batched across rollover');
        host._flush(); check(host.stats.queueSubmissions === 1, 'Empty flush submitted unnecessary work');
        host.call(3, uniform, pointer, 0);
        host._flush(); check(host.stats.queueSubmissions === 1, 'Zero-length upload submitted unnecessary work');
        cases.push({ name: 'ordered-same-buffer-and-rollover', status: 'PASS', values, submissions: host.stats.queueSubmissions, chunks: host.stats.uploadChunks });
        cases.push({ name: 'source-snapshot-survives-heap-growth', status: 'PASS', oldHeapBytes, newHeapBytes: module.HEAPU8.byteLength });
        cases.push({ name: 'empty-flush-and-zero-length-upload', status: 'PASS' });
        cases.push({ name: 'oversize-chunk-snapshot', status: 'PASS', bytes: largeBytes.length });
    } finally {
        read?.destroy(); result?.destroy(); if (growthPointer) module._free(growthPointer); module._free(pointer); await host.dispose();
    }
    check(host.stats.allocatedBytes === 0 && !host.uploadChunks.length && !host.retired.length, 'Ordered upload resources leaked');

    for (const invalidGpuCommand of [false, true]) {
        const failed = await FlowHostWebGpu.create(module, device, shaders, { maxBlocks: 16 });
        const data = module._malloc(16);
        try {
            const destination = failed.call(1, 16, 0, 1, 0);
            module.HEAPU8.fill(7, data, data + 16); failed.call(3, destination, data, 16);
            if (invalidGpuCommand) {
                device.pushErrorScope('validation');
                failed._encoder().copyBufferToBuffer(failed._get(destination).buffer, 0, failed._get(destination).buffer, 0, 16);
                failed._flush(); await device.queue.onSubmittedWorkDone();
                check(await device.popErrorScope(), 'Real invalid GPU command did not report validation failure');
            } else {
                for (const [pointer, size] of [[data, 20], [data, 3], [-1, 4], [module.HEAPU8.length, 4]]) {
                    let rejected = false;
                    try { failed.call(3, destination, pointer, size); } catch { rejected = true; }
                    check(rejected, `Invalid upload was accepted: ${pointer}, ${size}`);
                }
                // Disposing with an open mapped staging chunk must flush/unmap
                // and retire it even though no native step was ever requested.
            }
        } finally { module._free(data); await failed.dispose(); }
        check(failed.stats.allocatedBytes === 0 && !failed.uploadChunks.length && !failed.retired.length, 'Failure cleanup leaked upload staging');
        cases.push({ name: invalidGpuCommand ? 'gpu-validation-disposal' : 'upload-rejection-disposal', status: 'PASS' });
    }

    for (const method of ['submit', 'onSubmittedWorkDone']) {
        const failed = await FlowHostWebGpu.create(module, device, shaders, { maxBlocks: 16 });
        const data = module._malloc(16), original = device.queue[method], sentinel = new Error('Injected queue failure');
        let reported;
        try {
            const destination = failed.call(1, 16, 0, 1, 0);
            module.HEAPU8.fill(19, data, data + 16); failed.call(3, destination, data, 16);
            device.queue[method] = method === 'submit' ? () => { throw sentinel; } : () => Promise.reject(sentinel);
            try { await failed.dispose(); } catch (error) { reported = error; }
        } finally {
            device.queue[method] = original;
            await device.queue.onSubmittedWorkDone(); module._free(data);
        }
        check(reported === sentinel && failed.handle === 0 && !module.prFlowContexts.has(failed.id),
            'Queue failure lost its error or retained native ownership');
        check(failed.stats.allocatedBytes === 0 && !failed.uploadChunks.length && !failed.retired.length && !failed.resources.size,
            'Queue failure leaked staging or native GPU resources');
        await failed.dispose();
        cases.push({ name: 'fault-injection-' + method + '-disposal', status: 'PASS', injection: 'JS queue method rejection; actual native/GPU allocations' });
    }

    if (host.texturePool) {
        for (const type of [1, 2]) for (const format of [1, 2]) {
            const pooled = await FlowHostWebGpu.create(module, device, shaders, { maxBlocks: 16 });
            // Creation owns a temporary upload chunk as well as native buffers.
            // Establish the baseline after those queued uploads and both cache
            // generations retire; the fixture measures only its own additions.
            pooled._flush(); await device.queue.onSubmittedWorkDone(); pooled._retire(); pooled._retire();
            const descriptor = module._malloc(28), initialBytes = pooled.stats.allocatedBytes;
            const allocate = (width = 8) => {
                module.HEAPU32.set([type, 0, format, width, 4, type === 1 ? 3 : 4, 3], descriptor / 4);
                return pooled.call(4, descriptor);
            };
            const poison = resource => {
                pooled._materializeTexture(resource);
                for (let level = 0; level < 3; ++level) {
                    const extent = resource.size.map((value, axis) => axis === 2 && type === 1 ? value : Math.max(1, value >> level));
                    const values = new Uint32Array(extent[0] * extent[1] * extent[2] * 4);
                    for (let layer = 0; layer < extent[2]; ++layer)
                        values.fill(format === 1 ? 0x3f800000 + layer : layer + level + 1,
                            layer * extent[0] * extent[1] * 4, (layer + 1) * extent[0] * extent[1] * 4);
                    device.queue.writeTexture({ texture: resource.texture, mipLevel: level }, values,
                        { bytesPerRow: extent[0] * 16, rowsPerImage: extent[1] }, extent);
                }
            };
            let zeroBytes = 0;
            try {
                const first = allocate(), firstResource = pooled._get(first); poison(firstResource);
                pooled.call(2, first);
                const second = allocate(), secondResource = pooled._get(second); poison(secondResource);
                check(firstResource.texture !== secondResource.texture && pooled.stats.textureReuses === 0,
                    'Texture was reused before completed retirement');
                for (const resource of [firstResource, secondResource]) for (let level = 0; level < 3; ++level)
                    check(new Uint8Array(await snapshot(device, { density: resource }, 'density', level)).some(value => value !== 0), 'Poison fixture was empty');
                pooled.call(2, second); await device.queue.onSubmittedWorkDone(); pooled._retire();
                const third = allocate(), fourth = allocate(), leased = [pooled._get(third), pooled._get(fourth)];
                check(leased.every(resource => resource.texture === null) && [...pooled.texturePool.values()].flat().length === 2,
                    'Unused logical descriptors claimed physical cache leases');
                leased.forEach(resource => pooled._materializeTexture(resource));
                check(third !== first && fourth !== second && third !== fourth, 'Texture reuse retained a logical resource ID');
                check(new Set(leased.map(resource => resource.texture)).size === 2 && leased.every(resource =>
                    [firstResource.texture, secondResource.texture].includes(resource.texture)), 'Exact descriptors did not reuse their physical textures');
                const mismatch = allocate(16), mismatchResource = pooled._get(mismatch);
                pooled._materializeTexture(mismatchResource);
                check(!leased.some(resource => resource.texture === mismatchResource.texture), 'Mismatched descriptor reused a texture');
                pooled._flush(); await device.queue.onSubmittedWorkDone();
                for (const resource of leased) for (let level = 0; level < 3; ++level) {
                    const bytes = new Uint8Array(await snapshot(device, { density: resource }, 'density', level));
                    check(bytes.every(value => value === 0), `Reused texture was not fully reset: type${type}, format${format}, mip${level}`);
                    zeroBytes += bytes.length;
                }
                for (const id of [third, fourth, mismatch]) pooled.call(2, id);
                await device.queue.onSubmittedWorkDone(); pooled._retire();
                const next = allocate(); pooled._materializeTexture(pooled._get(next)); pooled.call(2, next); pooled._flush();
                await device.queue.onSubmittedWorkDone(); pooled._retire();
                check([...pooled.texturePool.values()].reduce((sum, entries) => sum + entries.length, 0) === 1,
                    'Unused older texture generation was retained');
                pooled._retire();
                check(!pooled.texturePool.size && !pooled.zeroTextures?.size && pooled.stats.allocatedBytes === initialBytes,
                    `Unused texture pools or zero templates were not retired/accounted: ${JSON.stringify({
                        initialBytes, allocatedBytes: pooled.stats.allocatedBytes,
                        poolKeys: [...pooled.texturePool.keys()], zeroKeys: [...(pooled.zeroTextures?.keys() ?? [])],
                        resources: [...pooled.resources.values()].map(resource => ({ kind: resource.kind, size: resource.size, bytes: resource.bytes })),
                        retired: pooled.retired.length,
                    })}`);
            } finally { module._free(descriptor); await pooled.dispose(); }
            check(pooled.stats.allocatedBytes === 0 && !pooled.texturePool.size && !pooled.zeroTextures?.size && !pooled.clearPipelines?.size,
                'Texture pool disposal leaked resources');
            cases.push({ name: `texture-pool-${type === 1 ? '2d-array' : '3d'}-${format === 1 ? 'float' : 'uint'}`,
                status: 'PASS', mips: 3, zeroBytes, textureReuses: pooled.stats.textureReuses,
                coverage: 'all poisoned mips/layers; fresh IDs; no early reuse; descriptor mismatch; generation and zero-template eviction' });
        }
        const failed = await FlowHostWebGpu.create(module, device, shaders, { maxBlocks: 16 });
        const descriptor = module._malloc(28), originalEncoder = failed._encoder, sentinel = new Error('Injected encoder failure');
        let reported;
        try {
            module.HEAPU32.set([2, 0, 1, 4, 4, 4, 1], descriptor / 4);
            const id = failed.call(4, descriptor); failed._materializeTexture(failed._get(id)); failed.call(2, id);
            await device.queue.onSubmittedWorkDone(); failed._retire();
            failed._encoder = () => { throw sentinel; };
            const reused = failed.call(4, descriptor);
            try { failed._materializeTexture(failed._get(reused)); } catch (error) { reported = error; }
            check(reported === sentinel && failed._get(reused).texture && !failed.retired.some(resource => resource.texture),
                'Failed reset dropped or duplicated ownership of its popped texture lease');
        } finally { failed._encoder = originalEncoder; module._free(descriptor); await failed.dispose(); }
        check(failed.stats.allocatedBytes === 0 && !failed.texturePool.size && !failed.zeroTextures?.size && !failed.clearPipelines?.size,
            'Failed texture reset leaked resources');
        cases.push({ name: 'fault-injection-texture-reset-disposal', status: 'PASS', injection: 'JS encoder rejection after real texture lease' });

        if ('pooledTextureBytes' in host.stats) {
            const bounded = await FlowHostWebGpu.create(module, device, shaders, { maxBlocks: 16 });
            bounded._flush(); await device.queue.onSubmittedWorkDone(); bounded._retire(); bounded._retire();
            const descriptor = module._malloc(28), initialBytes = bounded.stats.allocatedBytes;
            const allocate = (width, height, depth) => {
                module.HEAPU32.set([2, 0, 1, width, height, depth, 1], descriptor / 4);
                return bounded.call(4, descriptor);
            };
            let cached;
            try {
                for (const extent of [[256, 256, 64], [1, 1024, 2048]]) {
                    const first = allocate(...extent), texture = bounded._materializeTexture(bounded._get(first));
                    bounded.call(2, first); await device.queue.onSubmittedWorkDone(); bounded._retire();
                    check(!bounded.texturePool.size && bounded.stats.allocatedBytes === initialBytes,
                        'Oversized scratch or padded initialization was cached instead of following the original retirement path');
                    const second = allocate(...extent);
                    bounded._materializeTexture(bounded._get(second));
                    check(bounded._get(second).texture !== texture && bounded.stats.textureResets === 0, 'Unsafe descriptor was reused or unnecessarily reset');
                    bounded.call(2, second); await device.queue.onSubmittedWorkDone(); bounded._retire();
                }
                for (let index = 0; index < 5; ++index) {
                    const id = allocate(128, 128, 128); bounded._materializeTexture(bounded._get(id)); bounded.call(2, id);
                }
                await device.queue.onSubmittedWorkDone(); bounded._retire(); cached = ownedMemory(bounded);
                check(cached.pooledTextureBytes === 128 * 1024 * 1024 && cached.textures === 4,
                    'Aggregate texture cache exceeded its allocation budget');
                bounded._retire();
                check(bounded.stats.allocatedBytes === initialBytes && bounded.stats.pooledTextureBytes === 0, 'Bounded cache did not release the unused generation');
            } finally { module._free(descriptor); await bounded.dispose(); }
            check(bounded.stats.allocatedBytes === 0 && !bounded.texturePool.size && !bounded.clearPipelines.size, 'Bounded cache disposal leaked resources');
            cases.push({ name: 'texture-cache-budget-preserves-oversized-native-allocations', status: 'PASS',
                cacheBytes: cached.pooledTextureBytes, cachedTextures: cached.textures, oversizedTextureBytes: 64 * 1024 * 1024,
                narrowTextureBytes: 32 * 1024 * 1024, narrowAlignedInitializationBytes: 512 * 1024 * 1024,
                deviceMaxBufferSize: device.limits.maxBufferSize,
                scope: 'cache policy only; native allocations remain admitted and oversized scratch is never reset' });
        }
    }

    if (typeof host._materializeTexture === 'function') {
        const lazy = await FlowHostWebGpu.create(module, device, shaders, { maxBlocks: 16 });
        lazy._flush(); await device.queue.onSubmittedWorkDone(); lazy._retire(); lazy._retire();
        const pointer = module._malloc(256), initialBytes = lazy.stats.allocatedBytes;
        const createTexture = device.createTexture;
        let creations = 0;
        device.createTexture = function (...args) { ++creations; return createTexture.apply(this, args); };
        const allocate = (size = [1, 1, 1]) => {
            module.HEAPU32.set([2, 0, 1, ...size, 1], pointer / 4);
            return lazy.call(4, pointer);
        };
        const copy = (op, source, destination) => {
            module.HEAPU32.set(op === 11 ? [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1]
                : [0, 256, 256, 0, 0, 0, 0, 1, 1, 1], pointer / 4);
            lazy.call(op, source, destination, pointer);
        };
        let copyValues, bindingValues;
        try {
            const unused = [[512, 256, 256], [256, 128, 128], [256, 128, 128]].map(allocate);
            check(creations === 0 && lazy.stats.allocatedBytes === initialBytes && unused.every(id =>
                lazy._get(id).texture === null && lazy._get(id).bytes === 0), 'Unused scratch allocated physical GPU textures');
            lazy.call(7, 0, 0, 0, 0, 1, 1);
            lazy.call(2, unused[0]); lazy._retire();
            check(creations === 0 && lazy.stats.allocatedBytes === initialBytes && !lazy.texturePool.size,
                'Unused release or zero-dispatch materialized scratch');

            const input = lazy.call(1, 256, 0, 1, 0), read = lazy.call(1, 256, 0, 2, 0);
            module.HEAPF32.set([19, 23, 29, 31], pointer / 4); lazy.call(3, input, pointer, 256);
            const uploaded = allocate(); copy(9, input, uploaded);
            check(creations === 1 && lazy._get(uploaded).texture, 'Buffer-to-texture did not materialize destination');
            const copied = allocate(); copy(11, uploaded, copied);
            check(creations === 2 && lazy._get(copied).texture, 'Texture copy did not materialize destination');
            copy(10, copied, read); lazy._flush();
            const readBuffer = lazy._get(read).buffer;
            await readBuffer.mapAsync(GPUMapMode.READ);
            copyValues = Array.from(new Float32Array(readBuffer.getMappedRange(), 0, 4)); readBuffer.unmap();
            check(copyValues.join(',') === '19,23,29,31', 'Lazy materialization changed ordered texture-copy bytes');
            const emptySource = allocate(), emptyDestination = allocate(); copy(11, emptySource, emptyDestination);
            check(creations === 4 && lazy._get(emptySource).texture && lazy._get(emptyDestination).texture,
                'Texture copy did not materialize both untouched descriptors');
            const readSource = allocate(); copy(10, readSource, read); lazy._flush();
            check(creations === 5 && lazy._get(readSource).texture, 'Texture-to-buffer did not materialize source');
            await readBuffer.mapAsync(GPUMapMode.READ);
            check(new Uint8Array(readBuffer.getMappedRange(), 0, 16).every(value => value === 0), 'Fresh lazy texture was not zero-initialized');
            readBuffer.unmap();

            const bound = allocate(), name = 'lazy-materialization-oracle';
            lazy.shaders.set(name, { code: `
                @group(0) @binding(0) var atlas: texture_storage_3d<rgba32float, write>;
                @compute @workgroup_size(1) fn main() { textureStore(atlas, vec3i(0), vec4f(3, 5, 7, 11)); }`,
                reflection: { parameters: [{ binding: { index: 0 }, type: { baseShape: 'texture3D', access: 'readWrite' } }] } });
            const pipeline = lazy._resource({ kind: 'pipeline', name, pipeline: null });
            module.HEAPU32.set([0, 4, bound], pointer / 4); lazy.call(7, pipeline, pointer, 1, 1, 1, 1);
            check(creations === 6 && lazy._get(bound).texture, 'Native dispatch binding did not materialize texture');
            const density = allocate(), velocity = allocate(); lazy.call(12, density, velocity, 0, 0, 0, 0);
            check(creations === 8 && lazy.output.density.texture && lazy.output.velocity.texture,
                'Published output exposed unmaterialized density or velocity');
            lazy._flush(); await device.queue.onSubmittedWorkDone();
            bindingValues = Array.from(new Float32Array(await snapshot(device, { density: lazy._get(bound) }, 'density')).slice(0, 4));
            check(bindingValues.join(',') === '3,5,7,11', 'Lazy native dispatch did not write expected values');
            for (const resource of [lazy._get(emptyDestination), lazy.output.density, lazy.output.velocity])
                check(new Uint8Array(await snapshot(device, { density: resource }, 'density')).every(value => value === 0),
                    'Untouched copied/published texture was not zero');
            ownedMemory(lazy);
        } finally {
            module._free(pointer);
            try { await lazy.dispose(); } finally { device.createTexture = createTexture; }
        }
        check(creations === 8 && lazy.stats.allocatedBytes === 0 && !lazy.resources.size && !lazy.texturePool.size,
            'Disposal materialized unused descriptors or leaked physical ownership');
        cases.push({ name: 'lazy-texture-consumers-and-unused-scratch', status: 'PASS', creations,
            unusedScratchBytes: 640 * 1024 * 1024, copyValues, bindingValues,
            coverage: 'zero-dispatch; unconsumed release/dispose; buffer/texture copies both directions; dispatch binding; output publication; actual GPU readback' });

        const failed = await FlowHostWebGpu.create(module, device, shaders, { maxBlocks: 16 });
        failed._flush(); await device.queue.onSubmittedWorkDone(); failed._retire(); failed._retire();
        const descriptor = module._malloc(28), failureInitialBytes = failed.stats.allocatedBytes;
        const sentinel = new Error('Injected physical texture creation failure');
        let observed;
        try {
            module.HEAPU32.set([2, 0, 1, 4, 4, 4, 1], descriptor / 4);
            device.createTexture = () => { throw sentinel; };
            const id = failed.call(4, descriptor);
            try { failed._materializeTexture(failed._get(id)); } catch (error) { observed = error; }
            check(observed === sentinel && failed._get(id).texture === null && failed._get(id).bytes === 0
                && failed.stats.allocatedBytes === failureInitialBytes, 'Failed materialization changed physical accounting');
        } finally { device.createTexture = createTexture; module._free(descriptor); await failed.dispose(); }
        check(failed.stats.allocatedBytes === 0 && !failed.resources.size && !failed.texturePool.size,
            'Failed materialization leaked resources');
        cases.push({ name: 'lazy-texture-create-failure-cleanup', status: 'PASS',
            injection: 'JS createTexture rejection with real native host and resources' });
    }

    const fixtures = [
        { name: 'smoke-moving-sphere', fire: false, type: 'sphere' },
        { name: 'fire-moving-box', fire: true, type: 'box' },
    ];
    for (const fixture of differential ? fixtures : []) {
        const traces = [];
        for (const Host of [PreviousHost, FlowHostWebGpu]) {
            const flow = await Host.create(module, device, shaders, { maxBlocks: 32, cellSize: .12 });
            const trace = { states: [], frames: [], stats: null, elapsedMs: 0, uploadChunkSizes: {},
                peakMemory: ownedMemory(flow), finalMemory: null };
            const originalCall = flow.call.bind(flow);
            flow.call = (...args) => {
                const value = originalCall(...args);
                if (flow.stats.allocatedBytes > trace.peakMemory.allocatedBytes) trace.peakMemory = ownedMemory(flow);
                return value;
            };
            const originalFlush = flow._flush.bind(flow);
            flow._flush = () => {
                for (const chunk of flow.uploadChunks ?? []) {
                    const key = `${chunk.used}/${chunk.size}`;
                    trace.uploadChunkSizes[key] = (trace.uploadChunkSizes[key] ?? 0) + 1;
                }
                return originalFlush();
            };
            try {
                flow.setScene({ layers: [{ id: 0, cellSize: .12, gravity: [0, 0, 0], pressure: true, combustion: fixture.fire, vorticity: .2 }],
                    emitters: [{ id: 1, layer: 0, type: 'sphere', position: [0, 0, 0], radius: .32, velocity: [0, 2.4, 0],
                        temperature: fixture.fire ? .85 : 0, fuel: fixture.fire ? .65 : 0, smoke: .85,
                        coupleRateVelocity: 60, coupleRateTemperature: 60, coupleRateFuel: 60, coupleRateSmoke: 60 }] });
                for (let frame = 0; frame < 24; ++frame) {
                    flow.setColliders([{ id: 1, layer: 0, type: fixture.type, position: [.1 * Math.sin(frame / 8), .4, 0],
                        quaternion: [0, Math.sin(frame / 90), 0, Math.cos(frame / 90)],
                        ...(fixture.type === 'sphere' ? { radius: .2 } : { halfSize: [.2, .25, .2] }), coupleRateVelocity: 120 }]);
                    const start = performance.now(); await flow.step(1 / 60); trace.elapsedMs += performance.now() - start;
                    trace.frames.push({ blocks: flow.stats.activeBlocks, level: [...flow.output.level], layers: structuredClone(flow.output.layers) });
                    if ([0, 7, 23].includes(frame)) trace.states.push({ frame, density: await snapshot(device, flow.output, 'density'),
                        velocity: await snapshot(device, flow.output, 'velocity'), sparse: await snapshot(device, flow.output, 'sparse') });
                }
                trace.stats = structuredClone(flow.stats);
                trace.finalMemory = ownedMemory(flow);
            } finally { await flow.dispose(); }
            check(flow.stats.allocatedBytes === 0, 'Native differential host leaked resources');
            traces.push(trace);
        }
        const [before, after] = traces;
        check(JSON.stringify(before.frames) === JSON.stringify(after.frames), `${fixture.name}: sparse allocation/metadata evolution changed`);
        let maximumError = 0, comparedScalars = 0, positiveSmoke = 0, positiveBurn = 0;
        for (let frame = 0; frame < before.states.length; ++frame) for (const key of ['density', 'velocity', 'sparse']) {
            const old = key === 'sparse' ? new Uint32Array(before.states[frame][key]) : new Float32Array(before.states[frame][key]);
            const next = key === 'sparse' ? new Uint32Array(after.states[frame][key]) : new Float32Array(after.states[frame][key]);
            check(old.length === next.length, 'Native field size changed');
            for (let index = 0; index < old.length; ++index) {
                const delta = Math.abs(old[index] - next[index]); maximumError = Math.max(maximumError, delta); ++comparedScalars;
                check(Number.isFinite(old[index]) && Number.isFinite(next[index]) && delta <= (key === 'sparse' ? 0 : 1e-6), `${fixture.name} ${key}[${index}] differs: ${old[index]} vs ${next[index]}`);
                if (key === 'density' && index % 4 === 3 && next[index] > .01) ++positiveSmoke;
                if (key === 'density' && index % 4 === 2 && next[index] > 1e-8) ++positiveBurn;
            }
        }
        check(positiveSmoke > 0 && (!fixture.fire || positiveBurn > 0), 'Differential fixture compared empty/inactive chemistry');
        check(before.stats.dispatches === after.stats.dispatches, 'Ordered uploads changed the native dispatch graph');
        cases.push({ name: `native-field-differential-${fixture.name}`, status: 'PASS', frames: 24, comparedScalars, maximumError,
            positiveSmoke, positiveBurn, nativeDispatches: after.stats.dispatches, candidateSubmissions: after.stats.queueSubmissions,
            candidateUploadBytes: after.stats.uploadBytes, candidateUploadChunks: after.stats.uploadChunks,
            candidateUploadChunkSizes: after.uploadChunkSizes,
            memory: { scope: 'client-owned GPU buffers/textures from actual GPU descriptors; excludes observational readbacks and driver-internal allocations',
                baselinePeak: before.peakMemory, candidatePeak: after.peakMemory, baselineFinal: before.finalMemory, candidateFinal: after.finalMemory },
            oldStepMs: before.elapsedMs / 24, candidateStepMs: after.elapsedMs / 24, timingScope: 'sequential correctness fixture; not an isolated performance claim' });
    }
    check(module._pr_flow_host_live() === baselineContexts, 'Native Flow contexts leaked');
    cases.push({ name: 'cleanup', status: 'PASS', nativeContexts: baselineContexts });
    return { status: 'PASS', cleanup: 'PASS', cases };
}
