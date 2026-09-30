// SPDX-License-Identifier: MIT
// Actual PhysX SDK regression scenarios. No fallback solver or mocked actors.
// These scenarios are WRITTEN, NOT validated until a real build executes them.
import {PhysXBulk} from '../bridge/physx-bulk.mjs';
const assert=(ok,message)=>{if(!ok)throw new Error(message);};
const near=(actual,expected,tolerance,message)=>assert(Number.isFinite(actual)&&Math.abs(actual-expected)<=tolerance,`${message}: ${actual}, expected ${expected} ± ${tolerance}`);
const xyz=v=>[v.get_x(),v.get_y(),v.get_z()];
export const regressionNames=[
  'Regression API surface',
  'Zero-gravity constant velocity',
  'Impulse follows inverse mass',
  'Force accumulator clears after one step',
  'Angular rotation remains normalized',
  'Sleep and explicit wake',
  'Kinematic target and return to dynamic mode',
  'Raycast miss has no blocking hit',
  'Static actor remains fixed',
];
export const bulkRegressionNames=[
  'Bulk seven-component parity during motion',
  'Bulk contexts remain independent',
  'Bulk capacity and unregister reuse',
  'Bulk context lifecycle churn',
];
export async function runRigidRegressions({P,scene,body,ground,make,step,test,withBulk}) {
  const vec=(x=0,y=0,z=0)=>make('PxVec3',x,y,z);
  const transform=(x,y,z)=>{const t=make('PxTransform',P.PxIDENTITYEnum.PxIdentity);t.set_p(vec(x,y,z));return t;};
  const position=actor=>xyz(actor.getGlobalPose().get_p());
  const components=actor=>{const t=actor.getGlobalPose(),q=t.get_q();return [...xyz(t.get_p()),q.get_x(),q.get_y(),q.get_z(),q.get_w()];};
  const reset=()=>{
    scene.setGravity(vec());body.setGlobalPose(transform(0,10,0),true);
    body.setMass(1);body.setMassSpaceInertiaTensor(vec(1/6,1/6,1/6));
    body.setLinearDamping(0);body.setAngularDamping(0);
    body.setLinearVelocity(vec(),true);body.setAngularVelocity(vec(),true);body.wakeUp();
  };
  await test(regressionNames[0],()=>{
    for(const name of ['setGlobalPose','setLinearVelocity','getLinearVelocity','setAngularVelocity','setAngularDamping',
      'addForce','putToSleep','wakeUp','isSleeping','setRigidBodyFlag','setKinematicTarget'])
      assert(typeof body[name]==='function',`Required binding missing: PxRigidDynamic.${name}`);
    assert(typeof scene.setGravity==='function','Required binding missing: PxScene.setGravity');
    for(const [object,name] of [[P.PxForceModeEnum,'eIMPULSE'],[P.PxForceModeEnum,'eFORCE'],[P.PxRigidBodyFlagEnum,'eKINEMATIC']])
      assert(Number.isInteger(object?.[name]),`Required binding enum missing: ${name}`);
    return {missingBindingsAreFailures:true};
  });
  await test(regressionNames[1],()=>{
    reset();body.setLinearVelocity(vec(2,0,-1),true);step(60);const p=position(body);
    [2,10,-1].forEach((v,i)=>near(p[i],v,0.0005,`position[${i}]`));
    return {position:p,expected:[2,10,-1],steps:60};
  });
  await test(regressionNames[2],()=>{
    reset();body.setMass(2);body.addForce(vec(4,0,0),P.PxForceModeEnum.eIMPULSE,true);step(1);
    const vx=body.getLinearVelocity().get_x();near(vx,2,0.0001,'impulse / mass');
    return {mass:2,impulse:4,velocityX:vx};
  });
  await test(regressionNames[3],()=>{
    reset();body.addForce(vec(6,0,0),P.PxForceModeEnum.eFORCE,true);step(1);
    const first=body.getLinearVelocity().get_x();near(first,0.1,0.0001,'force integration');
    step(5);const later=body.getLinearVelocity().get_x();near(later,first,0.0001,'force did not reset');
    return {velocityAfterOneStep:first,velocityAfterSixSteps:later};
  });
  await test(regressionNames[4],()=>{
    reset();body.setAngularVelocity(vec(0,2,0),true);step(120);
    const p=components(body),norm=Math.hypot(...p.slice(3));near(norm,1,0.0002,'quaternion norm');
    assert(Math.abs(p[4])>0.1,'Orientation did not rotate around Y');
    return {quaternion:p.slice(3),norm,steps:120};
  });
  await test(regressionNames[5],()=>{
    reset();body.putToSleep();assert(body.isSleeping(),'putToSleep failed');step(10);
    near(position(body)[1],10,0.0001,'sleeping body moved');
    body.wakeUp();assert(!body.isSleeping(),'wakeUp failed');body.setLinearVelocity(vec(1,0,0),true);step(1);
    assert(position(body)[0]>0,'Awake body failed to move');return {sleepAndWake:true};
  });
  await test(regressionNames[6],()=>{
    reset();body.setRigidBodyFlag(P.PxRigidBodyFlagEnum.eKINEMATIC,true);
    body.setKinematicTarget(transform(3,10,1));step(1);const p=position(body);
    [3,10,1].forEach((v,i)=>near(p[i],v,0.0001,`kinematic[${i}]`));
    body.setRigidBodyFlag(P.PxRigidBodyFlagEnum.eKINEMATIC,false);
    body.setLinearVelocity(vec(),true);body.setAngularVelocity(vec(),true);
    scene.setGravity(vec(0,-9.81,0));body.wakeUp();step(1);
    assert(position(body)[1]<10,'Dynamic body did not resume gravity');return {target:p,dynamicY:position(body)[1]};
  });
  await test(regressionNames[7],()=>{
    const hits=make('PxRaycastBuffer10');
    assert(!scene.raycast(vec(1000,20,1000),vec(0,-1,0),30,hits),'Unexpected hit outside scene');
    assert(!hits.get_hasBlock(),'Miss has stale blocking hit');return {miss:true};
  });
  await test(regressionNames[8],()=>{
    const before=components(ground);step(60);const after=components(ground);
    before.forEach((v,i)=>near(after[i],v,0,'static transform'));return {staticPose:after};
  });
  if(withBulk){
    await test(bulkRegressionNames[0],()=>{
      reset();body.setLinearVelocity(vec(1,0,-0.5),true);body.setAngularVelocity(vec(0,1,0),true);
      const bulk=new PhysXBulk(P,{capacity:2});
      try{
        bulk.add(0xffffffff,body).add(16777217,ground);
        for(let i=0;i<256;i++){
          step(1);const s=bulk.snapshot({copy:i%2===0});
          assert(s.count===2&&s.ids[0]===0xffffffff&&s.ids[1]===16777217,'IDs/count changed');
          const expected=[...components(body),...components(ground)];
          expected.forEach((value,j)=>near(s.poses[j],value,0.000001,`bulk pose tick=${i} component=${j}`));
        }
        return {steps:256,componentsCompared:256*14};
      }finally{bulk.dispose();}
    });
    await test(bulkRegressionNames[1],()=>{
      const a=new PhysXBulk(P,{capacity:1});let b;
      try{
        b=new PhysXBulk(P,{capacity:1});a.add(1,body);b.add(1,ground);
        const before=b.snapshot({copy:true});a.dispose();const after=b.snapshot();
        assert(after.count===1&&after.ids[0]===before.ids[0],'Other context corrupted');
        [...before.poses].forEach((v,i)=>near(after.poses[i],v,0,'independent context'));
        return {sameModuleContexts:2};
      }finally{a.dispose();b?.dispose();}
    });
    await test(bulkRegressionNames[2],()=>{
      const b=new PhysXBulk(P,{capacity:1});
      try{
        b.add(1,body);let rejected=false;try{b.add(2,ground);}catch{rejected=true;}
        assert(rejected,'Capacity overflow accepted');assert(b.snapshot().ids[0]===1,'Overflow changed published state');
        b.remove(1);assert(b.snapshot().count===0,'Unregister did not empty batch');
        b.add(1,ground);assert(b.snapshot().ids[0]===1,'Re-register failed');return {capacity:1,reuse:true};
      }finally{b.dispose();}
    });
    await test(bulkRegressionNames[3],()=>{
      const before=P.HEAPU8.byteLength;
      for(let cycle=0;cycle<128;cycle++){
        const b=new PhysXBulk(P,{capacity:2});
        try{b.add(1,body);assert(b.snapshot().count===1,`cycle=${cycle}`);b.remove(1);}
        finally{b.dispose();}
      }
      return {cycles:128,heapBytesBefore:before,heapBytesAfter:P.HEAPU8.byteLength,
        note:'WASM heap high-water observation only; not proof of leak freedom.'};
    });
  }
  // Leave body in an ordinary dynamic state for the existing teardown.
  scene.setGravity(vec(0,-9.81,0));body.setMass(1);
}
