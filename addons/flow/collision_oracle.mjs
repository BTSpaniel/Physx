// SPDX-License-Identifier: MIT
import { FlowHostWebGpu } from './flow_host_webgpu.mjs';

function assert(value, message) { if (!value) throw new Error(message); }
function close(actual, expected, tolerance, message) {
    const error = Math.max(...expected.map((value, i) => Math.abs(value - actual[i])));
    assert(actual.length === expected.length && actual.every(Number.isFinite) && error <= tolerance,
        `${message}: actual=${actual}, expected=${expected}, error=${error}`);
    return error;
}
function rejects(fn, message) { let error; try { fn(); } catch (e) { error = e; } assert(error, message); }
const layer = id => ({ id, cellSize: .1, gravity: [0, 0, 0], pressure: false, combustion: false, vorticity: 0 });
const source = id => ({ id: 100 + id, layer: id, type: 'box', halfSize: [1, 1, 1],
    velocity: [.8, 0, 0], temperature: .25, fuel: .2, smoke: .5, burn: .1, divergence: .3,
    coupleRateVelocity: 5000, coupleRateTemperature: 5000, coupleRateFuel: 5000,
    coupleRateSmoke: 5000, coupleRateBurn: 5000, coupleRateDivergence: 5000, applyPostPressure: true });
const scene = { layers: [layer(3), layer(9)], emitters: [source(3), source(9)] };
async function frames(host, count = 4, dt = 1 / 60) { for (let i = 0; i < count; i++) await host.step(dt); }
async function sample(host, positions = [.1, .1, .1], id = 3) {
    return Array.from(await host.sampleVelocity(new Float32Array(positions), { layer: id }));
}
async function atlas(host, key = 'density') {
    const { texture, size, format } = host.output[key];
    assert(format === 'rgba32float', 'Collision oracle requires high-precision native atlases');
    const [width, height, depth] = size, pitch = Math.ceil(width * 16 / 256) * 256;
    const buffer = host.device.createBuffer({ size: pitch * height * depth, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ });
    try {
        const encoder = host.device.createCommandEncoder();
        encoder.copyTextureToBuffer({ texture }, { buffer, bytesPerRow: pitch, rowsPerImage: height }, size);
        host.device.queue.submit([encoder.finish()]); await buffer.mapAsync(GPUMapMode.READ);
        const data = new Float32Array(buffer.getMappedRange()), copy = new Float32Array(width * height * depth * 4);
        for (let z = 0; z < depth; z++) for (let y = 0; y < height; y++)
            copy.set(data.subarray((z * height + y) * pitch / 4, (z * height + y) * pitch / 4 + width * 4), (z * height + y) * width * 4);
        buffer.unmap(); assert(copy.every(Number.isFinite), `Nonfinite ${key} atlas`); return copy;
    } finally { buffer.destroy(); }
}
function maxDifference(a, b, channel) {
    assert(a.length === b.length, 'Native atlas size changed between paired fixtures');
    let difference = 0;
    for (let i = channel ?? 0; i < a.length; i += channel === undefined ? 1 : 4) difference = Math.max(difference, Math.abs(a[i] - b[i]));
    return difference;
}
function nativePacket(module) {
    const pointer = module._malloc(160); assert(pointer, 'Native collider fixture allocation failed');
    const data = new ArrayBuffer(160), u = new Uint32Array(data), f = new Float32Array(data);
    u.set([17, 3, 0, 5]); f.set([0, 0, 0, .7, 0, 0, 0, 0, 0, 1, 5000], 4);
    return { pointer, data, u, f, upload: () => module.HEAPU8.set(new Uint8Array(data), pointer) };
}

/** Native velocity-obstacle proof. Targets use the upstream backward pose
 * difference and two half-step relaxation passes, not an invented drag force.
 * Scalar equality is checked before changed velocities can advect later frames.
 */
export async function verifyFlowCollision(module, device, shaderRoot) {
    assert(module._pr_flow_host_collision_abi?.() === 1, 'Collision ABI 1 missing');
    const initialLive = module._pr_flow_host_live(), hosts = [], cases = [];
    globalThis.flowCollisionProgress = cases;
    const create = async (custom = scene) => {
        const host = await FlowHostWebGpu.create(module, device, shaderRoot, { maxBlocks: 128, cellSize: .1 });
        hosts.push(host); host.setScene(custom); return host;
    };
    const host = await create(), baseline = await create();
    const sphere = { id: 17, layer: 3, type: 'sphere', radius: .7, coupleRateVelocity: 30, multisample: false };
    const box = { id: 27, layer: 3, type: 'box', halfSize: [.7, .7, .7], coupleRateVelocity: 5000, multisample: false };
    try {
        await frames(host); await frames(baseline);
        close(await sample(host), [.8, 0, 0], 1e-5, 'Matched input field');
        const receipt = host.setColliders([sphere]);
        assert(receipt.count === 1 && receipt.enabledCount === 1 && receipt.abi === 1 && receipt.mode === 'one-way-native-velocity-obstacle', 'Collision receipt changed');
        await host.step(); await baseline.step();
        const staticVelocity = await sample(host), baselineVelocity = await sample(baseline);
        close(staticVelocity, [.8 * (1 - 30 / 120) ** 2, 0, 0], 2e-5, 'Two half-step static sphere law');
        const densityDifference = maxDifference(await atlas(host), await atlas(baseline));
        const divergenceDifference = maxDifference(await atlas(host, 'velocity'), await atlas(baseline, 'velocity'), 3);
        assert(densityDifference === 0 && divergenceDifference === 0, `Obstacle changed scalar channels: ${densityDifference}/${divergenceDifference}`);
        const dtResults = [];
        for (const dt of [1 / 120, 1 / 240, 1 / 60]) {
            host.setColliders([]); await host.step(1 / 60);
            close(await sample(host), [.8, 0, 0], 2e-5, 'Refresh field before separate collision timestep fixture');
            host.setColliders([sphere]);
            await host.step(dt);
            const actual = await sample(host), expected = [.8 * (1 - 30 * dt / 2) ** 2, 0, 0];
            close(actual, expected, 3e-5, 'Sphere timestep law'); dtResults.push({ dt, actual, expected });
        }
        cases.push({ name: 'sphere-static-velocity', status: 'PASS', staticVelocity, baselineVelocity, densityDifference, divergenceDifference, dtResults });

        host.setColliders([box]); await host.step(); close(await sample(host), [0, 0, 0], 2e-5, 'New box must prime at rest');
        host.setColliders([{ ...box, position: [.02, 0, 0] }]); await host.step();
        const translating = await sample(host); close(translating, [1.2, 0, 0], 3e-5, 'Native translating box');
        // Reset at identity, then rotate: upstream computes (x - R_old R_new^-1 x)/dt.
        host.setColliders([{ ...box, resetMotion: true }]); await host.step();
        const angle = .1, q = [0, 0, Math.sin(angle / 2), Math.cos(angle / 2)], point = [.3, .1, .1];
        host.setColliders([{ ...box, quaternion: q }]); await host.step();
        const rotating = await sample(host, point), expectedRotation = [
            (point[0] - Math.cos(angle) * point[0] - Math.sin(angle) * point[1]) * 60,
            (point[1] + Math.sin(angle) * point[0] - Math.cos(angle) * point[1]) * 60, 0];
        close(rotating, expectedRotation, 8e-5, 'Native finite-pose angular velocity');
        cases.push({ name: 'box-moving-velocity', status: 'PASS', translating, rotating, expectedRotation });

        host.setColliders([{ ...box, halfSize: [.65, .13, .5], quaternion: [0, 0, Math.SQRT1_2, Math.SQRT1_2], resetMotion: true }]);
        await host.step(); const coverage = await sample(host, [.1, .5, .1, .5, .1, .1]);
        close(coverage, [0, 0, 0, .8, 0, 0], 3e-5, 'Rotated thin box coverage');
        host.setColliders([{ ...box, halfSize: [.5, .13, .5], coupleRateVelocity: 30, multisample: true, resetMotion: true }]);
        await host.step(); const multisample = await sample(host);
        close(multisample, [.8 * (1 - 30 / 120 * .5) ** 2, 0, 0], 4e-5, 'Eight-sample half-covered cell');
        cases.push({ name: 'rotated-box-coverage', status: 'PASS', coverage, multisample, coverageFraction: .5 });
        const isolated = await sample(host, [.1, .1, .1], 9); close(isolated, [.8, 0, 0], 2e-5, 'Other layer velocity');
        cases.push({ name: 'collider-layer-isolation', status: 'PASS', isolated });

        const a = { ...box, id: 101, halfSize: [.25, .3, .3], position: [-.4, 0, 0] };
        const b = { ...sphere, id: 202, radius: .25, position: [.4, 0, 0], coupleRateVelocity: 5000 };
        host.setColliders([a, b]); await host.step();
        host.setColliders([{ ...b, position: [.37, 0, 0] }, { ...a, position: [-.38, 0, 0] }]); await host.step();
        const reordered = await sample(host, [-.3, .1, .1, .3, .1, .1]);
        close(reordered, [1.2, 0, 0, -1.8, 0, 0], 1e-4, 'Per-ID motion survives caller reordering');
        host.setColliders([{ ...a, position: [-.36, 0, 0], enabled: false }]); await host.step();
        const disabled = await sample(host, [-.3, .1, .1]); close(disabled, [.8, 0, 0], 3e-5, 'Disabled collider changed velocity');
        host.setColliders([{ ...a, position: [-.34, 0, 0] }]); await host.step();
        close(await sample(host, [-.3, .1, .1]), [1.2, 0, 0], 1e-4, 'Disabled frames must still update pose history');
        host.setColliders([{ ...b, id: 101, position: [-.3, 0, 0] }]); await host.step();
        close(await sample(host, [-.3, .1, .1]), [0, 0, 0], 1e-4, 'Shape replacement must reset motion');
        const overlapA = { ...box, id: 101, coupleRateVelocity: 30 }, overlapB = { ...box, id: 202, coupleRateVelocity: 30 };
        host.setColliders([{ ...overlapA, resetMotion: true }, { ...overlapB, resetMotion: true }]); await host.step();
        host.setColliders([{ ...overlapB, position: [-.02, 0, 0] }, { ...overlapA, position: [.02, 0, 0] }]); await host.step();
        const overlapping = await sample(host), halfRemain = (1 - 30 / 120) ** 2;
        close(overlapping, [(.8 * halfRemain + 1.2 * (1 - halfRemain)) * halfRemain - 1.2 * (1 - halfRemain), 0, 0],
            8e-5, 'Overlapping boundaries must apply in stable ascending ID order');
        host.setColliders([]); await host.step(); close(await sample(host), [.8, 0, 0], 3e-5, 'Removed collider left an active boundary');
        // A post-render-only copy would vanish here. Actual Grid velocity must
        // retain the correction after both emitter and collider removal.
        host.setColliders([{ ...box, resetMotion: true }]); await host.step();
        host.setColliders([]); host.setScene({ layers: scene.layers, emitters: [] }); await host.step();
        const persisted = await sample(host); close(persisted, [0, 0, 0], .01, 'Collision did not modify canonical grid velocity');
        cases.push({ name: 'collider-replacement-removal', status: 'PASS', reordered, disabled, persisted, overlapping });
        host.setScene(scene); await frames(host);

        const ownedPosition = new Float32Array([0, 0, 0]), ownedSize = new Float32Array([.7, .7, .7]);
        const owned = { ...box, position: ownedPosition, halfSize: ownedSize };
        host.setColliders([owned]); ownedPosition.fill(90); ownedSize.fill(.001); owned.enabled = false;
        await host.step(); close(await sample(host), [0, 0, 0], 3e-5, 'Caller mutation leaked into native collider state');
        cases.push({ name: 'collider-source-ownership', status: 'PASS', sample: await sample(host) });

        const invalid = [[box, box], [{ ...box, id: 0 }], [{ ...box, layer: 77 }], [{ ...box, type: 'mesh' }],
            [{ ...box, position: [NaN, 0, 0] }], [{ ...box, halfSize: [0, 1, 1] }], [{ ...box, quaternion: [0, 0, 0, 0] }],
            [{ ...box, coupleRateVelocity: -1 }], [{ ...box, enabled: 1 }], [{ ...box, resetMotion: 'yes' }],
            [{ ...box, velocity: [3, 0, 0] }], [{ ...box, radius: 1 }], [{ ...sphere, radius: 1e30 }],
            [{ ...box, position: [1e30, 0, 0] }], [{ ...box, smoke: .1 }]];
        for (const rows of invalid) rejects(() => host.setColliders(rows), 'Invalid collider transaction accepted');
        rejects(() => host.setScene({ layers: [layer(9)], emitters: [source(9)] }), 'Layer removed while collider still referenced it');
        const stepping = host.step(); rejects(() => host.setColliders([]), 'Collider mutation raced an asynchronous step'); await stepping;
        close(await sample(host), [0, 0, 0], 3e-5, 'Failed transaction changed active collider');
        cases.push({ name: 'collision-transaction-rejection', status: 'PASS', rejected: invalid.length + 2 });

        const packet = nativePacket(module); let nativeRejected = 0;
        try {
            const original = new Uint8Array(packet.data).slice();
            const edits = [() => packet.u[0] = 0, () => packet.u[1] = 77, () => packet.u[2] = 2,
                () => packet.u[3] = 8, () => packet.u[15] = 1, () => packet.f[4] = NaN,
                () => packet.f[7] = 0, () => packet.f[8] = 1, () => packet.f[13] = 0,
                () => packet.f[14] = -1, () => packet.f[4] = 1e30];
            for (const edit of edits) {
                new Uint8Array(packet.data).set(original); edit(); packet.upload();
                assert(module._pr_flow_host_colliders(host.handle, packet.pointer, 1) === 0, 'Native invalid collider row accepted'); nativeRejected++;
            }
            for (const [handle, pointer, count] of [[0, packet.pointer, 1], [host.handle, packet.pointer + 1, 1],
                [host.handle, module.HEAPU8.byteLength - 4, 1], [host.handle, 0, 1], [host.handle, packet.pointer, 1025]]) {
                assert(module._pr_flow_host_colliders(handle, pointer, count) === 0, 'Native invalid span/handle accepted'); nativeRejected++;
            }
            await host.step(); close(await sample(host), [0, 0, 0], 3e-5, 'Rejected native transactions changed prior collider state');
            new Uint8Array(packet.data).set(original); packet.upload();
            assert(module._pr_flow_host_colliders(host.handle, packet.pointer, 1) === 1, 'Native valid collider rejected');
            module.HEAPU8.fill(0xff, packet.pointer, packet.pointer + 160);
        } finally { module._free(packet.pointer); }
        await host.step(); close(await sample(host), [0, 0, 0], 3e-5, 'Native collider retained caller packet storage');
        cases.push({ name: 'native-collision-rejection', status: 'PASS', rejected: nativeRejected, ownedNativePacket: true });

        const empty = await create({ layers: [layer(0)], emitters: [] });
        empty.setColliders([{ ...box, layer: 0 }]); await frames(empty);
        assert(empty.stats.activeBlocks === 0, 'Velocity collider allocated sparse blocks');
        assert(module._pr_flow_host_live() === initialLive + 3, 'Native contexts unexpectedly shared ownership');
        close(await sample(baseline), [.8, 0, 0], 3e-5, 'Collider mutated independent context');
        cases.push({ name: 'independent-contexts', status: 'PASS', colliderOnlyActiveBlocks: empty.stats.activeBlocks, liveContexts: module._pr_flow_host_live() });
        const passes = { ...host.stats.passes };
        assert(Object.keys(passes).some(name => name.includes('EmitterSimpleCS')) && Object.keys(passes).some(name => name.includes('EmitterBoxCS')), 'Actual native collision operators did not dispatch');
        for (const value of hosts) await value.dispose();
        assert(module._pr_flow_host_live() === initialLive && hosts.every(value => value.resources.size === 0 && value.stats.allocatedBytes === 0), 'Native collision resources leaked');
        rejects(() => host.setColliders([]), 'Disposed host accepted colliders');
        // Borrowed device must remain usable after all native contexts are gone.
        const buffer = device.createBuffer({ size: 4, usage: GPUBufferUsage.COPY_DST }); device.queue.writeBuffer(buffer, 0, new Uint32Array([1]));
        await device.queue.onSubmittedWorkDone(); buffer.destroy();
        cases.push({ name: 'cleanup', status: 'PASS', remainingContexts: module._pr_flow_host_live() });
        return { flowCollisionAbi: 1, cases, cleanup: 'PASS', passes,
            coupling: 'one-way-native-velocity-obstacle', scalarUnits: 'normalized Flow channels; no rigid reaction impulse claimed' };
    } finally { for (const value of hosts) if (!value.disposed) await value.dispose(); }
}
