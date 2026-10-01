// SPDX-License-Identifier: MIT
import {FlowHostWebGpu} from './flow_host_webgpu.mjs';
import {rebaseFieldBytes} from './rebase_oracle.mjs';
import {FlowPhysXCollision} from '/engine/sim/FlowPhysXCollision.js';
import {captureFlowRigidBody,solveFlowMomentumExchange} from '/engine/sim/FlowMomentumExchange.js';
import {createPhysicsWorld,createBody,destroyPhysicsWorld} from '/engine/sim/physics/PhysXPhysicsWorld.js';
const assert=(value,message)=>{if(!value)throw Error(message);};
const near=(a,b,tolerance,label)=>assert(Math.abs(a-b)<=tolerance,`${label}: ${a} vs ${b}`);
const reject=async(fn,label)=>{let failed=false;try{await fn();}catch{failed=true;}assert(failed,`${label} accepted`);};
const same=(a,b,label)=>assert(a.length===b.length&&a.every((value,index)=>value===b[index]),label);
const rigid=(mass=6)=>({position:[0,0,0],mass,velocity:[0,0,0],angularVelocity:[0,0,0],inertia:[1,2,3],inertiaRotation:[0,0,0,1]});

// Independent atlas witness: enumerate each resident block's interior plus its
// one-cell halo by global integer cell address. Do not call the native scatter
// helper or infer correctness from a second interpolated velocity sample.
function scatterWitness(host,bytes,token,velocities){
    const table=new Uint32Array(bytes[2].buffer),level=host.output.level,extent=level.slice(0,3).map(value=>value+1),changes=new Map();
    token.gas.forEach((record,index)=>{
        const location=level[15]+record.cell[0]*4;
        const key=[table[location+3],...extent.map((size,axis)=>(table[location+axis]|0)*size+record.cell[axis+1])].join(',');
        assert(!changes.has(key),'Duplicate physical cell admission');changes.set(key,[...velocities[index],record.w]);
    });
    const expected=bytes[1].slice(),view=new DataView(expected.buffer),[width,height,depth]=host.output.velocity.size,pitch=Math.ceil(width*16/256)*256;
    let cells=0,halos=0;
    for(let block=0;block<level[7];block++){
        const packed=table[level[18]+block];if(!(packed&0x80000000))continue;
        const location=level[15]+block*4,base=[((packed<<1)|1)&4095,((packed>>>10)|1)&2047,((packed>>>20)|1)&2047];
        for(let z=-1;z<=extent[2];z++)for(let y=-1;y<=extent[1];y++)for(let x=-1;x<=extent[0];x++){
            const cell=[x,y,z],key=[table[location+3],...extent.map((size,axis)=>(table[location+axis]|0)*size+cell[axis])].join(','),value=changes.get(key);
            if(!value)continue;const p=base.map((part,axis)=>part+cell[axis]);
            assert(p[0]>=0&&p[0]<width&&p[1]>=0&&p[1]<height&&p[2]>=0&&p[2]<depth,'Atlas witness address invalid');
            const offset=(p[2]*height+p[1])*pitch+p[0]*16;value.forEach((part,channel)=>view.setFloat32(offset+channel*4,part,true));
            if(cell.some((part,axis)=>part<0||part>=extent[axis]))halos++;else cells++;
        }
    }
    return {expected,cells,halos};
}
function physicalWitness(token,velocities,before,after){
    const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]],dot=(a,b)=>a.reduce((sum,value,index)=>sum+value*b[index],0);
    const origin=before[0].position,deltaP=[0,0,0],deltaL=[0,0,0];let gasWork=0,bodyWork=0;
    token.gas.forEach((cell,index)=>{
        const change=velocities[index].map((value,axis)=>value-cell.velocity[axis]),impulse=change.map(value=>value*cell.mass);
        const angular=cross(cell.position.map((value,axis)=>value-origin[axis]),impulse);
        for(let axis=0;axis<3;axis++){deltaP[axis]+=impulse[axis];deltaL[axis]+=angular[axis];}
        gasWork+=.5*cell.mass*dot(change,velocities[index].map((value,axis)=>value+cell.velocity[axis]));
    });
    before.forEach((body,index)=>{
        const current=after[index],q=body.inertiaRotation,[x,y,z,w]=q;
        const R=[[1-2*y*y-2*z*z,2*x*y-2*z*w,2*x*z+2*y*w],[2*x*y+2*z*w,1-2*x*x-2*z*z,2*y*z-2*x*w],[2*x*z-2*y*w,2*y*z+2*x*w,1-2*x*x-2*y*y]];
        const I=R.map(row=>R.map(column=>row.reduce((sum,value,k)=>sum+value*column[k]*body.inertia[k],0)));
        const mul=vector=>I.map(row=>dot(row,vector));
        const dv=current.velocity.map((value,axis)=>value-body.velocity[axis]),dw=current.angularVelocity.map((value,axis)=>value-body.angularVelocity[axis]);
        const impulse=dv.map(value=>value*body.mass),spin=mul(dw),arm=body.position.map((value,axis)=>value-origin[axis]),angular=cross(arm,impulse);
        for(let axis=0;axis<3;axis++){deltaP[axis]+=impulse[axis];deltaL[axis]+=angular[axis]+spin[axis];}
        bodyWork+=.5*body.mass*dot(dv,current.velocity.map((value,axis)=>value+body.velocity[axis]))
            +.5*dot(dw,mul(current.angularVelocity.map((value,axis)=>value+body.angularVelocity[axis])));
    });
    return {deltaP,deltaL,gasWork,bodyWork};
}

export async function verifyFlowMomentum(module,device,shaderRoot){
    const cases=globalThis.flowMomentumProgress=[],before=module._pr_flow_host_live();let host,collision;
    const world=createPhysicsWorld({gravity:[0,0,0]});
    try{
        const gas=[{position:[0,0,0],mass:2,velocity:[2,0,0]}],body=[rigid()];
        const staged=solveFlowMomentumExchange(gas,body,[{gas:0,body:0,normal:[-1,0,0]}]);
        near(staged.gas[0].velocity[0],.5,0,'Analytic gas velocity');near(staged.bodies[0].velocity[0],.5,0,'Analytic body velocity');
        near(staged.receipt.heatObligationJ,3,1e-14,'Analytic inelastic heat');same(gas[0].velocity,[2,0,0],'Input mutated');
        const rotational=solveFlowMomentumExchange([{position:[0,2,0],mass:.25,velocity:[3,0,0]}],[rigid(2)],[{gas:0,body:0,normal:[-1,0,0]}]);
        assert(rotational.bodies[0].angularVelocity[2]<0,'Off-center paired torque missing');
        const invalid=JSON.stringify([gas,body]);await reject(()=>solveFlowMomentumExchange(gas,[{...rigid(),inertia:[0,1,1]}],[{gas:0,body:0,normal:[1,0,0]}]),'Singular inertia');
        assert(JSON.stringify([gas,body])===invalid,'Rejected physical input mutated');
        const coarseBody={...rigid(100),velocity:[1,0,0]},coarseGas={position:[0,0,0],mass:1,velocity:[Math.fround(1.01),0,0]};
        let roundingFailure='';try{solveFlowMomentumExchange([coarseGas],[coarseBody],[{gas:0,body:0,normal:[-1,0,0]}]);}catch(error){roundingFailure=String(error);}
        assert(roundingFailure.includes('native f32')||roundingFailure.includes('Native f32'),'Finite native rounding accuracy was not rejected');
        cases.push({name:'momentum-analytic-two-mass-and-angular-work',status:'PASS',evidence:{translation:staged.receipt,rotation:rotational.receipt,roundingFailure}});
        const deadline=performance.now()+20000;while(!world.ready&&performance.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));
        assert(world.ready,'PhysX initialization timeout');
        if(globalThis.expectedUnifiedFlowModule)assert(world.module===module&&module===globalThis.expectedUnifiedFlowModule,'Paired exchange did not use one unified native module');
        host=await FlowHostWebGpu.create(module,device,shaderRoot,{maxBlocks:32,cellSize:.125});
        host.setScene({layers:[{id:0,cellSize:.125,gravity:[0,0,0],pressure:false,combustion:false,vorticity:0}],emitters:[{
            id:1,type:'box',halfSize:[1,1,1],position:[0,0,0],velocity:[2,0,0],smoke:.1,fuel:0,temperature:0,burn:0,
            coupleRateSmoke:4,coupleRateVelocity:16,coupleRateTemperature:0,coupleRateFuel:0,coupleRateBurn:0}]});
        for(let i=0;i<3;i++)await host.step(1/64);
        const bodies=[createBody(world,{simMode:'dynamic',mass:2,position:[.75,-.375,0],linearVelocity:[-.5,0,0],
            collider:{shape:'box',halfExtents:[.0625,.25,.25]}}),createBody(world,{simMode:'dynamic',mass:3,position:[.75,.375,0],angularVelocity:[0,0,3],
            collider:{shape:'sphere',radius:.25}})];
        collision=new FlowPhysXCollision(world,host,{solidBoundaries:true,pairedMomentum:{densities:new Map([[0,1.2]])}});
        bodies.forEach(body=>collision.bind(body));
        const originalCommit=host.commitPreparedMomentum.bind(host),scatters=[];
        host.commitPreparedMomentum=async(token,velocities,synchronize)=>{
            const before=await rebaseFieldBytes(host),witness=scatterWitness(host,before,token,velocities);
            const rigidBefore=bodies.map(body=>captureFlowRigidBody(world,body));
            const result=await originalCommit(token,velocities,synchronize),after=await rebaseFieldBytes(host);
            same(witness.expected,after[1],'Actual native velocity atlas differs from independent interior/halo scatter');
            same(before[0],after[0],'Terminal exchange changed density');same(before[2],after[2],'Terminal exchange changed sparse addresses');
            const actualExchange=physicalWitness(token,velocities,rigidBefore,bodies.map(body=>captureFlowRigidBody(world,body)));
            scatters.push({cells:witness.cells,halos:witness.halos,velocityAtlasBytes:witness.expected.length});return {...result,actualExchange};
        };
        const report=await collision.step(1/64),receipt=report.momentum;
        assert(receipt.status==='EXCHANGED'&&receipt.cells>0&&receipt.contacts>0&&receipt.heatObligationJ>0,'Real resident-cell/body exchange absent');
        assert(receipt.bodies===2&&receipt.scope==='terminal-normal-exchange-only'&&receipt.heatDisposition==='unapplied-explicit-obligation','Physical scope receipt absent');
        assert(receipt.appliedMaximumClosingVelocity<=receipt.velocityTolerance+receipt.maximumRoundingVelocity,'Unbounded terminal penetration speed');
        assert(Math.hypot(...receipt.actualExchange.deltaP)<=receipt.budgets.linearImpulseNs,'Actual native linear impulse does not close');
        assert(Math.hypot(...receipt.actualExchange.deltaL)<=receipt.budgets.angularImpulseNms,'Actual native angular impulse does not close');
        assert(Math.abs(receipt.actualExchange.gasWork+receipt.actualExchange.bodyWork+receipt.heatObligationJ)<=receipt.budgets.energyJ,'Actual native kinetic/heat ledger does not close');
        near(receipt.actualExchange.gasWork,receipt.applied.gasWorkJ,1e-12,'Actual gas kinetic work');near(receipt.actualExchange.bodyWork,receipt.applied.bodyWorkJ,1e-12,'Actual body kinetic work');
        const actual=bodies.map(body=>captureFlowRigidBody(world,body));
        assert(actual[0].velocity[0]>-.5,'Translating body received no gas impulse');
        assert(actual[1].angularVelocity[2]!==3,'Rotating body received no gas torque');
        cases.push({name:'momentum-resident-gpu-cells-and-two-finite-native-bodies',status:'PASS',evidence:{receipt,actual:actual.map(({position,mass,velocity,angularVelocity,inertia})=>({position,mass,velocity,angularVelocity,inertia}))}});
        const beforeFields=await rebaseFieldBytes(host),beforeBodies=bodies.map(body=>captureFlowRigidBody(world,body).fingerprint);
        await reject(()=>host.prepareMomentumExchange(new Map([[0,1.2]]),{capacity:1}),'Truncated contact admission');
        const afterFields=await rebaseFieldBytes(host);beforeFields.slice(0,2).forEach((data,index)=>same(data,afterFields[index],'Overflow changed actual field'));
        bodies.forEach((body,index)=>same(captureFlowRigidBody(world,body).fingerprint,beforeBodies[index],'Overflow changed body'));
        const token=await host.prepareMomentumExchange(new Map([[0,1.2]]));
        await reject(()=>host.commitPreparedMomentum(token,token.gas.map(()=>[NaN,0,0])),'Nonfinite scatter');
        host.setScalarSources([]);await reject(()=>host.commitPreparedMomentum(token,token.gas.map(cell=>cell.velocity)),'Stale source token');
        const finalToken=await host.prepareMomentumExchange(new Map([[0,1.2]]));
        const densityBefore=(await rebaseFieldBytes(host))[0];
        await host.commitPreparedMomentum(finalToken,finalToken.gas.map(cell=>cell.velocity));
        same(densityBefore,(await rebaseFieldBytes(host))[0],'Momentum scatter changed scalar field');
        await reject(()=>host.commitPreparedMomentum(finalToken,finalToken.gas.map(cell=>cell.velocity)),'Consumed token');
        cases.push({name:'momentum-overflow-stale-invalid-and-consumed-admission-is-atomic',status:'PASS',evidence:{residentCells:finalToken.gas.length,contacts:finalToken.contacts.length,scalarBytes:densityBefore.length}});
        const actualPrepare=host.prepareMomentumExchange.bind(host),interventions=[];
        const withNativeVector=(values,operation)=>{const vector=new world.module.PxVec3(...values);try{operation(vector);}finally{world.module.destroy(vector);}};
        const savedMass=bodies[0]._actor.getMass(),savedInertia=captureFlowRigidBody(world,bodies[0]).inertia;
        const savedVelocity=captureFlowRigidBody(world,bodies[0]).velocity,savedCache=bodies[0].angularVelocity;
        const mutations=[
            ['mass',()=>bodies[0]._actor.setMass(savedMass*2),()=>bodies[0]._actor.setMass(savedMass)],
            ['principal-inertia',()=>withNativeVector(savedInertia.map(v=>v*2),v=>bodies[0]._actor.setMassSpaceInertiaTensor(v)),()=>withNativeVector(savedInertia,v=>bodies[0]._actor.setMassSpaceInertiaTensor(v))],
            ['native-velocity',()=>withNativeVector([7,0,0],v=>bodies[0]._actor.setLinearVelocity(v,true)),()=>withNativeVector(savedVelocity,v=>bodies[0]._actor.setLinearVelocity(v,true))],
            ['density-configuration',()=>collision.pairedMomentum.densities.set(0,2.4),()=>collision.pairedMomentum.densities.set(0,1.2)],
            ['frozen-public-cache',()=>{bodies[0].angularVelocity=Object.freeze([...savedCache]);},()=>{bodies[0].angularVelocity=savedCache;}],
        ];
        for(const [name,mutate,restore] of mutations){
            // Supply a real new terminal exchange before each intervention,
            // rather than testing vanishing float32 residual impulses.
            await host.step(1/64);
            const fields=await rebaseFieldBytes(host);let changed;
            host.prepareMomentumExchange=async(...args)=>{const value=await actualPrepare(...args);mutate();changed=bodies.map(body=>captureFlowRigidBody(world,body).fingerprint);return value;};
            try{
                let rejection='';try{await collision.exchangeMomentum();}catch(error){rejection=String(error);}
                const expected=name==='density-configuration'?'physical controls changed':name==='frozen-public-cache'?'velocity cache changed':'body mass, inertia, pose, velocity or geometry changed';
                assert(rejection.includes(expected),`Post-readback ${name} did not reach its admission guard: ${rejection}`);
                assert(changed,'Intervention never reached the actual completed GPU readback');
                const untouched=await rebaseFieldBytes(host);fields.forEach((value,index)=>same(value,untouched[index],'Rejected exchange changed actual GPU field'));
                bodies.forEach((body,index)=>same(changed[index],captureFlowRigidBody(world,body).fingerprint,'Rejected exchange applied an additional native body impulse'));
                interventions.push({name,rejection});
            }finally{host.prepareMomentumExchange=actualPrepare;restore();}
        }
        cases.push({name:'momentum-post-readback-native-mass-inertia-configuration-and-cache-rejection',status:'PASS',evidence:{interventions,actualGpuBytesAndNativeBodyStateUnchangedAfterEachIntervention:true}});
        const following=await collision.step(1/64);assert(following.momentum.status==='EXCHANGED','Physical step after exchange failed');
        assert(scatters.length>=3&&scatters.some(value=>value.halos>0),'Proof did not exercise actual halo aliases');
        cases.push({name:'momentum-native-halo-and-subsequent-step-lifecycle',status:'PASS',evidence:{receipt:following.momentum,scatters}});
        await collision.dispose();collision=null;await host.dispose();destroyPhysicsWorld(world);
        assert(module._pr_flow_host_live()===before&&host.resources.size===0&&host.stats.allocatedBytes===0&&world.bodies.size===0,'Paired exchange ownership leaked');
        return {cases,cleanup:{status:'PASS',nativeContextsBefore:before,nativeContextsAfter:module._pr_flow_host_live(),ownedGpuResources:0,bodies:0}};
    }finally{await collision?.dispose();await host?.dispose();if(!world.destroyed)destroyPhysicsWorld(world);}
}
