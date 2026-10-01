// SPDX-License-Identifier: MIT
import {FlowHostWebGpu} from './flow_host_webgpu.mjs';
import {rebaseFieldBytes} from './rebase_oracle.mjs';
import {FlowPhysXCollision} from '/engine/sim/FlowPhysXCollision.js';
import {FloatingOrigin} from '/engine/world/FloatingOrigin.js';
import {createPhysicsWorld,createBody,removeBody,stepPhysicsWorld,destroyPhysicsWorld} from '/engine/sim/physics/PhysXPhysicsWorld.js';
const assert=(value,message)=>{if(!value)throw Error(message);};
const identical=(a,b,label)=>assert(a.length===b.length&&a.every((value,index)=>Object.is(value,b[index])||value===b[index]),label);
const reject=async(operation,label)=>{let error=null;try{await operation();}catch(value){error=value;}assert(error,`${label} was accepted`);return String(error);};

/** Real native bodies, actual GPU atlases and the original borrowed device. */
export async function verifyEngineRebase(module,device,shaderRoot){
    const cases=[],before=module._pr_flow_host_live(),world=createPhysicsWorld({gravity:[0,0,0]});
    let host,collision;
    try{
        const deadline=performance.now()+20000;
        while(!world.ready&&performance.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));
        assert(world.ready,'PhysX world did not initialize');
        if(globalThis.expectedUnifiedFlowModule)assert(world.module===module&&module===globalThis.expectedUnifiedFlowModule,'Coordinated rebase did not use one unified native module');
        const P=world.module,origin=new FloatingOrigin();
        host=await FlowHostWebGpu.create(module,device,shaderRoot,{maxBlocks:16,cellSize:.125});
        host.setScene({layers:[{id:0,cellSize:.125,gravity:[0,0,0],pressure:true,combustion:false,vorticity:0}],emitters:[{
            id:1,type:'box',halfSize:[.4,.4,.4],position:[.25,.25,.25],velocity:[.25,0,0],smoke:.1,
            fuel:0,temperature:0,burn:0,coupleRateSmoke:4,coupleRateVelocity:4,coupleRateTemperature:0,coupleRateFuel:0,coupleRateBurn:0}]});
        const body=createBody(world,{simMode:'dynamic',mass:2,position:[.875,.25,.25],linearVelocity:[.1,.2,.3],angularVelocity:[.2,-.1,.3],
            collider:{shape:'box',halfExtents:[.03125,.5,.5]}});
        collision=new FlowPhysXCollision(world,host,{solidBoundaries:true});collision.bind(body);
        for(let i=0;i<3;i++)await collision.step(1/64);
        const shift=collision.selectRebaseShift([4096,-2048,1024]),initial=collision._bodyRebaseState(body),mass=body._actor.getMass();
        const fields=await rebaseFieldBytes(host),initialFrames=host.stats.frames,initialOrigin=origin.getOrigin();
        const untouched=async(label)=>{
            const next=await rebaseFieldBytes(host);next.forEach((bytes,index)=>identical(bytes,fields[index],`${label} changed GPU bytes`));
            identical(origin.getOrigin(),initialOrigin,`${label} changed origin`);
            identical(collision._bodyRebaseState(body).values,initial.values,`${label} changed native actor`);
        };
        await reject(()=>collision.prepareRebase([shift[0]+.25,0,0],{floatingOrigin:origin}),'Unaligned shift');await untouched('Invalid preparation');
        const pending=host.step(1/64);await reject(()=>host.prepareRebase(shift),'Pending native queue');await pending;
        cases.push({name:'rebase-pending-and-invalid-preparation-rejected',status:'PASS',evidence:{shift,invalidUnmutated:true,pendingRejected:true}});
        const stale=await collision.prepareRebase(shift,{floatingOrigin:origin});host.setScalarSources([]);
        await reject(()=>collision.commitPreparedRebase(stale),'Source-stale token');
        const moving=await collision.prepareRebase(shift,{floatingOrigin:origin});
        const velocity=new P.PxVec3(.5,.25,-.125);
        try{body._actor.setLinearVelocity(velocity,true);}finally{P.destroy(velocity);}
        await reject(()=>collision.commitPreparedRebase(moving),'Body-stale token');
        const changedContacts=await collision.prepareRebase(shift,{floatingOrigin:origin});
        world.contactEvents=[...world.contactEvents];await reject(()=>collision.commitPreparedRebase(changedContacts),'Contact ownership-stale token');
        const foreign=new FloatingOrigin(),originToken=origin.prepareRebase(shift);
        await reject(()=>foreign.commitPreparedRebase(originToken),'Foreign origin token');
        origin.originX=16;await reject(()=>origin.commitPreparedRebase(originToken),'Stale origin token');origin.originX=0;
        cases.push({name:'rebase-prepared-tokens-reject-source-body-contact-origin-staleness',status:'PASS',evidence:{rejections:5}});
        const atomicRejected=async(operation,label)=>{
            const bytes=await rebaseFieldBytes(host),pose=collision._bodyRebaseState(body).values,offset=origin.getOrigin();
            await reject(operation,label);
            const after=await rebaseFieldBytes(host);bytes.slice(0,2).forEach((value,index)=>identical(value,after[index],`${label} changed native atlas`));
            identical(collision._bodyRebaseState(body).values,pose,`${label} changed body`);identical(origin.getOrigin(),offset,`${label} changed origin`);
        };
        const cache=body.position;
        await atomicRejected(async()=>{body.position=Object.freeze([...cache]);try{await collision.prepareRebase(shift,{floatingOrigin:origin});}finally{body.position=cache;}},'Frozen body cache at prepare');
        await atomicRejected(async()=>{const value=await collision.prepareRebase(shift,{floatingOrigin:origin});body.position=Object.freeze([...cache]);
            try{await collision.commitPreparedRebase(value);}finally{body.position=cache;}},'Frozen body cache after prepare');
        const events=world.contactEvents;
        await atomicRejected(async()=>{world.contactEvents=[{contactPoints:[Object.freeze({position:[.25,0,0]})]}];
            try{await collision.prepareRebase(shift,{floatingOrigin:origin});}finally{world.contactEvents=events;}},'Frozen contact record');
        await atomicRejected(async()=>{const contact={position:[.25,0,0]};world.contactEvents=[{contactPoints:[contact]}];
            try{const value=await collision.prepareRebase(shift,{floatingOrigin:origin});Object.freeze(contact);await collision.commitPreparedRebase(value);}
            finally{world.contactEvents=events;}},'Contact frozen after prepare');
        collision.setAdditionalGeometry([{key:'rebase-guard',type:'box',position:[3,0,0],halfSize:[.1,.2,.3]}]);
        await atomicRejected(async()=>{const value=await collision.prepareRebase(shift,{floatingOrigin:origin});collision.additionalGeometry[0].halfSize[0]=.125;
            try{await collision.commitPreparedRebase(value);}finally{collision.additionalGeometry[0].halfSize[0]=.1;}},'Aliased additional geometry');
        collision.setAdditionalGeometry([]);
        await atomicRejected(async()=>{const value=await collision.prepareRebase(shift,{floatingOrigin:origin}),position=collision.boundaryGeometry[0].position[0];
            collision.boundaryGeometry[0].position[0]+=.25;try{await collision.commitPreparedRebase(value);}finally{collision.boundaryGeometry[0].position[0]=position;}},'Aliased submitted geometry');
        origin.setHierarchicalMode(true);
        await atomicRejected(async()=>{const value=await collision.prepareRebase(shift,{floatingOrigin:origin});origin.useHierarchical=false;
            try{await collision.commitPreparedRebase(value);}finally{origin.useHierarchical=true;}},'Changed hierarchical mode');
        await atomicRejected(async()=>{const value=await collision.prepareRebase(shift,{floatingOrigin:origin});origin.hierarchicalOrigin.localX=1;
            try{await collision.commitPreparedRebase(value);}finally{origin.hierarchicalOrigin.localX=0;}},'Changed hierarchical origin');
        cases.push({name:'rebase-cache-publication-geometry-and-hierarchy-rejections-are-atomic',status:'PASS',evidence:{rejections:8}});
        const beforeBody=collision._bodyRebaseState(body),beforeFields=await rebaseFieldBytes(host),frame=host.stats.frames;
        const token=await collision.prepareRebase(shift,{floatingOrigin:origin});let notified=0;
        origin.onRebase((...value)=>{
            identical(value,shift,'Observer shift');identical(origin.getOrigin(),shift,'Observer origin');
            identical(collision._bodyRebaseState(body).position,beforeBody.position.map((part,axis)=>Math.fround(part-shift[axis])),'Observer native position');notified++;
        });
        const completion=collision.commitPreparedRebase(token);
        // All three native coordinate commits precede the first await.
        identical(origin.getOrigin(),shift,'Origin commit was deferred');assert(notified===1,'Coordinate observer was not published once');
        identical(origin.hierarchicalOrigin.toWorld(),shift,'Hierarchical origin was not committed');
        const receipt=await completion,afterBody=collision._bodyRebaseState(body),afterFields=await rebaseFieldBytes(host);
        identical(beforeBody.values.slice(3),afterBody.values.slice(3),'Rebase changed orientation or velocities');
        assert(body._actor.getMass()===mass,'Rebase changed rigid mass');
        identical(beforeFields[0],afterFields[0],'Coordinated rebase changed density');identical(beforeFields[1],afterFields[1],'Coordinated rebase changed velocity');
        assert(frame===host.stats.frames&&origin.totalRebases===1&&receipt.advancedTime===0,'Rebase advanced a physical step');
        await reject(()=>collision.commitPreparedRebase(token),'Reused coupled token');await reject(()=>origin.commitPreparedRebase(originToken),'Old origin token');
        cases.push({name:'rebase-native-physx-flow-origin-commit-is-one-coordinate-transaction',status:'PASS',evidence:{receipt,mass,framesBefore:initialFrames,framesAtCommit:frame,
            beforeBody:beforeBody.values,afterBody:afterBody.values,atlasBytes:beforeFields.slice(0,2).map(value=>value.length),notified}});
        // The common clock can resume normally and no artificial velocity was introduced.
        stepPhysicsWorld(world,1/64);await collision.step(1/64);
        assert(host.stats.frames===frame+1,'Normal coupled step failed after rebase');
        cases.push({name:'rebase-shared-clock-resumes-after-native-coordinate-commit',status:'PASS',evidence:{frame:host.stats.frames,bodyPosition:collision._bodyRebaseState(body).position}});
        await collision.dispose();collision=null;await host.dispose();
        removeBody(world,body.handle);destroyPhysicsWorld(world);
        assert(world.destroyed&&world.bodies.size===0&&module._pr_flow_host_live()===before&&host.resources.size===0&&host.stats.allocatedBytes===0,'Coordinated native ownership leaked');
        return {cases,cleanup:{status:'PASS',nativeContextsBefore:before,nativeContextsAfter:module._pr_flow_host_live(),bodies:world.bodies.size,ownedGpuResources:0}};
    }finally{await collision?.dispose();await host?.dispose();if(!world.destroyed)destroyPhysicsWorld(world);}
}
