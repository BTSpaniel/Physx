// SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
// SPDX-License-Identifier: MIT
// Actual CPU PhysX behavior. No substitute solver and no feature skips.
const assert = (ok, message) => { if (!ok) throw new Error(message); };
const near = (actual, expected, tolerance, message) => assert(Number.isFinite(actual) && Math.abs(actual - expected) <= tolerance, `${message}: ${actual}, expected ${expected} ± ${tolerance}`);
export const advancedRegressionNames = [
  'Addon declarations match compiled exports',
  'D6 constrained translation and drive',
  'Reduced-coordinate articulation drive',
  'Capsule controller ground and wall collision',
  'Convex and triangle mesh cooking roundtrip',
  'Binary collection serialization roundtrip',
  'Same-build deterministic rigid replay',
];
export async function runAdvancedRegressions({P, physics, scene, material, make, step, test}) {
  const vec = (x = 0, y = 0, z = 0) => make('PxVec3', x, y, z);
  const pose = (x = 0, y = 0, z = 0) => { const value = make('PxTransform', P.PxIDENTITYEnum.PxIdentity); value.set_p(vec(x, y, z)); return value; };
  const components = actor => { const t = actor.getGlobalPose(), p = t.get_p(), q = t.get_q(); return [p.get_x(), p.get_y(), p.get_z(), q.get_x(), q.get_y(), q.get_z(), q.get_w()]; };
  const shapeFlags = make('PxShapeFlags', P.PxShapeFlagEnum.eSIMULATION_SHAPE | P.PxShapeFlagEnum.eSCENE_QUERY_SHAPE);
  const attach = (actor, geometry) => {
    const shape = physics.createShape(geometry, material, true, shapeFlags);
    assert(P.getPointer(shape), 'Native shape creation failed');
    try { assert(actor.attachShape(shape), 'Native attachShape failed'); } finally { shape.release(); }
  };
  const box = (x, y, z, dynamic = true, size = [.25, .25, .25]) => {
    const actor = dynamic ? physics.createRigidDynamic(pose(x, y, z)) : physics.createRigidStatic(pose(x, y, z));
    assert(P.getPointer(actor), 'Native actor creation failed');
    try {
      attach(actor, make('PxBoxGeometry', ...size));
      if (dynamic) { actor.setMass(1); actor.setMassSpaceInertiaTensor(vec(.1, .1, .1)); actor.setLinearDamping(0); actor.setAngularDamping(0); }
      assert(scene.addActor(actor), 'Native actor registration failed'); return actor;
    } catch (error) { actor.release(); throw error; }
  };
  await test(advancedRegressionNames[0], async () => {
    const response = await fetch(new URL('../types/addon-abi.json', import.meta.url), {cache: 'no-store'});
    assert(response.ok, 'Addon declaration metadata missing');
    const abi = await response.json(), entries = Object.entries(abi.exports ?? {});
    assert(abi.pointerModel === 'wasm32' && entries.length > 40, 'Addon declaration corpus incomplete');
    for (const [name] of entries) assert(typeof P[name] === 'function', `Declared export absent from runtime: ${name}`);
    return {exports: entries.length, pointerModel: abi.pointerModel, scope: 'Function presence, not a complete native ABI proof'};
  });
  await test(advancedRegressionNames[1], () => {
    const anchor = box(8, 8, 0, false), actor = box(8, 8, 0); let joint;
    try {
      joint = P.D6JointCreate(physics, anchor, pose(), actor, pose());
      assert(P.getPointer(joint), 'D6JointCreate failed');
      joint.setMotion(P.PxD6AxisEnum.eX, P.PxD6MotionEnum.eFREE);
      actor.setLinearVelocity(vec(2, 3, 0), true); step(60);
      const constrained = components(actor);
      assert(constrained[0] > 8.8, 'D6 free X axis did not move'); near(constrained[1], 8, .02, 'D6 locked Y axis');
      joint.setDrive(P.PxD6DriveEnum.eX, make('PxD6JointDrive', 500, 50, 10000, false));
      joint.setDrivePosition(pose(.5, 0, 0), true); step(180);
      const driven = components(actor); near(driven[0], 8.5, .04, 'D6 X drive target');
      return {constrained, driven, targetX: 8.5};
    } finally { joint?.release(); actor.release(); anchor.release(); }
  });
  await test(advancedRegressionNames[2], () => {
    const articulation = physics.createArticulationReducedCoordinate();
    assert(P.getPointer(articulation), 'Articulation creation failed');
    try {
      articulation.setArticulationFlag(P.PxArticulationFlagEnum.eFIX_BASE, true);
      const root = articulation.createLink(P.wrapPointer(0, P.PxArticulationLink), pose(-8, 8, 0));
      const child = articulation.createLink(root, pose(-8, 9, 0));
      assert(P.getPointer(root) && P.getPointer(child), 'Articulation link creation failed');
      for (const link of [root, child]) { attach(link, make('PxBoxGeometry', .2, .2, .2)); link.setMass(1); link.setMassSpaceInertiaTensor(vec(.1, .1, .1)); }
      const joint = child.getInboundJoint(), axis = P.PxArticulationAxisEnum.eTWIST;
      joint.setJointType(P.PxArticulationJointTypeEnum.eREVOLUTE);
      joint.setParentPose(pose(0, .5, 0)); joint.setChildPose(pose(0, -.5, 0));
      joint.setMotion(axis, P.PxArticulationMotionEnum.eFREE);
      joint.setDriveParams(axis, make('PxArticulationDrive', 100, 10, 10000, P.PxArticulationDriveTypeEnum.eFORCE));
      joint.setDriveTarget(axis, .5, true);
      assert(scene.addArticulation(articulation), 'Scene articulation registration failed'); step(240);
      const angle = joint.getJointPosition(axis), fixed = articulation.getRootGlobalPose().get_p();
      near(angle, .5, .03, 'Articulation drive angle'); near(fixed.get_y(), 8, .001, 'Fixed articulation root');
      assert(articulation.getNbLinks() === 2 && articulation.getDofs() === 1, 'Articulation topology/DOFs wrong');
      return {links: 2, dofs: 1, angle, target: .5};
    } finally { articulation.release(); }
  });
  await test(advancedRegressionNames[3], () => {
    const wall = box(4, 1.5, 5, false, [.2, 1.5, 2]), manager = P.CreateControllerManager(scene, false); let controller;
    assert(P.getPointer(manager), 'Controller manager creation failed');
    try {
      const desc = make('PxCapsuleControllerDesc'); desc.set_radius(.3); desc.set_height(1); desc.set_material(material);
      desc.set_position(make('PxExtendedVec3', 0, 3, 5)); desc.set_contactOffset(.02); desc.set_stepOffset(.1);
      assert(desc.isValid(), 'Capsule controller descriptor invalid'); controller = manager.createController(desc);
      assert(P.getPointer(controller), 'Capsule controller creation failed');
      const filters = make('PxControllerFilters'); let bottom = false;
      // move returns a borrowed native value cache; destroying it aborts WASM.
      for (let i = 0; i < 120; i++) { const flags = controller.move(vec(0, -.1, 0), .00001, 1 / 60, filters); bottom ||= flags.isSet(P.PxControllerCollisionFlagEnum.eCOLLISION_DOWN); }
      assert(bottom, 'Controller never reported ground collision'); near(controller.getFootPosition().get_y(), 0, .06, 'Controller foot contact');
      let side = false;
      for (let i = 0; i < 120; i++) { const flags = controller.move(vec(.1, -.01, 0), .00001, 1 / 60, filters); side ||= flags.isSet(P.PxControllerCollisionFlagEnum.eCOLLISION_SIDES); }
      const x = controller.getPosition().get_x(); assert(side && x > 3 && x < 3.55, `Controller penetrated wall or never reached it: ${x}`);
      controller.release(); controller = null; assert(manager.getNbControllers() === 0, 'Controller release did not remove ownership');
      return {bottom, side, stoppedX: x, remainingControllers: 0};
    } finally { controller?.release(); manager.release(); wall.release(); }
  });
  await test(advancedRegressionNames[4], () => {
    const allocated = [], meshes = [];
    const data = (array, stride) => {
      const pointer = P._malloc(array.byteLength); assert(pointer, 'Cooking input allocation failed'); allocated.push(pointer);
      P.HEAPU8.set(new Uint8Array(array.buffer), pointer);
      const bounded = make('PxBoundedData'); bounded.set_count(array.length / (stride / 4)); bounded.set_stride(stride); bounded.set_data(P.wrapPointer(pointer, P.VoidPtr)); return bounded;
    };
    try {
      const params = make('PxCookingParams', physics.getTolerancesScale()), convex = make('PxConvexMeshDesc');
      convex.set_points(data(new Float32Array([-1,0,-1, 1,0,-1, 1,0,1, -1,0,1, 0,2,0]), 12));
      convex.set_flags(make('PxConvexFlags', P.PxConvexFlagEnum.eCOMPUTE_CONVEX));
      const convexStream = make('PxDefaultMemoryOutputStream'); assert(P.CookConvexMesh(params, convex, convexStream), 'Convex cooking failed');
      const c = physics.createConvexMesh(make('PxDefaultMemoryInputData', P.NativeArrayHelpers.prototype.voidToU8Ptr(convexStream.getData()), convexStream.getSize()));
      assert(P.getPointer(c), 'Cooked convex reload failed'); meshes.push(c); assert(c.getNbVertices() === 5, 'Convex hull changed vertex topology');
      const triangle = make('PxTriangleMeshDesc');
      triangle.set_points(data(new Float32Array([-1,0,-1, 1,0,-1, 1,0,1, -1,0,1]), 12));
      triangle.set_triangles(data(new Uint32Array([0,2,1, 0,3,2]), 12));
      assert(triangle.isValid(), 'Triangle descriptor invalid');
      const triangleStream = make('PxDefaultMemoryOutputStream'); assert(P.CookTriangleMesh(params, triangle, triangleStream), 'Triangle cooking failed');
      const t = physics.createTriangleMesh(make('PxDefaultMemoryInputData', P.NativeArrayHelpers.prototype.voidToU8Ptr(triangleStream.getData()), triangleStream.getSize()));
      assert(P.getPointer(t), 'Cooked triangle reload failed'); meshes.push(t);
      assert(t.getNbVertices() === 4 && t.getNbTriangles() === 2, 'Triangle topology changed in roundtrip');
      return {convexVertices: c.getNbVertices(), convexBytes: convexStream.getSize(), triangleVertices: t.getNbVertices(), triangles: t.getNbTriangles(), triangleBytes: triangleStream.getSize()};
    } finally { for (const mesh of meshes.reverse()) mesh.release(); for (const pointer of allocated) P._free(pointer); }
  });
  await test(advancedRegressionNames[5], () => {
    const serialization = P.PxSerialization.prototype, actor = box(12, 6, 0);
    const registry = serialization.createSerializationRegistry(physics), collection = P.PxCollectionExt.prototype.createCollection(scene); let decoded; let allocation = 0;
    const objectId = 0x1000000010000001n;
    try {
      actor.setName('serialization-regression'); collection.addId(actor, objectId); serialization.complete(collection, registry);
      assert(serialization.isSerializable(collection, registry), 'Collection not serializable');
      const stream = make('PxDefaultMemoryOutputStream'); assert(serialization.serializeCollectionToBinary(stream, collection, registry, P.wrapPointer(0, P.PxCollection), true), 'Binary serialization failed');
      const bytes = stream.getSize(); assert(bytes > 0, 'Empty serialized collection');
      allocation = P._malloc(bytes + 127); assert(allocation, 'Serialized buffer allocation failed');
      const aligned = Math.ceil(allocation / 128) * 128; P.HEAPU8.copyWithin(aligned, P.getPointer(stream.getData()), P.getPointer(stream.getData()) + bytes);
      decoded = serialization.createCollectionFromBinary(P.wrapPointer(aligned, P.VoidPtr), registry); assert(P.getPointer(decoded), 'Binary collection decode failed');
      const copy = P.castObject(decoded.find(objectId), P.PxRigidDynamic); assert(P.getPointer(copy), 'Serialized actor ID missing');
      assert(decoded.getId(copy) === objectId, 'Serialized 64-bit object ID lost precision');
      const restored = components(copy); [12, 6, 0].forEach((value, index) => near(restored[index], value, 0, 'Serialized actor pose'));
      assert(copy.getName() === actor.getName(), 'Serialized actor name changed');
      return {objects: decoded.getNbObjects(), bytes, objectId: objectId.toString(), idExceedsNumberPrecision: true, pose: restored};
    } finally {
      if (decoded) { for (let index = decoded.getNbObjects() - 1; index >= 0; index--) { const object = decoded.getObject(index); if (object.isReleasable()) object.release(); } decoded.release(); }
      if (allocation) P._free(allocation); collection.release(); registry.release(); actor.release();
    }
  });
  await test(advancedRegressionNames[6], () => {
    const runs = [];
    for (let run = 0; run < 2; run++) {
      const actor = box(0, 6, -8); const trace = [];
      try {
        actor.setLinearVelocity(vec(1, 0, .25), true); actor.setAngularVelocity(vec(.2, .4, .1), true);
        for (let tick = 0; tick < 240; tick++) { if (tick === 40) actor.addForce(vec(.2, .5, 0), P.PxForceModeEnum.eIMPULSE, true); step(1); trace.push(...components(actor)); }
      } finally { actor.release(); }
      runs.push(new Float32Array(trace));
    }
    const a = new Uint8Array(runs[0].buffer), b = new Uint8Array(runs[1].buffer); assert(a.every((value, index) => value === b[index]), 'Same-build deterministic replay differs');
    return {runs: 2, steps: 240, components: runs[0].length, identicalBytes: a.length, scope: 'Same module, fixed timestep and inputs; not cross-build/platform multiplayer determinism'};
  });
}
