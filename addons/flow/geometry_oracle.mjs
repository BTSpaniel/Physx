// SPDX-License-Identifier: MIT
import { FlowHostWebGpu } from './flow_host_webgpu.mjs';

function assert(condition, message) { if (!condition) throw new Error(message); }
function reject(action, message, expectedMessage) {
    let failure;
    try { action(); } catch (error) { failure = error; }
    assert(failure && (!expectedMessage || expectedMessage.test(String(failure))), `${message}: ${failure ?? 'accepted'}`);
}
function passCount(host, token) {
    return Object.entries(host.stats.passes).filter(([name]) => name.includes(token)).reduce((sum, [, n]) => sum + n, 0);
}
function close(actual, expected, tolerance, message) {
    const error = Math.max(...expected.map((value, index) => Math.abs(actual[index] - value)));
    assert(actual.length === expected.length && actual.every(Number.isFinite) && error <= tolerance,
        `${message}: error=${error}, actual=${Array.from(actual)}, expected=${expected}`);
    return error;
}
const layer = id => ({ id, cellSize: .1, gravity: [0, 0, 0], pressure: false, combustion: false, vorticity: 0 });
const common = { temperature: 0, fuel: 0, smoke: .6, coupleRateVelocity: 5000, coupleRateSmoke: 5000 };
const quad = (x, y, z, halfX = .4, halfY = .4) => [x-halfX,y-halfY,z, x+halfX,y-halfY,z, x+halfX,y+halfY,z, x-halfX,y+halfY,z];
const quadIndices = [0, 1, 2, 0, 2, 3];
async function frames(host, count = 3) { for (let i = 0; i < count; i++) await host.step(); }
async function samples(host, coordinates, id) { return Array.from(await host.sampleVelocity(new Float32Array(coordinates), id === undefined ? undefined : { layer: id })); }

async function densityStats(host) {
    const { texture, size, format } = host.output.density;
    assert(format === 'rgba32float', 'Geometry oracle requires the native high-precision density atlas');
    const [width, height, depth] = size, row = Math.ceil(width * 16 / 256) * 256;
    const buffer = host.device.createBuffer({ size: row * height * depth, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ });
    try {
        const encoder = host.device.createCommandEncoder();
        encoder.copyTextureToBuffer({ texture }, { buffer, bytesPerRow: row, rowsPerImage: height }, size);
        host.device.queue.submit([encoder.finish()]);
        await buffer.mapAsync(GPUMapMode.READ);
        const values = new Float32Array(buffer.getMappedRange());
        let positive = 0, maxSmoke = 0, smokeSum = 0;
        for (let z = 0; z < depth; z++) for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
            const i = (z * height + y) * row / 4 + x * 4;
            assert(values.subarray(i, i + 4).every(Number.isFinite), 'Nonfinite native density field');
            if (values[i + 3] > 1e-6) positive++;
            maxSmoke = Math.max(maxSmoke, values[i + 3]); smokeSum += values[i + 3];
        }
        buffer.unmap();
        assert(positive > 0 && maxSmoke > .1, 'Native geometry emitted no smoke');
        return { positiveVoxels: positive, maxSmoke, smokeSum, units: 'normalized atlas values, including sparse halo copies' };
    } finally { buffer.destroy(); }
}

// Independent raw ABI fixture. Caller storage is freed/overwritten before any
// graph step, proving native ownership rather than only JS typed-array copies.
function nativePacket(module, type = 2) {
    const bytes = 320, pointer = module._malloc(bytes);
    assert(pointer, 'Geometry native fixture allocation failed');
    module.HEAPU8.fill(0, pointer, pointer + bytes);
    const word = pointer / 4, emitter = word + 8, geometry = emitter + 32;
    const positions = pointer + 224, indices = pointer + 272, velocities = pointer + 288;
    const u = module.HEAPU32, f = module.HEAPF32;
    u[word] = 0; f[word + 1] = .1;
    u[emitter] = 999; u[emitter + 1] = 0; u[emitter + 2] = type; u[emitter + 3] = 1;
    f[emitter + 13] = 1; f[emitter + 14] = .3; f[emitter + 19] = .6;
    f[emitter + 20] = 5000; f[emitter + 23] = 5000; f[emitter + 24] = 1;
    u[geometry] = 999; u[geometry + 1] = positions; u[geometry + 2] = type === 2 ? 1 : 3;
    u[geometry + 7] = 1; f[geometry + 8] = 1;
    if (type === 2) f.set([.1, .1, .1], positions / 4);
    else {
        f.set([-.3, -.3, .1, .5, -.3, .1, .1, .5, .1], positions / 4);
        u.set([0, 1, 2], indices / 4); u[geometry + 5] = indices; u[geometry + 6] = 3;
        f[geometry + 9] = -.15; f[geometry + 10] = .15;
    }
    return { pointer, bytes, word, emitter, geometry, positions, indices, velocities,
        invoke: handle => module._pr_flow_host_scene_geometry(handle, pointer, 1, pointer + 32, 1, pointer + 160, 1) };
}

/** Actual upstream point/mesh graph, numerical field coverage and ABI lifecycle. */
export async function verifyFlowGeometry(module, device, shaderRoot) {
    assert(module._pr_flow_host_geometry_abi?.() === 1 && module._pr_flow_host_scene_abi?.() === 1,
        'Native additive geometry ABI1 and compatible scene ABI1 are required');
    const cases = [], allStats = [];
    globalThis.flowGeometryProgress = { cases, stats: allStats };
    const record = (name, data = {}) => cases.push({ name, status: 'PASS', ...data });
    async function withHost(action, borrowedDevice = device) {
        const host = await FlowHostWebGpu.create(module, borrowedDevice, shaderRoot, { maxBlocks: 128, cellSize: .1 });
        try { return await action(host); }
        finally {
            allStats.push(structuredClone(host.stats));
            await host.dispose(); await host.dispose();
            assert(module._pr_flow_host_live() === 0 && !host.resources.size && host.stats.allocatedBytes === 0,
                'Geometry context leaked native or GPU resources');
        }
    }
    await withHost(async host => {
        const layers = [layer(3), layer(9)];
        const points = new Float32Array([-1.5, .1, .1, -1.5, 1.1, .1]);
        // Sample .1 m off the plane: upstream signedDistTriangle uses sign(-t),
        // whose exact-zero coplanar tie cannot distinguish neighboring faces.
        const vertices = new Float32Array([...quad(1.3, .1, 0), ...quad(1.3, 2.1, 0)]);
        const indices = new Uint32Array(quadIndices);
        let point = { ...common, id: 101, layer: 3, type: 'points', positions: points, velocity: [.8, 0, 0] };
        let mesh = { ...common, id: 202, layer: 9, type: 'mesh', positions: vertices, indices,
            minDistance: -.15, maxDistance: .15, velocity: [0, .6, 0] };
        host.setScene({ layers, emitters: [point, mesh] });
        // Mutating all source arrays after setScene must not change the native scene.
        points.fill(99); vertices.fill(99); indices.fill(99);
        await frames(host);
        const pointAt = [-1.5, .1, .1, -1.5, 1.1, .1], meshAt = [1.3, .1, .1, 1.3, 2.1, .1];
        const pointValue = await samples(host, pointAt, 3), meshValue = await samples(host, meshAt, 9);
        assert(passCount(host, 'EmitterPoint3CS') > 0, 'Native point scatter/reduce/apply did not execute');
        record('points-native-emission', { velocity: pointValue, maxError: close(pointValue, [.8,0,0,.8,0,0], .025, 'Point grid-center emission'),
            pointKernelPasses: passCount(host, 'EmitterPoint3CS') });
        assert(passCount(host, 'EmitterMeshClosestCS') > 0 && passCount(host, 'EmitterMeshApplyCS') > 0, 'Native mesh closest/apply did not execute');
        record('mesh-native-emission', { velocity: meshValue, maxError: close(meshValue, [0,.6,0,0,0,0], .025, 'Indexed mesh shell emission'),
            meshKernelPasses: passCount(host, 'EmitterMeshApplyCS'), density: await densityStats(host) });
        const crossPoints = await samples(host, pointAt, 9), crossMesh = await samples(host, meshAt, 3);
        close(crossPoints, [0,0,0,0,0,0], 1e-6, 'Mesh layer contaminated point layer coordinates');
        close(crossMesh, [0,0,0,0,0,0], 1e-6, 'Point layer contaminated mesh layer coordinates');
        record('mixed-layer-isolation', { layerIds: host.output.layers.map(row => row.id), crossPoints, crossMesh });

        points.set([-1.5,.1,.1,-1.5,1.1,.1]); vertices.set([...quad(1.3,.1,0), ...quad(1.3,2.1,0)]); indices.set(quadIndices);
        const pointVelocities = new Float32Array([0,0,.45,-.35,0,0]);
        const vertexVelocities = new Float32Array([0,0,0,.4,0,0,.4,0,0,0,0,0, 0,0,0,0,0,0,0,0,0,0,0,0]);
        point = { ...point, velocities: pointVelocities }; mesh = { ...mesh, velocities: vertexVelocities };
        host.setScene({ layers, emitters: [point, mesh] });
        pointVelocities.fill(99); vertexVelocities.fill(99);
        await frames(host, 2);
        const pointAttributes = await samples(host, pointAt, 3);
        const meshAttributes = await samples(host, [1.1,.1,.1,1.5,.1,.1], 9);
        record('vertex-velocity-attributes', {
            point: pointAttributes, mesh: meshAttributes,
            maxPointError: close(pointAttributes, [0,0,.45,-.35,0,0], .025, 'Per-point world velocity'),
            maxMeshError: close(meshAttributes, [.1,0,0,.3,0,0], .025, 'Barycentric affine per-vertex world velocity'),
        });
        // Restore the copied source values for subsequent setter calls.
        pointVelocities.set([0,0,.45,-.35,0,0]);
        vertexVelocities.set([0,0,0,.4,0,0,.4,0,0,0,0,0, 0,0,0,0,0,0,0,0,0,0,0,0]);
        const stable = { layers, emitters: [point, mesh] };
        const invalid = [
            { ...point, positions: [] }, { ...point, positions: [0,0] }, { ...point, positions: [NaN,0,0] },
            { ...point, positions: [1e100,0,0] }, { ...point, velocities: [0,0,0] },
            { ...point, widths: [1] }, { ...point, widthScale: 1 }, { ...point, radius: 1 }, { ...point, halfSize: [1,1,1] },
            { ...point, allocationScale: 0 }, { ...point, allocateMask: 'false' },
            { ...mesh, indices: [0,1] }, { ...mesh, indices: [0,1,99] }, { ...mesh, indices: [0,0,1] },
            { ...mesh, indices: [0,.5,1] }, { ...mesh, minDistance: .2, maxDistance: .1 },
            { ...mesh, velocities: [0,0,0] }, { ...mesh, orientationLeftHanded: 'false' },
            { ...mesh, positions: [1,1,0,1+1e-10,1,0,1,1+1e-10,0], indices: [0,1,2], velocities: [] },
            { ...mesh, minDistance: 1, maxDistance: 1 + 1e-10 },
        ];
        const beforeReject = await samples(host, [...pointAt, ...meshAt], 3);
        for (const emitter of invalid) reject(() => host.setScene({ layers, emitters: [emitter] }), 'Invalid JS geometry accepted');
        const bufferLimit = Math.min(0x80000000, device.limits.maxBufferSize, device.limits.maxStorageBufferBindingSize);
        const oversizedCount = Math.floor(bufferLimit / 128) + 1;
        reject(() => host.setScene({ layers, emitters: [{ ...point, positions: new Float32Array(oversizedCount * 3), velocities: [] }] }),
            'Device-derived point scratch limit was not admitted before allocation', /GPU buffer or dispatch limits/);
        close(await samples(host, [...pointAt, ...meshAt], 3), beforeReject, 0, 'Rejected JS transaction changed GPU field');
        const pending = host.step();
        reject(() => host.setScene(stable), 'Geometry mutation raced a pending native step');
        await pending;
        assert(host.output.layers.map(row => row.id).join(',') === '3,9', 'Rejected transaction changed native layers');
        record('geometry-transaction-rejection', { rejectedCases: invalid.length + 1, concurrentMutationRejected: true,
            deviceBufferLimit: bufferLimit, rejectedPointCount: oversizedCount, f32DegenerateAndShellCases: true });

        const packet = nativePacket(module, 3), original = module.HEAPU8.slice(packet.pointer, packet.pointer + packet.bytes);
        const nativeMutations = [
            () => module.HEAPU32[packet.geometry + 1] = module.HEAPU8.byteLength - 4,
            () => module.HEAPU32[packet.geometry + 2] = 0,
            () => module.HEAPU32[packet.geometry + 3] = packet.positions,
            () => module.HEAPU32[packet.geometry + 5] = 0,
            () => module.HEAPU32[packet.geometry + 6] = 2,
            () => module.HEAPU32[packet.indices / 4 + 2] = 999,
            () => module.HEAPU32[packet.indices / 4 + 2] = 0,
            () => module.HEAPF32[packet.geometry + 8] = 2,
            () => module.HEAPF32[packet.geometry + 10] = -.2,
            () => module.HEAPU32[packet.geometry + 12] = 1,
            () => module.HEAPU32[packet.geometry + 13] = 1,
            () => module.HEAPU32[packet.geometry] = 998,
            () => module.HEAPF32[packet.positions / 4] = Infinity,
            () => module.HEAPU32[packet.emitter + 2] = 77,
            () => module.HEAPU32[packet.emitter + 1] = 77,
        ];
        try {
            assert(packet.invoke(0) === 0, 'Native invalid handle accepted');
            for (const mutate of nativeMutations) {
                module.HEAPU8.set(original, packet.pointer); mutate();
                assert(packet.invoke(host.handle) === 0, 'Invalid native geometry accepted');
            }
        } finally { module._free(packet.pointer); }
        await host.step();
        assert(host.output.layers.map(row => row.id).join(',') === '3,9', 'Rejected native packet partially replaced the scene');
        close(await samples(host, pointAt, 3), [0,0,.45,-.35,0,0], .025, 'Native rejection changed existing emitter');
        record('native-geometry-rejection', { rejectedCases: nativeMutations.length + 1 });

        // Same ID and same point count, new spatial allocation and upload data.
        const moved = [-.7,.1,.1,-.7,1.1,.1];
        close(await samples(host, moved, 3), [0,0,0,0,0,0], 1e-5, 'Replacement destination was already active');
        point = { ...point, positions: new Float32Array(moved), velocity: [0,0,-.7] }; delete point.velocities;
        host.setScene({ layers, emitters: [point, mesh] }); await frames(host, 2);
        const movedValue = await samples(host, moved, 3);
        record('geometry-replacement', { velocity: movedValue, maxError: close(movedValue, [0,0,-.7,0,0,-.7], .025, 'Same-ID point position replacement') });

        // Vertex bytes/count and triangle count stay unchanged; only topology changes.
        close(await samples(host, [1.3,2.1,.1], 9), [0,0,0], 1e-5, 'Topology replacement destination was already emitting');
        mesh = { ...mesh, indices: new Uint32Array([4,5,6,4,6,7]), velocity: [0,-.5,0] }; delete mesh.velocities;
        host.setScene({ layers, emitters: [point, mesh] }); await frames(host, 2);
        const topology = await samples(host, [1.3,2.1,.1], 9);
        record('mesh-topology-replacement', { velocity: topology, maxError: close(topology, [0,-.5,0], .025, 'Same-ID mesh index replacement') });
        const pointPasses = passCount(host, 'EmitterPoint3CS'), meshPasses = passCount(host, 'EmitterMeshApplyCS');
        host.setScene({ layers, emitters: [] }); await frames(host, 2);
        assert(passCount(host, 'EmitterPoint3CS') === pointPasses && passCount(host, 'EmitterMeshApplyCS') === meshPasses,
            'Removed geometry still dispatched native emission');
        const retained = await samples(host, moved, 3);
        assert(retained.some(value => Math.abs(value) > .05), 'Geometry removal deleted the existing field');
        record('geometry-removal', { retainedVelocity: retained, emitterDispatchesAfterRemoval: 0 });
    });

    await withHost(async host => {
        const rotation = [0,0,Math.SQRT1_2,Math.SQRT1_2];
        host.setScene({ layers: [layer(3), layer(9)], emitters: [
            { ...common, id: 1, layer: 3, type: 'mesh', positions: quad(0,0,0,.6,.1), indices: quadIndices,
                position: [.1,.1,0], quaternion: rotation, velocity: [.2,0,0], minDistance: -.15, maxDistance: .15 },
            { ...common, id: 2, layer: 9, type: 'points', positions: [0,0,0,.8,0,0], position: [1.1,.1,.1],
                quaternion: rotation, velocity: [0,0,.3] },
        ] }); await frames(host);
        const mesh = await samples(host, [.1,.1,.1,.1,.5,.1,.5,.1,.1], 3);
        const points = await samples(host, [1.1,.1,.1,1.1,.9,.1,1.9,.1,.1], 9);
        record('geometry-transform-coverage', { mesh, points,
            maxMeshError: close(mesh, [.2,0,0,.2,0,0,0,0,0], .025, 'Mesh quaternion and translation coverage'),
            maxPointError: close(points, [0,0,.3,0,0,.3,0,0,0], .025, 'Point quaternion and translation coverage') });
    });

    await withHost(async host => {
        const packet = nativePacket(module);
        try {
            assert(packet.invoke(host.handle) === 1, 'Native valid ownership packet rejected');
            module.HEAPU8.fill(0xa5, packet.pointer, packet.pointer + packet.bytes);
        } finally { module._free(packet.pointer); }
        await frames(host);
        const value = await samples(host, [.1,.1,.1]);
        const originalMalloc = module._malloc, viewPointer = originalMalloc(12);
        assert(viewPointer, 'Heap-view ownership fixture allocation failed');
        module.HEAPF32.set([.9,.1,.1], viewPointer / 4);
        const heapView = module.HEAPF32.subarray(viewPointer / 4, viewPointer / 4 + 3), oldBuffer = heapView.buffer;
        let growthPointer = 0, grew = false;
        try {
            module._malloc = bytes => {
                if (!growthPointer) {
                    growthPointer = originalMalloc(oldBuffer.byteLength + 65536);
                    assert(growthPointer, 'Controlled WASM heap growth allocation failed');
                    grew = module.HEAPU8.buffer !== oldBuffer;
                }
                return originalMalloc(bytes);
            };
            host.setScene({ layers: [layer(0)], emitters: [{ ...common, id: 1000, type: 'points', positions: heapView, velocity: [-.4,0,0] }] });
        } finally {
            module._malloc = originalMalloc;
            if (growthPointer) module._free(growthPointer);
            module._free(viewPointer);
        }
        assert(grew && heapView.byteLength === 0, 'Heap-view fixture did not exercise real WASM memory growth');
        await frames(host, 2);
        const heapValue = await samples(host, [.9,.1,.1], 0);
        close(heapValue, [-.4,0,0], .025, 'WASM source view was read after growing malloc detached it');
        record('geometry-source-ownership', { jsArraysMutatedAfterSetScene: true, nativePacketOverwrittenAndFreed: true,
            wasmHeapGrewDuringSetter: grew, heapViewVelocity: heapValue,
            velocity: value, maxError: close(value, [.3,0,0], .025, 'Native geometry retained caller storage') });
    });
    const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
    assert((adapter?.info?.isFallbackAdapter ?? adapter?.isFallbackAdapter) === false, 'Limited-device fixture requires native GPU');
    const limited = await adapter.requestDevice({ requiredFeatures: ['float32-filterable'], requiredLimits: {
        maxComputeInvocationsPerWorkgroup: 1024, maxComputeWorkgroupSizeX: 1024, maxStorageBuffersPerShaderStage: 8 } });
    const limitedErrors = [];
    limited.addEventListener('uncapturederror', event => limitedErrors.push(event.error.message));
    try {
        assert(limited.limits.maxStorageBuffersPerShaderStage === 8, 'Limited-device fixture did not constrain storage bindings');
        await withHost(async host => {
            host.setScene({ layers: [layer(0)], emitters: [{ ...common, id: 1, type: 'mesh', positions: quad(.1,.1,0),
                indices: quadIndices, minDistance: -.15, maxDistance: .15, velocity: [0,.25,0] }] });
            await frames(host, 2);
            reject(() => host.setScene({ layers: [layer(0)], emitters: [{ ...common, id: 2, type: 'points',
                positions: [.1,.1,.1], velocity: [.5,0,0] }] }), 'Points accepted an eight-storage-buffer device', /storage buffer/i);
            await host.step();
            const value = await samples(host, [.1,.1,.1], 0);
            close(value, [0,.25,0], .025, 'Unsupported points changed a valid low-limit mesh scene');
            Object.assign(cases.find(row => row.name === 'geometry-transaction-rejection'), {
                eightBindingDevicePointsRejected: true, eightBindingDeviceMeshVelocity: value });
        }, limited);
        assert(!limitedErrors.length, `Limited-device fixture emitted GPU errors: ${limitedErrors}`);
    } finally { limited.destroy(); }
    record('cleanup', { nativeLive: module._pr_flow_host_live(), contextsDisposed: allStats.length });
    const expected = ['points-native-emission','mesh-native-emission','geometry-transform-coverage','vertex-velocity-attributes',
        'geometry-replacement','mesh-topology-replacement','geometry-transaction-rejection','native-geometry-rejection',
        'geometry-removal','mixed-layer-isolation','geometry-source-ownership','cleanup'];
    assert(cases.length === expected.length && expected.every(name => cases.some(row => row.name === name)), 'Geometry acceptance coverage incomplete');
    return { flowGeometryAbi: 1, cleanup: 'PASS', cases: expected.map(name => cases.find(row => row.name === name)), stats: allStats,
        model: 'Native upstream 8-cell quantized point splats and indexed triangle distance shells; per-vertex velocities in world coordinates',
        limitation: 'Exact coplanar nearest-face ties inherit upstream sign(0) behavior; barycentric oracles sample off-plane within the explicit distance shell' };
}
