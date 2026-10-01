// SPDX-License-Identifier: MIT
import { FlowHostWebGpu } from './flow_host_webgpu.mjs';

function assert(condition, message) { if (!condition) throw new Error(message); }
function expectReject(action, message) {
    let rejected = false;
    try { action(); } catch { rejected = true; }
    assert(rejected, message);
}
async function expectAsyncReject(action, message) {
    let rejected = false;
    try { await action(); } catch { rejected = true; }
    assert(rejected, message);
}
function passCount(host, name) {
    return Object.entries(host.stats.passes).filter(([key]) => key.includes(name)).reduce((sum, [, count]) => sum + count, 0);
}

/** Real native graph: layer isolation, geometric coverage, transactions and lifecycle. */
export async function verifyFlowScene(module, device, shaderRoot) {
    assert(module._pr_flow_host_scene_abi?.() === 1, 'Native scene ABI missing');
    const host = await FlowHostWebGpu.create(module, device, shaderRoot, { maxBlocks: 128, cellSize: .1 });
    const layers = [3, 9].map(id => ({ id, cellSize: id === 3 ? .1 : .125,
        gravity: [0, 0, 0], pressure: false, combustion: false, vorticity: 0 }));
    const emitters = [
        { id: 101, layer: 3, type: 'sphere', position: [-1.5, 0, 0], radius: .6, velocity: [.8, 0, 0],
            temperature: 0, fuel: 0, smoke: .5, coupleRateVelocity: 120, coupleRateSmoke: 120 },
        { id: 202, layer: 9, type: 'box', position: [1.5, 0, 0], halfSize: [.65, .15, .45],
            quaternion: [0, 0, Math.SQRT1_2, Math.SQRT1_2], velocity: [0, -.6, 0],
            temperature: 0, fuel: 0, smoke: .5, coupleRateVelocity: 120, coupleRateSmoke: 120 },
        { id: 303, layer: 3, type: 'sphere', position: [-1.5, 2, 0], radius: .6, velocity: [0, 0, .4],
            temperature: 0, fuel: 0, smoke: .5, coupleRateVelocity: 120, coupleRateSmoke: 120 },
    ];
    const scene = { layers, emitters };
    const positions = new Float32Array([-1.5, 0, 0, 1.5, 0, 0, 1.5, .4, 0, 1.95, 0, 0, 100, 100, 100, -1.5, 2, 0]);
    let result;
    try {
        host.setScene(scene);
        for (let i = 0; i < 12; i++) await host.step();
        assert(host.output.layers.length === 2, 'Both native layers must be published');
        const meta = host.output.layers.map(row => ({ ...row, blockSizeWorld: [...row.blockSizeWorld] }));
        assert(meta.some(row => row.id === 3) && meta.some(row => row.id === 9), 'Native layer IDs changed');
        assert(meta.find(row => row.id === 3).blockSizeWorld[0] !== meta.find(row => row.id === 9).blockSizeWorld[0], 'Layer cell sizes collapsed');
        const sphere = await host.sampleVelocity(positions, { layer: 3 });
        const box = await host.sampleVelocity(positions, { layer: 9 });
        assert(Math.abs(sphere[0] - .8) < .01 && Math.abs(sphere[1]) < .01 && Math.abs(sphere[2]) < .01,
            `Sphere center failed native target velocity: ${sphere}`);
        assert(Math.abs(box[4] + .6) < .01 && Math.abs(box[3]) < .01 && Math.abs(box[5]) < .01,
            `Box center failed native target velocity: ${box}`);
        assert(Math.abs(box[7] + .6) < .02, `Rotated box must extend along Y: ${box}`);
        assert(sphere.slice(3, 15).every(value => Math.abs(value) < 1e-5), `Sphere contaminated other layer or distant cells: ${sphere}`);
        assert(Math.abs(sphere[17] - .4) < .01 && Math.abs(sphere[15]) < .01 && Math.abs(sphere[16]) < .01,
            `Second sphere must preserve its own target: ${sphere}`);
        assert(box.slice(0, 3).every(value => Math.abs(value) < 1e-5) && box.slice(9).every(value => Math.abs(value) < 1e-5),
            `Box has unrotated X coverage or cross-layer leakage: ${box}`);
        assert(passCount(host, 'EmitterSimpleCS') > 0 && passCount(host, 'EmitterBoxCS') > 0, 'Both native emitter operators must execute');
        assert(passCount(host, 'PressureJacobiCS') === 0 && passCount(host, 'Vorticity2CS') === 0, 'Disabled layer controls executed');
        const other = await FlowHostWebGpu.create(module, device, shaderRoot, { maxBlocks: 16, cellSize: .1 });
        try {
            other.setScene({ layers: [{ ...layers[0], id: 0 }], emitters: emitters.map(emitter => ({ ...emitter, layer: 0, allocationScale: 0 })) });
            for (let i = 0; i < 4; i++) await other.step();
            assert(other.stats.activeBlocks === 0 && module._pr_flow_host_live() === 2 && host.stats.frames === 12,
                'Native scene contexts share state');
            const isolated = await host.sampleVelocity(positions, { layer: 3 });
            assert(isolated.every((value, index) => value === sphere[index]), 'Other context modified the first field');
        } finally { await other.dispose(); }

        const invalidScenes = [
            { layers: [], emitters },
            { layers: [...layers, layers[0]], emitters },
            { layers: [{ ...layers[0], id: 65536 }], emitters: [] },
            { layers: [{ ...layers[0], cellSize: 1e-50 }], emitters: [] },
            { layers, emitters: [...emitters, emitters[0]] },
            { layers, emitters: [{ ...emitters[0], layer: 77 }] },
            { layers, emitters: [{ ...emitters[0], type: 'mesh' }] },
            { layers, emitters: [{ ...emitters[0], id: 0 }] },
            { layers, emitters: [{ ...emitters[0], velocity: [1e300, 0, 0] }] },
            { layers, emitters: [{ ...emitters[0], quaternion: [0, 0, 0, 0] }] },
            { layers, emitters: [{ ...emitters[1], halfSize: [.1, -1, .1] }] },
            { layers, emitters: [{ ...emitters[0], enabled: 'false' }] },
        ];
        for (const invalid of invalidScenes) expectReject(() => host.setScene(invalid), 'Invalid scene accepted');
        expectReject(() => host.setEmitter({ enabled: false }), 'Legacy emitter mutated custom scene');
        expectReject(() => host.setControls({ pressure: true }), 'Legacy controls mutated custom scene');
        await expectAsyncReject(() => host.sampleVelocity(positions, { layer: 77 }), 'Unknown sample layer accepted');
        await expectAsyncReject(() => host.sampleVelocity(positions), 'Scene without layer 0 silently sampled the first layer');
        // Bypass JS validation: C++ must also reject the whole transaction after
        // reading a valid layer followed by an invalid emitter type.
        const pointer = module._malloc(160);
        assert(pointer, 'Native fixture allocation failed');
        try {
            assert(module._pr_flow_host_scene(0, pointer, 1, pointer + 32, 0) === 0, 'Invalid native handle accepted');
            module.HEAPU8.fill(0, pointer, pointer + 160);
            const base = pointer / 4;
            module.HEAPU32[base] = 42;
            module.HEAPF32[base + 1] = .1;
            module.HEAPU32[base + 8] = 999;
            module.HEAPU32[base + 9] = 42;
            module.HEAPU32[base + 10] = 77;
            assert(module._pr_flow_host_scene(host.handle, pointer, 1, pointer + 32, 1) === 0, 'Native invalid scene accepted');
        } finally { module._free(pointer); }
        const unchanged = await host.sampleVelocity(positions, { layer: 3 });
        assert(unchanged.every((value, index) => value === sphere[index]), 'Rejected scene changed existing field');
        await host.step();
        assert(host.output.layers.map(row => row.id).join(',') === meta.map(row => row.id).join(','), 'Rejected scene changed native layer configuration');

        const pending = host.step();
        expectReject(() => host.setScene(scene), 'Scene replacement raced a native frame');
        await pending;
        host.setScene({ layers, emitters: emitters.map(emitter => ({ ...emitter, allocationScale: 0,
            velocity: emitter.id === 101 ? [-.25, 0, 0] : emitter.velocity })) });
        await host.step();
        const noAllocation = await host.sampleVelocity(positions, { layer: 3 });
        assert(Math.abs(noAllocation[0] + .25) < .01, 'Zero allocation scale must still couple to existing cells');
        host.setScene(scene);
        await host.step();
        const sphereDispatches = passCount(host, 'EmitterSimpleCS'), boxDispatches = passCount(host, 'EmitterBoxCS');
        host.setScene({ layers, emitters: [] });
        for (let i = 0; i < 6; i++) await host.step();
        assert(passCount(host, 'EmitterSimpleCS') === sphereDispatches && passCount(host, 'EmitterBoxCS') === boxDispatches,
            'Removed emitters still dispatched');
        const residual = await host.sampleVelocity(positions, { layer: 3 });
        assert(residual.some(value => Math.abs(value) > .001), 'Removing emitters incorrectly cleared all existing momentum');
        host.setScene({ layers: [{ ...layers[1], pressure: true, combustion: true, vorticity: .6 }], emitters: [emitters[1]] });
        for (let i = 0; i < 4; i++) await host.step();
        assert(host.output.layers.length === 1 && host.output.layers[0].id === 9, 'Removed layer is still published');
        await expectAsyncReject(() => host.sampleVelocity(positions, { layer: 3 }), 'Removed layer remains sampleable');
        assert(passCount(host, 'PressureJacobiCS') > 0 && passCount(host, 'Vorticity2CS') > 0, 'Per-layer controls did not activate');
        const final = await host.sampleVelocity(positions, { layer: 9 });
        assert(final.every(Number.isFinite), 'Layer replacement produced nonfinite field');
        host.setScene({ layers: [{ ...layers[0], id: 0 }], emitters: [{ ...emitters[0], layer: 0 }] });
        for (let i = 0; i < 4; i++) await host.step();
        const defaultLayer = await host.sampleVelocity(positions);
        const explicitDefault = await host.sampleVelocity(positions, { layer: 0 });
        assert(defaultLayer.every((value, index) => value === explicitDefault[index]), 'Custom default must sample layer 0');
        assert(Math.abs(defaultLayer[0] - .8) < .01, 'Default layer 0 lost native emitter');
        result = { flowSceneAbi: 1, layers: meta, sphereVelocity: Array.from(sphere), rotatedBoxVelocity: Array.from(box),
            sourceRemovalResidual: Array.from(residual), invalidSceneCases: invalidScenes.length,
            nativeAtomicRejection: 'PASS', layerRemoval: 'PASS', layerControls: 'PASS', busyMutation: 'PASS',
            defaultLayerSelection: 'PASS', multilayerVelocityIsolation: 'PASS', rotatedBoxCoverage: 'PASS',
            emitterRemoval: 'PASS', zeroAllocationScale: 'PASS',
            stats: structuredClone(host.stats) };
    } finally { await host.dispose(); }
    assert(module._pr_flow_host_live() === 0 && host.stats.allocatedBytes === 0 && host.resources.size === 0, 'Native scene resources leaked');
    await host.dispose();
    return { ...result, cleanup: 'PASS', cases: [
        'sphere-box-multiple-emitters', 'multiple-layer-velocity-isolation', 'rotated-box-coverage',
        'scene-transaction-rejection', 'independent-contexts', 'layer-controls-removal',
        'source-removal', 'default-layer-selection', 'zero-allocation-scale', 'cleanup',
    ].map(name => ({ name, status: 'PASS' })) };
}
