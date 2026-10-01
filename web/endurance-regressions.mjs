// SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
// SPDX-License-Identifier: MIT
// Separate bounded endurance lane; does not change the frozen functional suite.
import {readArtifacts, instantiateVerified, digest, decodeVersion} from './suite.mjs';
import {PhysXBulk} from '../bridge/physx-bulk.mjs';
const assert = (ok, message) => { if (!ok) throw new Error(message); };

export async function runEndurance({cycles = 256, physicalSeconds = 600} = {}, progress = () => {}) {
  assert(Number.isInteger(cycles) && cycles >= 64 && cycles <= 4096, 'cycles must be 64–4096');
  assert(Number.isInteger(physicalSeconds) && physicalSeconds >= 60 && physicalSeconds <= 3600, 'physicalSeconds must be 60–3600');
  const report = {status: 'RUNNING', startedUtc: new Date().toISOString(), tests: [], stderr: [],
    physicsExecuted: false, releaseApproved: false, engineIntegrationVerified: false,
    scope: 'Bounded native CPU-WASM ownership and identical-input replay on this browser/device; no full allocator leak or cross-platform determinism proof',
    notRun: ['Android device', 'Firefox', 'Safari', 'pthread builds', 'unbounded endurance', 'full allocator accounting', 'GPU Flow execution in this CPU lane']};
  const test = async (name, run) => {
    const start = performance.now();
    try { const detail = await run(); const row = {name, status: 'PASS', ms: performance.now() - start, detail}; report.tests.push(row); progress(row); return detail; }
    catch (error) { report.tests.push({name, status: 'FAIL', ms: performance.now() - start, error: String(error.stack ?? error)}); throw error; }
  };
  let P, foundation, physics, material;
  const sdkValues = [], worlds = new Set();
  const create = (list, name, ...args) => { const value = new P[name](...args); list.push(value); return value; };
  const destroyValues = list => { for (let i = list.length - 1; i >= 0; i--) P.destroy(list[i]); list.length = 0; };
  const live = () => ({blastFamilies: P._pr_blast_live_families(), blastAuthoringResults: P._pr_blast_authoring_live_results(), flowHosts: P._pr_flow_host_live()});
  const allocatorSample = () => {
    const sizes = [16, 256, 4096, 65536, 1048576], allocated = [];
    try {
      for (const bytes of sizes) { const ptr = P._malloc(bytes); assert(ptr > 0, 'Native allocation probe failed'); allocated.push({bytes, pointer: ptr}); }
      return {heapBytes: P.HEAPU8.byteLength, allocations: allocated.map(x => ({...x}))};
    } finally { for (let i = allocated.length - 1; i >= 0; i--) P._free(allocated[i].pointer); }
  };
  const createWorld = (bodyCount, seed = 1337) => {
    const values = [], actors = [], state = {values, actors, scene: null, bulk: null, dispatcher: null}; worlds.add(state);
    const make = (name, ...args) => create(values, name, ...args);
    const vec = (x = 0, y = 0, z = 0) => make('PxVec3', x, y, z);
    const pose = (x, y, z) => { const p = make('PxTransform', P.PxIDENTITYEnum.PxIdentity); p.set_p(vec(x, y, z)); return p; };
    const desc = make('PxSceneDesc', sdkValues[2]); desc.set_gravity(vec(0, -9.81, 0));
    state.dispatcher = P.DefaultCpuDispatcherCreate(0); desc.set_cpuDispatcher(state.dispatcher); desc.set_filterShader(P.DefaultFilterShader());
    state.scene = physics.createScene(desc); assert(P.getPointer(state.scene), 'Native endurance scene creation failed');
    const flags = make('PxShapeFlags', P.PxShapeFlagEnum.eSIMULATION_SHAPE | P.PxShapeFlagEnum.eSCENE_QUERY_SHAPE);
    const filter = make('PxFilterData', 1, 1, 0, 0);
    const add = (dynamic, location, half) => {
      const actor = dynamic ? physics.createRigidDynamic(pose(...location)) : physics.createRigidStatic(pose(...location));
      assert(P.getPointer(actor), 'Native endurance actor creation failed'); actors.push(actor);
      const shape = physics.createShape(make('PxBoxGeometry', ...half), material, true, flags);
      assert(P.getPointer(shape), 'Native endurance shape creation failed');
      try { shape.setSimulationFilterData(filter); assert(actor.attachShape(shape), 'Native endurance attachShape failed'); } finally { shape.release(); }
      if (dynamic) { actor.setMass(1); actor.setMassSpaceInertiaTensor(vec(.0816666667, .0816666667, .0816666667)); actor.setLinearDamping(.01); }
      assert(state.scene.addActor(actor), 'Native endurance addActor failed'); return actor;
    };
    add(false, [0, -.5, 0], [5000, .5, 5000]);
    let randomState = seed >>> 0;
    const random = () => { randomState ^= randomState << 13; randomState ^= randomState >>> 17; randomState ^= randomState << 5; return (randomState >>> 0) / 4294967296; };
    for (let i = 0; i < bodyCount; i++) {
      const actor = add(true, [(i % 4) * 1.5 - 2.25, 1.5 + .4 * (i % 3), Math.floor(i / 4) * 1.5], [.35, .35, .35]);
      actor.setLinearVelocity(vec((random() - .5) * 2, 0, (random() - .5) * 2));
      actor.setAngularVelocity(vec(random(), random(), random()));
    }
    state.bulk = new PhysXBulk(P, {capacity: bodyCount});
    for (let i = 0; i < bodyCount; i++) state.bulk.add(i + 1, actors[i + 1]);
    state.impulses = [vec(.4, 2.5, .2), vec(-.4, 2.5, -.2)];
    state.step = () => { state.scene.simulate(1 / 120); assert(state.scene.fetchResults(true), 'Native endurance fetchResults failed'); report.physicsExecuted = true; };
    state.count = () => state.scene.getNbActors(make('PxActorTypeFlags', P.PxActorTypeFlagEnum.eRIGID_STATIC | P.PxActorTypeFlagEnum.eRIGID_DYNAMIC));
    state.close = () => {
      if (!worlds.has(state)) return;
      state.bulk?.dispose(); state.bulk = null;
      for (const actor of actors) actor.release(); actors.length = 0;
      const remaining = state.count(); assert(remaining === 0, 'Native endurance scene retained actors');
      state.scene.release(); state.scene = null; P.destroy(state.dispatcher); state.dispatcher = null;
      destroyValues(values); worlds.delete(state); return remaining;
    };
    return state;
  };
  try {
    const artifacts = await readArtifacts('candidate');
    P = await instantiateVerified(artifacts, {print: () => {}, printErr: (...args) => report.stderr.push(args.join(' '))});
    report.artifactHashes = artifacts.manifest.artifacts;
    assert(decodeVersion(P.PHYSICS_VERSION) === '5.11.0', 'Wrong native SDK');
    const allocator = create(sdkValues, 'PxDefaultAllocator'), errors = create(sdkValues, 'PxDefaultErrorCallback');
    foundation = P.CreateFoundation(P.PHYSICS_VERSION, allocator, errors);
    const tolerances = create(sdkValues, 'PxTolerancesScale'); physics = P.CreatePhysics(P.PHYSICS_VERSION, foundation, tolerances);
    assert(P.getPointer(foundation) && P.getPointer(physics), 'Native endurance SDK initialization failed');
    material = physics.createMaterial(.4, .4, .25);
    await test('Repeated native scenes, actors, bulk contexts and Blast ownership', () => {
      const warmupCycles = 32, samples = [], activeCounts = [], bulkFailures = [], baselineLive = live();
      assert(Object.values(baselineLive).every(n => n === 0), 'Fresh native module has live addon resources');
      for (let cycle = 0; cycle < warmupCycles + cycles; cycle++) {
        const world = createWorld(16);
        try {
          assert(world.count() === 17, 'Unexpected native active actor count');
          for (let step = 0; step < 16; step++) world.step();
          const snapshot = world.bulk.snapshot({copy: true}); assert(snapshot.count === 16 && snapshot.poses.every(Number.isFinite), 'Invalid native lifecycle snapshot');
          const handle = P._pr_bulk_create(2); assert(handle > 0, 'Native extra bulk context failed');
          assert(P._pr_bulk_add(handle, 1, P.getPointer(world.actors[1])) === 0, 'Native bulk actor registration failed');
          assert(P._pr_bulk_snapshot(handle) === 0 && P._pr_bulk_count(handle) === 1, 'Native bulk context did not publish');
          assert(P._pr_bulk_destroy(handle) === 0, 'Native bulk context release failed');
          const retired = P._pr_bulk_snapshot(handle); bulkFailures.push(retired); assert(retired === -1, 'Destroyed native context remained callable');
          assert(P._pr_blast_smoke() === 0, 'Native Blast fracture/split lifecycle failed');
        } finally { activeCounts.push(world.close()); }
        assert(JSON.stringify(live()) === JSON.stringify(baselineLive), 'Native Blast/Flow ownership changed after lifecycle');
        if (cycle >= warmupCycles) samples.push(allocatorSample());
        if ((cycle + 1) % 64 === 0) progress({name: 'lifecycle progress', cycle: cycle + 1, total: warmupCycles + cycles});
      }
      const tail = samples.slice(Math.floor(samples.length / 2));
      const bySize = tail[0].allocations.map((p, i) => ({bytes: p.bytes, addresses: [...new Set(tail.map(s => s.allocations[i].pointer))]}));
      const heapSizes = [...new Set(tail.map(s => s.heapBytes))];
      assert(heapSizes.length === 1, 'WASM memory continued growing in final half of bounded lifecycle run');
      assert(bySize.every(p => p.addresses.length <= 4), 'Native same-size allocation probes did not reach bounded address reuse');
      return {cycles, warmupCycles, scenesCreatedAndReleased: cycles + warmupCycles, dynamicActorsCreatedAndReleased: (cycles + warmupCycles) * 16,
        bulkContextsCreatedAndReleased: (cycles + warmupCycles) * 2, blastFractureSmokeRuns: cycles + warmupCycles,
        maximumActiveActors: 17, activeActorsAfterEveryClose: [...new Set(activeCounts)], retiredBulkStatus: [...new Set(bulkFailures)], liveBefore: baselineLive, liveAfter: live(),
        allocationProbeSamples: samples.length, tailSamples: tail.length, allocationReuse: bySize, wasmHeapBytesTail: heapSizes,
        limitation: 'Stable probe addresses and WASM memory high-water are bounded reuse observations; no mallinfo or full native allocator leak accounting is exposed.'};
    });
    await test('Fixed physical-time replay remains finite and repeatable', async () => {
      const bodies = 8, dt = 1 / 120, steps = physicalSeconds * 120, sampleEvery = 30, runs = [];
      const input = {seed: 1337, bodies, dt, steps, sampleEvery, initialLayout: '1.5 m four-column grid', impulseEverySteps: 120,
        impulses: [[.4, 2.5, .2], [-.4, 2.5, -.2]], impulseBody: 'floor(step/120) mod 8', impulseChoice: 'floor(step/120) mod 2'};
      for (let run = 0; run < 2; run++) {
        const world = createWorld(bodies), samples = new Float32Array(steps / sampleEvery * bodies * 7); let offset = 0, maxQuaternionError = 0;
        try {
          for (let step = 0; step < steps; step++) {
            if (step % 120 === 0) world.actors[1 + Math.floor(step / 120) % bodies].addForce(world.impulses[Math.floor(step / 120) % 2], P.PxForceModeEnum.eIMPULSE, true);
            world.step();
            if ((step + 1) % sampleEvery === 0) {
              const snapshot = world.bulk.snapshot({copy: true}); assert(snapshot.poses.every(Number.isFinite), 'Replay generated nonfinite native poses');
              for (let b = 0; b < bodies; b++) {
                const q = snapshot.poses.subarray(b * 7 + 3, b * 7 + 7), error = Math.abs(Math.hypot(...q) - 1); maxQuaternionError = Math.max(maxQuaternionError, error);
                assert(error < .005 && snapshot.poses[b * 7 + 1] > -.1, 'Native replay quaternion/ground position drifted out of tolerance');
              }
              samples.set(snapshot.poses, offset); offset += snapshot.poses.length;
            }
            if ((step + 1) % 12000 === 0) progress({name: 'replay progress', run: run + 1, physicalSeconds: (step + 1) * dt, totalPhysicalSeconds: physicalSeconds});
          }
          assert(offset === samples.length, 'Physical replay sampling count incorrect');
          runs.push({samples, maxQuaternionError, remainingActors: world.close()});
        } finally { world.close(); }
      }
      const a = new Uint8Array(runs[0].samples.buffer), b = new Uint8Array(runs[1].samples.buffer); let differentBytes = 0, maxPoseDelta = 0;
      for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) differentBytes++;
      for (let i = 0; i < runs[0].samples.length; i++) maxPoseDelta = Math.max(maxPoseDelta, Math.abs(runs[0].samples[i] - runs[1].samples[i]));
      assert(differentBytes === 0 && maxPoseDelta === 0, `Identical same-build replay diverged: ${differentBytes} bytes, ${maxPoseDelta}`);
      return {physicalSecondsPerRun: physicalSeconds, stepsPerRun: steps, sampleCountPerRun: steps / sampleEvery, sampledComponents: runs[0].samples.length,
        input, inputSha256: await digest(new TextEncoder().encode(JSON.stringify(input))), trajectorySha256: await digest(a),
        trajectoryBytes: a.length, differentBytes, maxPoseDelta, maxQuaternionError: Math.max(...runs.map(r => r.maxQuaternionError)),
        remainingActors: runs.map(r => r.remainingActors), allocationAfterReplay: allocatorSample(), scope: 'Two identical fixed-input runs on this exact native build/browser/device only'};
    });
    await test('Bounded endurance native teardown', () => {
      assert(worlds.size === 0, 'Endurance world owner retained a scene');
      const finalLive = live(); assert(Object.values(finalLive).every(n => n === 0), 'Endurance addons retained live resources');
      material.release(); material = null; physics.release(); physics = null;
      P.destroy(sdkValues.pop()); foundation.release(); foundation = null; destroyValues(sdkValues);
      assert(report.stderr.length === 0, report.stderr.join('\n'));
      return {ownedScenes: 0, addonLiveCounters: finalLive, sdkReleased: true, sdkStderr: 0, workerTerminationStillRequired: true};
    });
    report.status = 'BOUNDED_CPU_ENDURANCE_PASSED';
  } catch (error) { report.status = 'FAILED'; report.error = String(error.stack ?? error); }
  finally {
    // Successful paths prove explicit release; failed setup is additionally
    // discarded by the driver terminating its private Worker, never reused.
    report.finishedUtc = new Date().toISOString();
  }
  return report;
}
