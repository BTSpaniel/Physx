// SPDX-License-Identifier: MIT
// Integration evidence using the game's unmodified authoritative playground solver.
import {FlowWasmWebGpuBridge} from './webgpu_bridge.mjs';
import {runAdvectionSmoke} from './advection_smoke.mjs';
import {createSphFluidModel} from '/tests/playground/src/demos/sphFluid/model.js';
import {buildSphFluidShaderSources} from '/tests/playground/src/demos/sphFluid/shaders.js';
import {createSphFluidSimulation} from '/tests/playground/src/demos/sphFluid/simulation.js';
import {WasmComputeProvider} from '/engine/core/compute/WasmComputeProvider.js';

function check(value, message) { if (!value) throw new Error(message); }

export async function runSphPlaygroundSmoke(module, {frames = 60} = {}) {
  check(Number.isInteger(frames) && frames >= 1 && frames <= 600, 'Invalid frame count');
  const adapter = await navigator.gpu.requestAdapter({powerPreference: 'high-performance'});
  check(adapter && !adapter.info.isFallbackAdapter, 'Hardware WebGPU adapter required');
  const device = await adapter.requestDevice({requiredFeatures: ['float32-filterable'],
    requiredLimits: {maxStorageBuffersPerShaderStage: 10}});
  const errors = [];
  device.addEventListener('uncapturederror', event => errors.push(event.error.message));
  const bridge = await FlowWasmWebGpuBridge.create(module, {adapter, device});
  const provider = new WasmComputeProvider({maxWorkers: 1});
  let simulation;
  const cases = [];
  async function record(name, operation) {
    const result = await operation();
    const row = {name, status: 'PASS', ...result};
    cases.push(row);
    console.log('SPH port ' + JSON.stringify(row));
    return result;
  }
  try {
    const model = createSphFluidModel();
    const bytes = model.config.initialParticleCount * 16;
    const pointer = bridge.allocate(bytes);
    const mirror = bridge.createStorageBuffer(bytes, 0, 'SPH candidate heap mirror');
    simulation = await createSphFluidSimulation({device, model,
      shaders: buildSphFluidShaderSources(model), ctx: {async makeShader(code, label) {
        const shader = device.createShaderModule({code, label});
        const failures = (await shader.getCompilationInfo()).messages.filter(m => m.type === 'error');
        check(!failures.length, failures.map(m => m.message).join('\n'));
        return shader;
      }}});
    const compare = await bridge.createComputePipeline(`
      @group(0) @binding(0) var<storage, read> source: array<u32>;
      @group(0) @binding(1) var<storage, read> copy: array<u32>;
      @group(0) @binding(2) var<storage, read_write> errors: atomic<u32>;
      @compute @workgroup_size(128)
      fn main(@builtin(global_invocation_id) id: vec3u) {
        if (id.x < arrayLength(&copy) && source[id.x] != copy[id.x]) {
          atomicAdd(&errors, 1u);
        }
      }`, 'main', 'SPH byte-exact comparison');
    const mismatch = bridge.createStorageBuffer(4);
    const mismatchPointer = bridge.allocate(4);
    async function snapshot(buffer) {
      await bridge.download(buffer, pointer, bytes);
      return module.HEAPU8.slice(pointer, pointer + bytes);
    }
    async function roundTrip(buffer) {
      const started = performance.now();
      const original = await snapshot(buffer);
      bridge.upload(mirror, pointer, bytes);
      device.queue.writeBuffer(mismatch, 0, new Uint32Array([0]));
      bridge.dispatch(compare, [buffer, mirror, mismatch].map((buffer, binding) =>
        ({binding, resource: {buffer}})), Math.ceil(bytes / 4 / 128));
      await bridge.download(mismatch, mismatchPointer, 4);
      const mismatches = new DataView(module.HEAPU8.buffer).getUint32(mismatchPointer, true);
      check(mismatches === 0, `SPH transfer corrupted ${mismatches} words`);
      return {original, byteLength: bytes, mismatches, completedWallMs: performance.now() - started};
    }
    const initial = await snapshot(simulation.getCurrentPositionBuffer());
    await record('initial-state', async () => {
      const expected = new Uint8Array(model.positions.buffer, model.positions.byteOffset, bytes);
      check(initial.every((value, index) => value === expected[index]), 'Initial GPU state differs from model');
      return {particles: model.config.initialParticleCount, bytes};
    });
    async function advance(count, membrane = {sealed: true}) {
      const started = performance.now();
      for (let i = 0; i < count; i++) {
        const encoder = device.createCommandEncoder();
        const frame = simulation.encodeFrame(encoder, {frameDt: model.config.fixedStep, membrane});
        check(frame.substeps === 1, 'Expected one fixed PBF step');
        device.queue.submit([encoder.finish()]);
        await device.queue.onSubmittedWorkDone();
      }
      return {frames: count, completedWallMs: performance.now() - started};
    }
    await record('sealed-pbf-baseline', async () => {
      const timing = await advance(frames);
      const positions = new Float32Array((await snapshot(simulation.getCurrentPositionBuffer())).buffer);
      const velocities = new Float32Array((await snapshot(simulation.getCurrentVelocityBuffer())).buffer);
      let moved = 0, maximumSpeed = 0;
      const before = new Float32Array(initial.buffer);
      for (let i = 0; i < positions.length; i += 4) {
        check(positions.subarray(i, i + 4).every(Number.isFinite)
          && velocities.subarray(i, i + 4).every(Number.isFinite), 'Nonfinite live SPH state');
        check(positions[i + 3] >= 0, 'Sealed particle escaped');
        for (let axis = 0; axis < 3; axis++) {
          check(Math.abs(positions[i + axis]) < model.config.maximumFieldHalfExtents[axis] + 1,
            'Sealed particle escaped spatial envelope');
        }
        if (positions[i + 1] !== before[i + 1]) moved++;
        maximumSpeed = Math.max(maximumSpeed, Math.hypot(...velocities.subarray(i, i + 3)));
      }
      check(moved > 0, 'SPH did not advance');
      check(maximumSpeed <= model.config.maximumSpeed + 0.001, 'SPH speed cap exceeded');
      return {...timing, moved, maximumSpeed, stats: simulation.getStats(), timingKind: 'CPU submit plus GPU completion; rendering excluded'};
    });
    for (const [name, buffer] of [['positions', simulation.getCurrentPositionBuffer()],
      ['velocities', simulation.getCurrentVelocityBuffer()]]) {
      await record(name + '-gpu-wasm-gpu', async () => {
        const {original, ...receipt} = await roundTrip(buffer);
        return receipt;
      });
    }
    await record('game-existing-wasm-simd', async () => {
      const current = new Float32Array((await snapshot(simulation.getCurrentPositionBuffer())).buffer);
      const positions = new Float64Array(model.config.initialParticleCount * 3);
      for (let i = 0; i < positions.length / 3; i++) positions.set(current.subarray(i * 4, i * 4 + 3), i * 3);
      const matrix = new Float64Array([2,0,0,0, 0,0.5,0,0, 0,0,-1,0, 1,-2,3,1]);
      const result = await provider.execute('sph-port', {
        operation: 'math.geometry.transform-points@1', inputs: {positions, matrix},
        parameters: {}, policy: {precision: 'f64'},
      }, 'wasm-simd');
      const transformed = result.outputs.positions;
      let maximumError = 0;
      for (let i = 0; i < positions.length; i += 3) {
        const expected = [positions[i] * 2 + 1, positions[i + 1] * 0.5 - 2, -positions[i + 2] + 3];
        for (let axis = 0; axis < 3; axis++) {
          check(Number.isFinite(transformed[i + axis]), 'Nonfinite game SIMD result');
          maximumError = Math.max(maximumError, Math.abs(transformed[i + axis] - expected[axis]));
        }
      }
      check(maximumError < 1e-10, 'Existing game SIMD transform differs from oracle');
      return {checked: positions.length, maximumError, metrics: result.metrics,
        separateWorkerHeap: true, backend: 'wasm-simd'};
    });
    await record('blast-in-candidate-heap', async () => {
      check(module._pr_blast_version() === 50006 && module._pr_blast_smoke() === 0, 'Blast fracture failed');
      return {version: 50006, fractureSplitPassed: true};
    });
    await record('flow-advection-same-device', () => runAdvectionSmoke(module,
      '/port/dist/flow-wgsl', {adapter, device}));
    await record('pbf-after-flow-resource-cleanup', () => advance(2));
    await record('reset-restores-original-bytes', async () => {
      simulation.reset();
      const encoder = device.createCommandEncoder();
      check(simulation.encodeFrame(encoder, {paused: true}).resetApplied, 'Reset not applied');
      device.queue.submit([encoder.finish()]);
      const reset = await snapshot(simulation.getCurrentPositionBuffer());
      check(reset.every((value, index) => value === initial[index]), 'Reset changed initial positions');
      const velocities = await snapshot(simulation.getCurrentVelocityBuffer());
      check(velocities.every(value => value === 0), 'Reset did not restore resting velocities');
      return {checkedBytes: reset.length + velocities.length};
    });
    await device.queue.onSubmittedWorkDone();
    check(errors.length === 0, errors.join('\n'));
    return {status: 'SPH_PLAYGROUND_PORT_INTEGRATION_PASSED', cases,
      adapter: {vendor: adapter.info.vendor, architecture: adapter.info.architecture,
        isFallbackAdapter: adapter.info.isFallbackAdapter}, errors,
      scope: 'Actual playground solver, transfer and separate game SIMD integration; Flow host graph and visual rendering not covered'};
  } finally {
    simulation?.destroy(); provider.dispose(); bridge.close(); device.destroy();
  }
}
