// SPDX-License-Identifier: MIT
// Real PhysX smoke tests. There is deliberately NO substitute/fake physics backend.
import {PhysXBulk} from '../bridge/physx-bulk.mjs';
import {runRigidRegressions} from './regressions.mjs';
const versions={baseline:'5.6.1',candidate:'5.11.0'};
export const decodeVersion=n=>`${(n>>>24)&255}.${(n>>>16)&255}.${(n>>>8)&255}`;
const assert=(ok,message)=>{if(!ok)throw new Error(message);};
export async function digest(bytes) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');
}
export async function readArtifacts(profile) {
  assert(Object.hasOwn(versions,profile),'Unknown build profile');
  const base=new URL(`../dist/${profile}/`,import.meta.url);
  const mr=await fetch(new URL('build-manifest.json',base),{cache:'no-store'});
  if(!mr.ok)throw new Error(`No staged ${profile} build (HTTP ${mr.status}). Build or stage the real matched .mjs/.wasm files first.`);
  const manifest=await mr.json();
  assert(manifest.expected_runtime_version===versions[profile],'Manifest version does not match profile');
  const fetched={};
  const stem=profile==='candidate'?'physx-pe':'physx-js-webidl';
  for(const name of [stem+'.mjs',stem+'.wasm']){
    const r=await fetch(new URL(name,base),{cache:'no-store'});
    assert(r.ok,`Missing ${name}: HTTP ${r.status}`);
    const bytes=new Uint8Array(await r.arrayBuffer());
    const meta=manifest.artifacts?.[name];
    assert(meta && bytes.byteLength===meta.bytes,`${name}: byte count mismatch`);
    assert(await digest(bytes)===meta.sha256,`${name}: SHA-256 mismatch`);
    fetched[name]=bytes;
  }
  const magic=[0,97,115,109,1,0,0,0], wb=fetched[stem+'.wasm'];
  assert(wb.length>8 && magic.every((n,i)=>wb[i]===n),'Invalid WASM header');
  return {base,manifest,wasmBinary:wb,loaderBytes:fetched[stem+'.mjs']};
}
// Execute precisely the bytes whose digest was checked, not a second URL fetch.
// Single-thread module only: wasmBinary and locateFile are supplied explicitly.
// CSP must permit blob: module scripts; do not weaken an application's policy.
export async function instantiateVerified(artifacts, options={}) {
  assert(artifacts.loaderBytes instanceof Uint8Array, 'Verified loader bytes missing');
  const url=URL.createObjectURL(new Blob([artifacts.loaderBytes], {type:'text/javascript'}));
  try {
    const mod=await import(url);
    assert(typeof mod.default==='function', 'Expected an Emscripten module factory');
    return await mod.default({...options, wasmBinary:artifacts.wasmBinary,
      locateFile:name=>new URL(name,artifacts.base).href});
  } finally { URL.revokeObjectURL(url); }
}
export async function runSuite(profile,onTest=()=>{}) {
  const report={profile,started:new Date().toISOString(),status:'RUNNING',tests:[],stderr:[],
    physicsExecuted:false,engineIntegrationVerified:false,releaseApproved:false,
    notRun:['D6/ragdoll behavior','articulation behavior','vehicle behavior','controller behavior',
      'mesh cooking / serialization roundtrip','callbacks / contact events','long-run leak checks',
      'cross-browser/device coverage','pthread build','deterministic replay / multiplayer','full Particle Realms integration']};
  const test=async(name,fn)=>{const t=performance.now();try{const detail=await fn();const r={name,status:'PASS',ms:performance.now()-t,detail:detail??null};report.tests.push(r);onTest(r);return detail;}
    catch(e){const r={name,status:'FAIL',ms:performance.now()-t,error:String(e.message??e)};report.tests.push(r);onTest(r);throw e;}};
  let P,foundation,physics,scene,material,ground,body,bulk;
  const created=[];
  try {
    const artifacts=await test('Matched loader/WASM hashes',()=>readArtifacts(profile));
    // Avoid serializing bytes into the report.
    report.tests[0].detail={sourceVersion:artifacts.manifest.source_version,artifacts:artifacts.manifest.artifacts};
    P=await instantiateVerified(artifacts,{
      print:()=>{},printErr:(...a)=>report.stderr.push(a.join(' '))});
    const make=(name,...args)=>{const x=new P[name](...args);created.push(x);return x;};
    await test('Runtime version and required WebIDL API',()=>{
      assert(decodeVersion(P.PHYSICS_VERSION)===versions[profile],`Expected ${versions[profile]}, loaded ${decodeVersion(P.PHYSICS_VERSION)}`);
      for(const name of ['CreateFoundation','CreatePhysics','PxVec3','PxTransform','PxSceneDesc','PxBoxGeometry',
        'PxRaycastBuffer10','DefaultCpuDispatcherCreate','DefaultFilterShader'])assert(typeof P[name]==='function',`Missing ${name}`);
      return {runtimeVersion:decodeVersion(P.PHYSICS_VERSION)};
    });
    await test('Foundation, CPU scene and rigid bodies',()=>{
      const allocator=make('PxDefaultAllocator'),err=make('PxDefaultErrorCallback');
      foundation=P.CreateFoundation(P.PHYSICS_VERSION,allocator,err);
      assert(P.getPointer(foundation),'Null foundation');
      const tolerances=make('PxTolerancesScale');
      physics=P.CreatePhysics(P.PHYSICS_VERSION,foundation,tolerances);
      assert(P.getPointer(physics),'Null physics');
      const desc=make('PxSceneDesc',tolerances);
      desc.set_gravity(make('PxVec3',0,-9.81,0));
      // Upstream single-thread dispatcher. This suite runs in its OWN Worker.
      const dispatcher=P.DefaultCpuDispatcherCreate(0);
      created.push(dispatcher);
      desc.set_cpuDispatcher(dispatcher);desc.set_filterShader(P.DefaultFilterShader());
      scene=physics.createScene(desc);assert(P.getPointer(scene),'Null scene');
      material=physics.createMaterial(0.5,0.5,0);
      const flags=make('PxShapeFlags',P.PxShapeFlagEnum.eSCENE_QUERY_SHAPE|P.PxShapeFlagEnum.eSIMULATION_SHAPE);
      const filter=make('PxFilterData',1,1,0,0);
      const add=(dynamic,y,hx,hy,hz)=>{
        const pose=make('PxTransform',P.PxIDENTITYEnum.PxIdentity);pose.set_p(make('PxVec3',0,y,0));
        const a=dynamic?physics.createRigidDynamic(pose):physics.createRigidStatic(pose);
        assert(P.getPointer(a),'Null actor');
        const shape=physics.createShape(make('PxBoxGeometry',hx,hy,hz),material,true,flags);
        assert(P.getPointer(shape),'Null shape');shape.setSimulationFilterData(filter);
        assert(a.attachShape(shape),'attachShape failed');shape.release();
        if(dynamic){a.setMass(1);a.setMassSpaceInertiaTensor(make('PxVec3',1/6,1/6,1/6));a.setLinearDamping(0);}
        assert(scene.addActor(a),'addActor failed');return a;
      };
      ground=add(false,-0.5,20,0.5,20);body=add(true,10,0.5,0.5,0.5);
      return {bodies:2,gravity:-9.81,worker:true,dispatcherThreads:0};
    });
    const y=()=>body.getGlobalPose().get_p().get_y();
    const step=n=>{for(let i=0;i<n;i++){scene.simulate(1/60);report.physicsExecuted=true;assert(scene.fetchResults(true),'fetchResults failed');}};
    await test('Free fall: one second without contact',()=>{
      step(60);const actual=y(),continuous=10-0.5*9.81;
      assert(Number.isFinite(actual)&&Math.abs(actual-continuous)<0.15,`Unexpected free fall height ${actual}`);
      return {actualY:actual,continuousReference:continuous,tolerance:0.15};
    });
    await test('Ground collision and resting height',()=>{
      step(300);const actual=y();
      assert(Number.isFinite(actual)&&Math.abs(actual-0.5)<0.08,`Box did not settle: ${actual}`);
      return {actualY:actual,expectedY:0.5,tolerance:0.08};
    });
    await test('Scene raycast hits the resting box',()=>{
      const origin=make('PxVec3',0,20,0),direction=make('PxVec3',0,-1,0),hits=make('PxRaycastBuffer10');
      assert(scene.raycast(origin,direction,30,hits),'Raycast missed all geometry');
      // A nonempty touch buffer defaults to eTOUCH; its hits are not sorted.
      assert(!hits.get_hasBlock()&&hits.getNbTouches()===2,'Expected box and ground touch hits');
      const first=hits.getTouch(0),second=hits.getTouch(1);
      const hit=first.get_distance()<second.get_distance()?first:second;
      assert(P.getPointer(hit.get_actor())===P.getPointer(body),'Raycast did not hit the expected actor');
      const other=hit===first?second:first;
      assert(P.getPointer(other.get_actor())===P.getPointer(ground),'Raycast did not also hit the ground');
      assert(Math.abs(hit.get_distance()-19)<0.12,'Unexpected raycast distance');
      assert(Math.abs(other.get_distance()-20)<0.12,'Unexpected ground raycast distance');
      return {distance:hit.get_distance(),groundDistance:other.get_distance(),touches:2};
    });
    if(artifacts.manifest.bridge_backend==='rust'){
      await test('Rust backend identity',()=>{
        assert(typeof P._pr_bulk_backend==='function' && P._pr_bulk_backend()===2,'Manifest promises Rust but runtime does not identify as Rust-backed');
        return {backend:'rust',internalAbi:2,publicBulkAbi:1};
      });
    }
    if(artifacts.manifest.bulk_addon_requested){
      await test('Bulk addon: IDs, pose equality, removal',()=>{
        bulk=new PhysXBulk(P,{capacity:2});bulk.add(0xffffffff,body);bulk.add(1,ground);
        const s=bulk.snapshot({copy:true});assert(s.count===2&&s.ids[0]===0xffffffff,'Bulk ID/count mismatch');
        assert(Math.abs(s.poses[1]-y())<1e-5,'Bulk/WebIDL pose mismatch');
        bulk.remove(0xffffffff);const after=bulk.snapshot();assert(after.count===1&&after.ids[0]===1,'Bulk removal failed');
        bulk.dispose();bulk=null;return {count:2,largeUint32ID:true};
      });
    }else report.notRun.push('Optional bulk addon (not in this build)');
    await runRigidRegressions({P,scene,body,ground,make,step,test,withBulk:artifacts.manifest.bulk_addon_requested===true});
    await test('Actor, scene and SDK teardown',()=>{
      body.release();body=null;ground.release();ground=null;scene.release();scene=null;
      material.release();material=null;physics.release();physics=null;
      // Foundation must outlive dispatcher/SDK objects. Destroy user-owned value
      // wrappers before foundation; allocator and error callback outlive it.
      for(let i=created.length-1;i>=2;i--)P.destroy(created[i]);created.length=2;
      foundation.release();foundation=null;
      for(let i=created.length-1;i>=0;i--)P.destroy(created[i]);created.length=0;
      return 'Resources released; not a long-run leak certification';
    });
    await test('No SDK stderr diagnostics',()=>{assert(report.stderr.length===0,report.stderr.join('\n'));return {lines:0};});
    report.status='SMOKE_PASSED_NOT_RELEASE_CERTIFIED';
  }catch(e){report.status=report.physicsExecuted?'FAILED':'BLOCKED_OR_FAILED';report.error=String(e.stack??e);}
  // The owner ALWAYS terminates this Worker after receiving the report. On any
  // failed setup, terminating the Worker discards its WASM heap. Never reuse it.
  for(const t of report.tests)if(t.detail?.wasmBinary)delete t.detail.wasmBinary;
  report.finished=new Date().toISOString();return report;
}
