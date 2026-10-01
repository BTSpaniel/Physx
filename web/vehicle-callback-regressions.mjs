// SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
// SPDX-License-Identifier: MIT
// Independently authored fixtures for the unchanged native CPU-WASM binding.
// Vehicle2 direct drive uses wheel torque, suspension raycasts and tire forces;
// no fixture applies a chassis velocity to manufacture acceleration or steering.
const assert = (ok, message) => { if (!ok) throw new Error(message); };
export const vehicleCallbackRegressionNames = [
  'Vehicle2 tire-supported acceleration',
  'Vehicle2 braking from speed',
  'Vehicle2 steering changes trajectory',
  'Vehicle2 actor and extension cleanup',
  'Contact callback native actors and impulses',
  'Trigger callback enter and exit without blocking',
  'Simulation callback detach and scene cleanup',
];

export async function runVehicleCallbackRegressions({P, foundation, physics, material, make, test}) {
  const vec = (x = 0, y = 0, z = 0) => make('PxVec3', x, y, z);
  const pose = (x = 0, y = 0, z = 0) => {
    const value = make('PxTransform', P.PxIDENTITYEnum.PxIdentity);
    value.set_p(vec(x, y, z)); return value;
  };
  const native = (value, Type) => typeof value === 'number' ? P.wrapPointer(value, Type) : value;
  const actorTypes = make('PxActorTypeFlags', P.PxActorTypeFlagEnum.eRIGID_STATIC | P.PxActorTypeFlagEnum.eRIGID_DYNAMIC);
  const position = actor => { const p = actor.getGlobalPose().get_p(); return [p.get_x(), p.get_y(), p.get_z()]; };
  const speed = actor => { const v = actor.getLinearVelocity(); return Math.hypot(v.get_x(), v.get_y(), v.get_z()); };
  const dispatchers = new Map();
  const createScene = (gravity = -9.81, callback = null) => {
    const desc = make('PxSceneDesc', make('PxTolerancesScale'));
    desc.set_gravity(vec(0, gravity, 0));
    const dispatcher = P.DefaultCpuDispatcherCreate(0);
    desc.set_cpuDispatcher(dispatcher);
    desc.set_filterShader(P.DefaultFilterShader());
    if (callback) desc.set_simulationEventCallback(callback);
    const scene = physics.createScene(desc);
    assert(P.getPointer(scene), 'Native fixture scene creation failed');
    dispatchers.set(scene, dispatcher);
    return scene;
  };
  const releaseScene = scene => {
    scene.release(); P.destroy(dispatchers.get(scene)); dispatchers.delete(scene);
  };
  const advance = (scene, count, before) => {
    for (let i = 0; i < count; i++) {
      if (before) before();
      scene.simulate(1 / 60); assert(scene.fetchResults(true), 'Fixture fetchResults failed');
    }
  };
  const addBox = (scene, dynamic, at, half, flags, pairFlags = 0) => {
    const actor = dynamic ? physics.createRigidDynamic(pose(...at)) : physics.createRigidStatic(pose(...at));
    assert(P.getPointer(actor), 'Native fixture actor creation failed');
    const shape = physics.createShape(make('PxBoxGeometry', ...half), material, true, make('PxShapeFlags', flags));
    assert(P.getPointer(shape), 'Native fixture shape creation failed');
    try {
      shape.setSimulationFilterData(make('PxFilterData', 1, 1, pairFlags, 0));
      assert(actor.attachShape(shape), 'Native fixture attachShape failed');
    } finally { shape.release(); }
    if (dynamic) {
      actor.setMass(1); actor.setMassSpaceInertiaTensor(vec(1 / 6, 1 / 6, 1 / 6));
      actor.setLinearDamping(0);
    }
    assert(scene.addActor(actor), 'Native fixture addActor failed'); return actor;
  };
  const simFlags = P.PxShapeFlagEnum.eSIMULATION_SHAPE | P.PxShapeFlagEnum.eSCENE_QUERY_SHAPE;
  const top = P.PxVehicleTopLevelFunctions.prototype;
  let extension = false, car = null;
  const vehicles = [];
  const makeCar = () => {
    const scene = createScene(), entry = {scene, road: null, vehicle: null, actor: null, initialized: false, closed: false};
    vehicles.push(entry);
    entry.road = addBox(scene, false, [0, -.5, 0], [200, .5, 200], simFlags);
    const vehicle = make('DirectDriveVehicle'); entry.vehicle = vehicle;
    const base = vehicle.get_baseParams(), axles = base.get_axleDescription();
    axles.setToDefault(); axles.set_nbAxles(2); axles.set_nbWheels(4);
    for (let a = 0; a < 2; a++) { axles.set_nbWheelsPerAxle(a, 2); axles.set_axleToWheelIds(a, a * 2); }
    const frame = base.get_frame();
    frame.set_lngAxis(P.PxVehicleAxesEnum.ePosZ); frame.set_latAxis(P.PxVehicleAxesEnum.ePosX); frame.set_vrtAxis(P.PxVehicleAxesEnum.ePosY);
    base.get_scale().set_scale(1);
    base.get_rigidBodyParams().set_mass(800);
    base.get_rigidBodyParams().set_moi(vec(800 * (1.6 ** 2 + .6 ** 2) / 12, 800 * (3.2 ** 2 + 1.6 ** 2) / 12, 800 * (3.2 ** 2 + .6 ** 2) / 12));
    const steer = base.get_steerResponseParams(); steer.set_maxResponse(.45); steer.get_nonlinearResponse().clear();
    for (let b = 0; b < 2; b++) {
      const brake = base.get_brakeResponseParams(b); brake.set_maxResponse(b ? 0 : 900); brake.get_nonlinearResponse().clear();
      for (let w = 0; w < 4; w++) brake.set_wheelResponseMultipliers(w, 1);
    }
    const ackermann = base.get_ackermannParams(0);
    ackermann.set_wheelIds(0, 0); ackermann.set_wheelIds(1, 1);
    ackermann.set_wheelBase(2.2); ackermann.set_trackWidth(1.5); ackermann.set_strength(1);
    const suspensionState = base.get_suspensionStateCalculationParams();
    suspensionState.set_suspensionJounceCalculationType(P.PxVehicleSuspensionJounceCalculationTypeEnum.eRAYCAST);
    suspensionState.set_limitSuspensionExpansionVelocity(false);
    const tireExt = P.PxVehicleTireForceParamsExt.prototype;
    for (let w = 0; w < 4; w++) {
      axles.set_wheelIdsInAxleOrder(w, w); steer.set_wheelResponseMultipliers(w, w < 2 ? 1 : 0);
      const suspension = base.get_suspensionParams(w);
      suspension.set_suspensionAttachment(pose(w % 2 ? .75 : -.75, -.25, w < 2 ? 1.1 : -1.1));
      suspension.set_wheelAttachment(pose()); suspension.set_suspensionTravelDir(vec(0, -1, 0)); suspension.set_suspensionTravelDist(.3);
      const compliance = base.get_suspensionComplianceParams(w);
      for (const field of ['wheelToeAngle', 'wheelCamberAngle', 'suspForceAppPoint', 'tireForceAppPoint']) compliance['get_' + field]().clear();
      const spring = base.get_suspensionForceParams(w);
      spring.set_sprungMass(200); spring.set_stiffness(18000); spring.set_damping(2200);
      const tire = base.get_tireForceParams(w);
      tire.set_restLoad(2158.2); tire.set_longStiff(18000); tire.set_latStiffX(2); tire.set_latStiffY(45000); tire.set_camberStiff(0);
      for (let i = 0; i < 3; i++) { tireExt.setFrictionVsSlip(tire, i, 0, [0, .1, 1][i]); tireExt.setFrictionVsSlip(tire, i, 1, 1); }
      tireExt.setLoadFilter(tire, 0, 0, 0); tireExt.setLoadFilter(tire, 0, 1, 0);
      tireExt.setLoadFilter(tire, 1, 0, 3); tireExt.setLoadFilter(tire, 1, 1, 3);
      const wheel = base.get_wheelParams(w);
      wheel.set_radius(.3); wheel.set_halfWidth(.12); wheel.set_mass(20); wheel.set_moi(.9); wheel.set_dampingRate(.15);
    }
    base.set_nbAntiRollForceParams(0); assert(base.isValid(), 'Vehicle2 base parameters invalid');
    const throttle = vehicle.get_directDriveParams().get_directDriveThrottleResponseParams();
    throttle.set_maxResponse(220); throttle.get_nonlinearResponse().clear();
    for (let w = 0; w < 4; w++) throttle.set_wheelResponseMultipliers(w, 1);
    const integration = vehicle.get_physXParams(), friction = make('PxVehiclePhysXMaterialFriction');
    friction.set_material(material); friction.set_friction(1);
    integration.create(axles, make('PxQueryFilterData'), 0, friction, 1, 1, pose(), make('PxBoxGeometry', 1.6, .3, .8), pose(), P.PxVehiclePhysXRoadGeometryQueryTypeEnum.eRAYCAST);
    // Suspension queries may hit road query shapes, never the vehicle itself.
    integration.set_physxActorShapeFlags(make('PxShapeFlags', P.PxShapeFlagEnum.eSIMULATION_SHAPE));
    integration.set_physxActorSimulationFilterData(make('PxFilterData', 1, 1, 0, 0));
    integration.set_physxActorWheelShapeFlags(make('PxShapeFlags', P.PxShapeFlagEnum.eVISUALIZATION));
    const cooking = make('PxCookingParams', make('PxTolerancesScale'));
    assert(vehicle.initialize(physics, cooking, material, true), 'Native Vehicle2 initialize failed'); entry.initialized = true;
    entry.actor = P.castObject(vehicle.get_physXState().get_physxActor().get_rigidBody(), P.PxRigidDynamic);
    assert(P.getPointer(entry.actor), 'Native Vehicle2 actor missing');
    entry.actor.setGlobalPose(pose(0, 1, 0)); entry.actor.setSolverIterationCounts(8, 2);
    assert(scene.addActor(entry.actor), 'Vehicle2 actor scene insertion failed');
    const context = make('PxVehiclePhysXSimulationContext'); context.setToDefault();
    context.set_frame(frame); context.get_scale().set_scale(1); context.set_gravity(vec(0, -9.81, 0));
    context.set_physxScene(scene); context.set_physxActorUpdateMode(P.PxVehiclePhysXActorUpdateModeEnum.eAPPLY_ACCELERATION);
    entry.command = vehicle.get_commandState(); entry.command.set_nbBrakes(2);
    vehicle.get_transmissionCommandState().set_gear(P.PxVehicleDirectDriveTransmissionCommandStateEnum.eFORWARD);
    entry.run = count => advance(scene, count, () => vehicle.step(1 / 60, context));
    entry.run(180); return entry;
  };
  const closeCar = entry => {
    if (entry.closed) return;
    if (entry.initialized) {
      if (entry.actor && P.getPointer(entry.actor.getScene())) entry.scene.removeActor(entry.actor);
      entry.vehicle.destroyState(); entry.initialized = false;
    }
    if (entry.road) { entry.road.release(); entry.road = null; }
    assert(entry.scene.getNbActors(actorTypes) === 0, 'Vehicle fixture leaked scene actors');
    releaseScene(entry.scene); entry.closed = true;
  };
  try {
    await test(vehicleCallbackRegressionNames[0], () => {
      assert(top.InitVehicleExtension(foundation), 'Vehicle extension initialization failed'); extension = true;
      car = makeCar(); const base = car.vehicle.get_baseState(), atRest = position(car.actor), restSpeed = speed(car.actor);
      const support = Array.from({length: 4}, (_, w) => ({roadHit: base.get_roadGeomStates(w).get_hitState(), loadN: base.get_tireGripStates(w).get_load(), jounceM: base.get_suspensionStates(w).get_jounce()}));
      assert(support.every(w => w.roadHit && w.loadN > 100), 'Vehicle did not settle on four load-bearing tires');
      assert(atRest[1] > .3 && atRest[1] < 1.1 && restSpeed < .15, `Vehicle failed to settle: ${atRest}, ${restSpeed}`);
      car.command.set_throttle(.65); car.run(180); const atSpeed = position(car.actor), finalSpeed = speed(car.actor);
      const wheelSpeed = base.get_wheelRigidBody1dStates(0).get_rotationSpeed();
      assert(finalSpeed > 3 && atSpeed[2] - atRest[2] > 4 && Math.abs(wheelSpeed) > 5, `Native tire acceleration missing: ${finalSpeed}, ${atSpeed}, ${wheelSpeed}`);
      return {backend: 'CPU-WASM Vehicle2 DirectDriveVehicle',massKg: 800, torqueNmPerWheel: 143, seconds: 3, atRest, restSpeed, support, atSpeed, speedMps: finalSpeed, wheelRadPerSec: wheelSpeed};
    });
    await test(vehicleCallbackRegressionNames[1], () => {
      const before = speed(car.actor); car.command.set_throttle(0); car.command.set_brakes(0, 1); car.run(240);
      const after = speed(car.actor); assert(after < before * .2 && after < .5, `Vehicle braking failed: ${before} -> ${after}`);
      return {initialMps: before, finalMps: after, brakeTorqueNmPerWheel: 900, seconds: 4};
    });
    await test(vehicleCallbackRegressionNames[2], () => {
      const turns = [];
      for (const steer of [-.5, .5]) {
        const trial = makeCar();
        try {
          trial.command.set_throttle(.5); trial.run(120); const start = position(trial.actor);
          trial.command.set_steer(steer); trial.run(180); const end = position(trial.actor), q = trial.actor.getGlobalPose().get_q();
          const yaw = Math.atan2(2 * (q.get_w() * q.get_y() + q.get_x() * q.get_z()), 1 - 2 * (q.get_y() ** 2 + q.get_x() ** 2));
          turns.push({steer, start, end, lateralM: end[0] - start[0], yawRad: yaw, frontAngleRad: trial.vehicle.get_baseState().get_steerCommandResponseStates(0)});
        } finally { closeCar(trial); }
      }
      assert(turns.every(t => Math.abs(t.lateralM) > .5 && Math.abs(t.yawRad) > .05), `Steering did not turn chassis: ${JSON.stringify(turns)}`);
      assert(turns[0].lateralM * turns[1].lateralM < 0 && turns[0].yawRad * turns[1].yawRad < 0, 'Opposite steering did not produce opposite native trajectories');
      return {secondsPerTurn: 3, turns};
    });
    await test(vehicleCallbackRegressionNames[3], () => {
      for (const entry of vehicles) closeCar(entry);
      top.CloseVehicleExtension(); extension = false;
      return {vehiclesDestroyed: vehicles.length, scenesReleased: vehicles.length, remainingFixtureActors: 0, extensionClosed: true, longRunLeakCertification: false};
    });
  } finally {
    for (const entry of vehicles) if (!entry.closed) closeCar(entry);
    if (extension) top.CloseVehicleExtension();
  }

  const callback = make('PxSimulationEventCallbackImpl');
  const events = {contacts: [], triggers: []}, arrays = P.NativeArrayHelpers.prototype;
  const points = make('PxArray_PxContactPairPoint', 16);
  callback.onConstraintBreak = () => {}; callback.onWake = () => {}; callback.onSleep = () => {};
  callback.onContact = (headerArg, pairsArg, count) => {
    const header = native(headerArg, P.PxContactPairHeader), pairs = native(pairsArg, P.PxContactPair);
    const actors = [P.getPointer(header.get_actors(0)), P.getPointer(header.get_actors(1))];
    for (let i = 0; i < count; i++) {
      const pair = arrays.getContactPairAt(pairs, i), flags = pair.get_events();
      const extracted = pair.extractContacts(points.begin(), 16), samples = [];
      for (let j = 0; j < extracted; j++) {
        const p = points.get(j), impulse = p.get_impulse(), normal = p.get_normal();
        samples.push({impulseNsec: Math.hypot(impulse.get_x(), impulse.get_y(), impulse.get_z()), normalY: normal.get_y(), separationM: p.get_separation()});
      }
      events.contacts.push({actors, found: flags.isSet(P.PxPairFlagEnum.eNOTIFY_TOUCH_FOUND), lost: flags.isSet(P.PxPairFlagEnum.eNOTIFY_TOUCH_LOST), samples});
    }
  };
  callback.onTrigger = (pairsArg, count) => {
    const pairs = native(pairsArg, P.PxTriggerPair);
    for (let i = 0; i < count; i++) {
      const pair = arrays.getTriggerPairAt(pairs, i);
      events.triggers.push({trigger: P.getPointer(pair.get_triggerActor()), other: P.getPointer(pair.get_otherActor()), status: pair.get_status()});
    }
  };
  let contactScene, triggerScene, floor, falling, trigger, crossing;
  try {
    await test(vehicleCallbackRegressionNames[4], () => {
      contactScene = createScene(-9.81, callback);
      const notify = P.PxPairFlagEnum.eNOTIFY_TOUCH_FOUND | P.PxPairFlagEnum.eNOTIFY_TOUCH_LOST | P.PxPairFlagEnum.eNOTIFY_CONTACT_POINTS;
      floor = addBox(contactScene, false, [0, -.5, 0], [5, .5, 5], simFlags, notify);
      falling = addBox(contactScene, true, [0, 3, 0], [.5, .5, .5], simFlags, notify);
      advance(contactScene, 180);
      const expected = [P.getPointer(floor), P.getPointer(falling)].sort((a, b) => a - b);
      assert(events.contacts.length > 0 && events.contacts.every(e => e.actors.slice().sort((a, b) => a - b).every((p, i) => p === expected[i])), 'Contact callbacks have incorrect native actor identity');
      const found = events.contacts.find(e => e.found);
      const maxImpulse = Math.max(0, ...events.contacts.flatMap(e => e.samples.map(p => p.impulseNsec)));
      assert(found && maxImpulse > .1 && found.samples.some(p => Math.abs(p.normalY) > .9), 'Native impact contact point/normal/impulse missing');
      falling.setGlobalPose(pose(0, 3, 0)); falling.setLinearVelocity(vec()); advance(contactScene, 1);
      assert(events.contacts.some(e => e.lost), 'Contact separation did not deliver touch-lost');
      return {actorPointers: expected, callbackPairs: events.contacts.length, extractedPoints: events.contacts.reduce((n, e) => n + e.samples.length, 0), maxImpulseNsec: maxImpulse, found: true, lost: true};
    });
    await test(vehicleCallbackRegressionNames[5], () => {
      triggerScene = createScene(0, callback);
      trigger = addBox(triggerScene, false, [0, 1, 0], [1, 1, 1], P.PxShapeFlagEnum.eTRIGGER_SHAPE | P.PxShapeFlagEnum.eSCENE_QUERY_SHAPE);
      crossing = addBox(triggerScene, true, [-3, 1, 0], [.25, .25, .25], simFlags);
      crossing.setLinearVelocity(vec(3, 0, 0)); advance(triggerScene, 120);
      const expected = {trigger: P.getPointer(trigger), other: P.getPointer(crossing)};
      assert(events.triggers.length === 2 && events.triggers.every(e => e.trigger === expected.trigger && e.other === expected.other), `Unexpected trigger actors/events: ${JSON.stringify(events.triggers)}`);
      assert(events.triggers[0].status === P.PxPairFlagEnum.eNOTIFY_TOUCH_FOUND && events.triggers[1].status === P.PxPairFlagEnum.eNOTIFY_TOUCH_LOST, 'Trigger enter/exit ordering incorrect');
      const end = position(crossing), finalSpeed = speed(crossing);
      assert(Math.abs(end[0] - 3) < .02 && Math.abs(finalSpeed - 3) < .02, 'Trigger volume physically blocked the crossing body');
      return {events: events.triggers, seconds: 2, finalPosition: end, speedMps: finalSpeed, triggerDoesNotApplyContactForce: true};
    });
    await test(vehicleCallbackRegressionNames[6], () => {
      contactScene.setSimulationEventCallback(0); triggerScene.setSimulationEventCallback(0);
      const counts = [events.contacts.length, events.triggers.length];
      falling.setGlobalPose(pose(0, 1, 0)); falling.setLinearVelocity(vec(0, -2, 0)); advance(contactScene, 60);
      crossing.setGlobalPose(pose(-3, 1, 0)); crossing.setLinearVelocity(vec(3, 0, 0)); advance(triggerScene, 120);
      assert(events.contacts.length === counts[0] && events.triggers.length === counts[1], 'Detached native callback was invoked');
      floor.release(); floor = null; falling.release(); falling = null; trigger.release(); trigger = null; crossing.release(); crossing = null;
      assert(contactScene.getNbActors(actorTypes) === 0 && triggerScene.getNbActors(actorTypes) === 0, 'Callback fixture actors were not released');
      releaseScene(contactScene); contactScene = null; releaseScene(triggerScene); triggerScene = null;
      return {callbacksDetached: 2, nativeActorsReleased: 4, scenesReleased: 2, remainingFixtureActors: 0, eventsAfterDetach: 0};
    });
  } finally {
    if (contactScene) contactScene.setSimulationEventCallback(0);
    if (triggerScene) triggerScene.setSimulationEventCallback(0);
    for (const actor of [floor, falling, trigger, crossing]) if (actor) actor.release();
    if (contactScene) releaseScene(contactScene); if (triggerScene) releaseScene(triggerScene);
  }
}
