// SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
// SPDX-License-Identifier: MIT
import {readArtifacts, instantiateVerified, decodeVersion} from './suite.mjs';
let P, foundation, physics, scene, material, ground, ball, timer;
let time = 0;
const owned = [];
const diagnostics = [];
const require = (condition, message) => {if (!condition) throw new Error(message);};
const make = (name, ...arguments_) => {const value = new P[name](...arguments_); owned.push(value); return value;};
const vector = (x, y, z) => make('PxVec3', x, y, z);

function snapshot(status) {
  self.postMessage({status, time, y: ball.getGlobalPose().get_p().get_y(), velocityY: ball.getLinearVelocity().get_y(), runtimeVersion: decodeVersion(P.PHYSICS_VERSION)});
}
function pause() {clearInterval(timer); timer = null;}
function close() {
  pause();
  for (const value of [ball, ground, scene, material, physics]) value?.release();
  ball = ground = scene = material = physics = null;
  for (let index = owned.length - 1; index >= 2; index--) P.destroy(owned[index]);
  owned.length = Math.min(owned.length, 2);
  foundation?.release(); foundation = null;
  for (let index = owned.length - 1; index >= 0; index--) P.destroy(owned[index]);
  owned.length = 0;
}
function resume() {
  if (timer || !scene) return;
  snapshot('RUNNING');
  timer = setInterval(() => {
    try {
      require(scene.simulate(1 / 60) !== false, 'PhysX simulate failed');
      require(scene.fetchResults(true), 'PhysX fetchResults failed');
      time += 1 / 60;
      require(Number.isFinite(ball.getGlobalPose().get_p().get_y()), 'Non-finite native position');
      require(diagnostics.length === 0, diagnostics.join('\n'));
      snapshot('RUNNING');
    } catch (error) {pause(); self.postMessage({status: 'FAILED', error: String(error)});}
  }, 1000 / 60);
}

try {
  P = await instantiateVerified(await readArtifacts('candidate'), {print: () => {}, printErr: (...values) => diagnostics.push(values.join(' '))});
  require(decodeVersion(P.PHYSICS_VERSION) === '5.11.0', 'Expected PhysX SDK 5.11.0');
  const allocator = make('PxDefaultAllocator'), errors = make('PxDefaultErrorCallback');
  foundation = P.CreateFoundation(P.PHYSICS_VERSION, allocator, errors);
  require(P.getPointer(foundation), 'Could not create PhysX foundation');
  const scale = make('PxTolerancesScale');
  physics = P.CreatePhysics(P.PHYSICS_VERSION, foundation, scale);
  require(P.getPointer(physics), 'Could not create PhysX physics');
  const description = make('PxSceneDesc', scale);
  description.set_gravity(vector(0, -9.81, 0));
  const dispatcher = P.DefaultCpuDispatcherCreate(0); owned.push(dispatcher);
  description.set_cpuDispatcher(dispatcher); description.set_filterShader(P.DefaultFilterShader());
  scene = physics.createScene(description);
  require(P.getPointer(scene), 'Could not create PhysX scene');
  material = physics.createMaterial(0.5, 0.5, 0.45);
  const flags = make('PxShapeFlags', P.PxShapeFlagEnum.eSCENE_QUERY_SHAPE | P.PxShapeFlagEnum.eSIMULATION_SHAPE);
  const filter = make('PxFilterData', 1, 1, 0, 0);
  function actor(dynamic, height, geometry) {
    const pose = make('PxTransform', P.PxIDENTITYEnum.PxIdentity); pose.set_p(vector(0, height, 0));
    const body = dynamic ? physics.createRigidDynamic(pose) : physics.createRigidStatic(pose);
    require(P.getPointer(body), 'Could not create rigid body');
    const shape = physics.createShape(geometry, material, true, flags);
    require(P.getPointer(shape), 'Could not create collision shape');
    shape.setSimulationFilterData(filter);
    require(body.attachShape(shape), 'Could not attach collision shape'); shape.release();
    if (dynamic) {body.setMass(1); body.setMassSpaceInertiaTensor(vector(0.025, 0.025, 0.025));}
    require(scene.addActor(body), 'Could not add rigid body'); return body;
  }
  ground = actor(false, -0.5, make('PxBoxGeometry', 5, 0.5, 5));
  ball = actor(true, 3, make('PxSphereGeometry', 0.25));
  require(diagnostics.length === 0, diagnostics.join('\n'));
  self.onmessage = ({data}) => {
    try {
      if (data.type === 'pause') {pause(); snapshot('PAUSED');}
      else if (data.type === 'resume') resume();
      else if (data.type === 'close') {close(); require(diagnostics.length === 0, diagnostics.join('\n')); self.postMessage({status: 'CLOSED', ownedWrappers: owned.length});}
    } catch (error) {pause(); self.postMessage({status: 'FAILED', error: String(error)});}
  };
  resume();
} catch (error) {pause(); self.postMessage({status: 'FAILED', error: String(error)});}
