// SPDX-License-Identifier: MIT
import { FlowHostWebGpu, solidBoundaryWGSL } from './flow_host_webgpu.mjs';
import { packSolidBoundaries } from './flow_solid_boundary.mjs';
import { verifyVelocityGather } from './velocity_gather_oracle.mjs';
const assert=(value,message)=>{if(!value)throw new Error(message);};
const layer=(pressure=true)=>({id:0,cellSize:.1,gravity:[0,0,0],pressure,combustion:false,vorticity:0});
const emitter=(extra={})=>({id:1,layer:0,type:'box',position:[-.4,.05,.05],halfSize:[.3,.35,.35],
    velocity:[2,0,0],temperature:0,fuel:0,smoke:.8,burn:0,coupleRateVelocity:120,coupleRateTemperature:0,
    coupleRateFuel:0,coupleRateSmoke:120,coupleRateBurn:0,...extra});
const slab=(extra={})=>({id:1,layer:0,type:'box',position:[0,0,0],halfSize:[.0015,3,3],...extra});
// A broad real velocity source supplies through-flow across the smoke source;
// pressure remains enabled and the solid operator must constrain this forcing.
const wind=(extra={})=>emitter({id:99,position:[0,.05,.05],halfSize:[1.4,.8,.8],smoke:0,coupleRateSmoke:0,...extra});
async function frames(host,count,dt=1/30){for(let i=0;i<count;i++)await host.step(dt);}

/** Read native cell centres directly, independently of presentation masking. */
async function densityCells(host,positions,layerId=0){
    const {device,output}=host,[width,height,depth]=output.density.size,pitch=Math.ceil(width*16/256)*256;
    const tableRead=device.createBuffer({size:output.sparse.size,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ});
    const atlasRead=device.createBuffer({size:pitch*height*depth,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ});
    try{
        const encoder=device.createCommandEncoder();encoder.copyBufferToBuffer(output.sparse.buffer,0,tableRead,0,output.sparse.size);
        encoder.copyTextureToBuffer({texture:output.density.texture},{buffer:atlasRead,bytesPerRow:pitch,rowsPerImage:height},output.density.size);
        device.queue.submit([encoder.finish()]);await Promise.all([tableRead,atlasRead].map(b=>b.mapAsync(GPUMapMode.READ)));
        const table=new Uint32Array(tableRead.getMappedRange()),atlas=new Float32Array(atlasRead.getMappedRange()),level=output.level;
        const block=level.slice(0,3).map(v=>2*(v+1)),mapping=Math.ceil((level[17]+level[7])/32)*32;
        const meta=output.layers.find(row=>row.id===layerId);
        assert(meta,`No native metadata for layer ${layerId}`);
        return positions.map(position=>{
            const cell=position.map((v,i)=>Math.floor(v*block[i]/meta.blockSizeWorld[i]));
            const location=cell.map((v,i)=>Math.floor(v/block[i])),bucket=location.map((v,i)=>v&level[8+i]);
            const hash=((bucket[2]<<level[13])|(bucket[1]<<level[12])|bucket[0])>>>0;
            for(let i=table[hash*2];i<table[hash*2+1];i++){
                const address=level[15]+i*4;
                if(location.some((v,j)=>v!==(table[address+j]|0))||table[address+3]!==meta.layerAndLevel)continue;
                const packed=table[mapping+i];if(!(packed&0x80000000))break;
                const local=cell.map((v,j)=>((v%block[j])+block[j])%block[j]);
                const p=[((packed*2+1)+local[0])&4095,(((packed>>>10)|1)+local[1])&2047,(((packed>>>20)|1)+local[2])&2047];
                const offset=(p[2]*height+p[1])*pitch/4+p[0]*4;const value=Array.from(atlas.subarray(offset,offset+4));
                assert(value.every(Number.isFinite),'Nonfinite native density');return value;
            }return [0,0,0,0];
        });
    }finally{for(const b of[tableRead,atlasRead]){if(b.mapState==='mapped')b.unmap();b.destroy();}}
}

async function queryGeometry(host){
    const device=host.device,module=device.createShaderModule({code:solidBoundaryWGSL({binding:0})+`
        @group(0) @binding(1) var<storage,read_write> answer:array<vec4f>;
        @compute @workgroup_size(1) fn main(){
            answer[0]=vec4f(solidTrace(0u,vec3f(-1,0,0),vec3f(1,0,0)),
                solidTrace(0u,vec3f(-1,3,0),vec3f(1,3,0)),
                select(0.0,1.0,solidInside(0u,vec3f(0))),
                solidTrace(1u,vec3f(-1,0,0),vec3f(1,0,0)));
        }`});
    const info=await module.getCompilationInfo();assert(!info.messages.some(m=>m.type==='error'),JSON.stringify(info.messages));
    const pipeline=await device.createComputePipelineAsync({layout:'auto',compute:{module,entryPoint:'main'}});
    const values=device.createBuffer({size:16,usage:GPUBufferUsage.STORAGE|GPUBufferUsage.COPY_SRC});
    const read=device.createBuffer({size:16,usage:GPUBufferUsage.MAP_READ|GPUBufferUsage.COPY_DST});
    try{const group=device.createBindGroup({layout:pipeline.getBindGroupLayout(0),entries:[{binding:0,resource:{buffer:host.output.boundaries.buffer}},{binding:1,resource:{buffer:values}}]});
        const encoder=device.createCommandEncoder(),pass=encoder.beginComputePass();pass.setPipeline(pipeline);pass.setBindGroup(0,group);pass.dispatchWorkgroups(1);pass.end();
        encoder.copyBufferToBuffer(values,0,read,0,16);device.queue.submit([encoder.finish()]);await read.mapAsync(GPUMapMode.READ);
        const result=Array.from(new Float32Array(read.getMappedRange()));read.unmap();return result;
    }finally{values.destroy();read.destroy();}
}

async function verifyNativeMotionBounds(host){
    const read=host.device.createBuffer({size:32,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ});
    const bounds=async()=>{
        const encoder=host.device.createCommandEncoder();encoder.copyBufferToBuffer(host.output.boundaries.buffer,32,read,0,32);
        host.device.queue.submit([encoder.finish()]);await read.mapAsync(GPUMapMode.READ);
        const values=Array.from(new Float32Array(read.getMappedRange()));read.unmap();return {lower:values.slice(0,3),upper:values.slice(4,7)};
    };
    try{
        const wall=slab({halfSize:[.0015,1,1]});host.setSolidBoundaries([wall]);await frames(host,2);
        const stationary=await bounds();assert(Math.abs(stationary.lower[0]+.0015)<1e-8&&Math.abs(stationary.upper[0]-.0015)<1e-8,'Stationary wall retained an inflated swept sphere');
        host.setSolidBoundaries([{...wall,quaternion:[0,0,0,-1]}]);await host.step();
        const equivalentQuaternion=await bounds();assert(JSON.stringify(equivalentQuaternion)===JSON.stringify(stationary),'Quaternion sign invented physical motion');
        host.setSolidBoundaries([{...wall,position:[.5,0,0]}]);await host.step();
        const translated=await bounds();assert(translated.lower[0]<=-.0015&&translated.upper[0]>=.5015,'Translated wall lost its swept bounds');
        await host.step();const settled=await bounds();assert(Math.abs(settled.lower[0]-.4985)<1e-7&&Math.abs(settled.upper[0]-.5015)<1e-7,'Settled wall did not recover tight bounds');
        const angle=Math.PI/2;host.setSolidBoundaries([{...wall,position:[.5,0,0],quaternion:[0,0,Math.sin(angle/2),Math.cos(angle/2)]}]);await host.step();
        const rotated=await bounds();
        for(let corner=0;corner<4;corner++){
            const x=(corner&1)?.0015:-.0015,y=(corner&2)?1:-1,c=Math.cos(angle/2),s=Math.sin(angle/2);
            const p=[.5+c*x-s*y,s*x+c*y,0];assert(p.every((v,i)=>v>=rotated.lower[i]&&v<=rotated.upper[i]),'Rotating wall lost its intermediate sweep');
        }
        return {stationary,equivalentQuaternion,translated,settled,rotated};
    }finally{if(read.mapState==='mapped')read.unmap();read.destroy();}
}

/** Compare the actual native divergence cache with independent point/segment
 * queries on the GPU, before its original buffers are recycled by the graph. */
async function verifyPressureFaceCache(host){
    const {device}=host,shader=device.createShaderModule({code:solidBoundaryWGSL({binding:0})+`
        @group(0) @binding(1) var<storage,read> params:array<u32>;
        @group(0) @binding(2) var<storage,read> table:array<u32>;
        @group(0) @binding(3) var<storage,read> faces:array<u32>;
        @group(0) @binding(4) var<storage,read_write> result:array<atomic<u32>>;
        @compute @workgroup_size(128) fn main(@builtin(global_invocation_id) id:vec3u){
            if(id.x>=params[7]){return;}
            let block=params[0]+id.y;let offset=params[19]+block*4u;
            let location=bitcast<vec3i>(vec3u(table[offset],table[offset+1u],table[offset+2u]));
            let mask=vec3u(params[4],params[5],params[6]);let dims=vec3f(mask+1u);
            let cell=vec3u(id.x,id.x>>params[8],id.x>>(params[8]+params[9]))&mask;
            let row=solidBoundary[solidBoundary[1].x+table[params[24]+block]];
            let blockSize=bitcast<vec3f>(row.xyz);let world=(vec3f(location)+(vec3f(cell)+.5)/dims)*blockSize;
            var expected=0u;
            if(!solidInside(row.w,world)){
                for(var axis=0u;axis<3u;axis++){for(var side=0u;side<2u;side++){
                    var delta=vec3f(0);delta[axis]=select(-1.0,1.0,side==1u)*(blockSize/dims)[axis];
                    if(solidTrace(row.w,world,world+delta)>=1.0){expected|=1u<<(axis*2u+side);}
                }}
            }else{atomicAdd(&result[4],1u);}
            atomicAdd(&result[0],1u);
            if(faces[block*params[7]+id.x]!=expected){atomicAdd(&result[1],1u);}
            if(expected==63u){atomicAdd(&result[2],1u);}else{atomicAdd(&result[3],1u);}
        }`});
    const pipeline=await device.createComputePipelineAsync({layout:'auto',compute:{module:shader,entryPoint:'main'}});
    const values=device.createBuffer({size:20,usage:GPUBufferUsage.STORAGE|GPUBufferUsage.COPY_SRC});
    const read=device.createBuffer({size:20,usage:GPUBufferUsage.MAP_READ|GPUBufferUsage.COPY_DST});
    const original=host.call;let builds=0,jacobi=0;
    host.call=function(op,a,b,c,d,e,f){
        const returned=original.call(this,op,a,b,c,d,e,f);
        if(op!==7 || !d || !e || !f)return returned;
        const resource=this._get(a);
        if(resource.name.endsWith('/SolidPressureJacobiCS.wgsl'))jacobi++;
        if(resource.name.endsWith('/SolidPressureDivergenceCS.wgsl')){
            const writes=this._words(b,c*3),bindings=new Map();
            for(let i=0;i<writes.length;i+=3)bindings.set(writes[i],this._get(writes[i+2]).buffer);
            const reflection=this.shaders.get(resource.name).reflection.parameters;
            const named=name=>bindings.get(reflection.find(p=>p.name===name).binding.index);
            const entries=[bindings.get(30),named('gParams'),named('gTable'),bindings.get(32),values]
                .map((buffer,binding)=>({binding,resource:{buffer}}));
            const group=device.createBindGroup({layout:pipeline.getBindGroupLayout(0),entries});
            const pass=this._encoder().beginComputePass({label:'Native pressure face cache oracle'});
            pass.setPipeline(pipeline);pass.setBindGroup(0,group);pass.dispatchWorkgroups(d,e,f);pass.end();builds++;
        }
        return returned;
    };
    try{
        const floor={id:2,type:'plane',position:[0,-.4,0],quaternion:[0,0,Math.SQRT1_2,Math.SQRT1_2]};
        host.setSolidBoundaries([slab(),floor]);await frames(host,2);
        host.setSolidBoundaries([slab({position:[.12,0,0],quaternion:[0,0,Math.sin(.13),Math.cos(.13)]}),floor]);await host.step();
        host.setSolidBoundaries([floor]);await host.step();
        const encoder=device.createCommandEncoder();encoder.copyBufferToBuffer(values,0,read,0,20);device.queue.submit([encoder.finish()]);
        await read.mapAsync(GPUMapMode.READ);const [cells,mismatches,openCells,barrierCells,interiorCells]=new Uint32Array(read.getMappedRange());
        assert(cells>0&&mismatches===0&&openCells>0&&barrierCells>0&&interiorCells>0,'Native pressure mask disagrees with exact geometry');
        assert(builds>0&&jacobi===builds*40,`Pressure iteration count changed: ${jacobi}/${builds}`);
        return {cells,mismatches,openCells,barrierCells,interiorCells,cacheBuilds:builds,jacobiPasses:jacobi};
    }finally{host.call=original;if(read.mapState==='mapped')read.unmap();read.destroy();values.destroy();}
}

export async function verifyFlowSolids(module,device,shaderRoot,{quick=false}={}){
    assert(module._pr_flow_host_solid_abi?.()===1,'Native solid ABI1 missing');
    const cases=globalThis.flowSolidProgress=[],hosts=[],initial=module._pr_flow_host_live();
    const create=async(scene)=>{const h=await FlowHostWebGpu.create(module,device,shaderRoot,{maxBlocks:32,cellSize:.1});hosts.push(h);h.setScene(scene);return h;};
    const record=(name,numericEvidence)=>cases.push({name,status:'PASS',numericEvidence});
    try{
        const empty=await create({layers:[layer()],emitters:[]});empty.setSolidBoundaries([slab()]);await empty.step();
        assert(empty.output.boundaries?.abi===1&&empty.stats.activeBlocks===0,'Empty scene boundary publication failed');
        record('solid-empty-scene-publication',{activeBlocks:empty.stats.activeBlocks,bytes:empty.output.boundaries.size});
        await empty.dispose();
        const solid=await create({layers:[layer()],emitters:[emitter({position:[0,0,0],halfSize:[.6,.6,.6],applyPostPressure:true})]});
        solid.setSolidBoundaries([slab({halfSize:[.25,.25,.25]})]);await frames(solid,3);
        const inside=await densityCells(solid,[[.05,.05,.05],[-.05,-.05,-.05],[.45,.05,.05]]);
        assert(inside.slice(0,2).every(v=>v.every(x=>x===0))&&inside[2][3]>.1,`Native solid exclusion failed: ${inside}`);
        record('solid-source-interior-exclusion',{inside});
        solid.setSolidBoundaries([slab()]);await solid.step();const geometry=await queryGeometry(solid);
        assert(Math.abs(geometry[0]-.49925)<1e-5&&geometry[1]===1&&geometry[2]===1&&geometry[3]===1,`Shared geometry mismatch: ${geometry}`);
        record('solid-presentation-query-parity',{geometry});await solid.dispose();
        if(!quick){
            const bounded=await create({layers:[layer()],emitters:[]});
            record('solid-stationary-bounds-preserve-translation-and-rotation-sweeps',await verifyNativeMotionBounds(bounded));await bounded.dispose();
            const cached=await create({layers:[layer()],emitters:[emitter()]});
            record('solid-pressure-face-cache-matches-exact-geometry',await verifyPressureFaceCache(cached));await cached.dispose();
            const trial=async(boundaries)=>{const h=await create({layers:[layer()],emitters:[emitter(),wind()]});h.setSolidBoundaries(boundaries);await frames(h,24);
                const points=[[-.15,.05,.05],[.15,.05,.05],[.35,.05,.05],[.15,.25,.05]],values=await densityCells(h,points);
                const velocities=Array.from(await h.sampleVelocity(new Float32Array(points.flat()),{layer:0}));
                const stats=structuredClone(h.stats);await h.dispose();return {points,values,velocities,stats};};
            const control=await trial([]),closed=await trial([slab()]);
            assert(control.values[1][3]>.02,`Open control did not transport smoke to witness: ${JSON.stringify({control,closed})}`);
            assert(closed.values[0][3]>.02&&closed.values.slice(1).every(v=>v[3]<1e-6),`Sealed3mmwall leaked native smoke: ${JSON.stringify(closed.values)}`);
            record('solid-thin-slab-no-through',{control:control.values,closed:closed.values,passes:closed.stats.passes});
            const pressure=await create({layers:[layer()],emitters:[emitter()]});pressure.setSolidBoundaries([slab()]);await frames(pressure,12);
            const normalVelocity=Array.from(await pressure.sampleVelocity(new Float32Array([-.1,.1,.1,.1,.1,.1]),{layer:0}));
            assert(Math.abs(normalVelocity[0])<1e-5&&Math.abs(normalVelocity[3])<1e-5,`Pressure face normal flow: ${normalVelocity}`);
            record('solid-pressure-no-through-flow',{normalVelocity,passes:pressure.stats.passes});await pressure.dispose();
            const angle=.22,rotated=await trial([slab({quaternion:[0,0,Math.sin(angle/2),Math.cos(angle/2)]})]);
            assert(rotated.values.slice(1).every(v=>v[3]<1e-6),'Rotated thin slab leaked');record('solid-rotated-slab-no-through',{values:rotated.values,angle});
            const aperture=[slab({id:1,position:[0,1.7,0],halfSize:[.0015,1.3,3]}),slab({id:2,position:[0,-1.7,0],halfSize:[.0015,1.3,3]}),
                slab({id:3,position:[0,0,1.7],halfSize:[.0015,.4,1.3]}),slab({id:4,position:[0,0,-1.7],halfSize:[.0015,.4,1.3]})];
            const opened=await trial(aperture);assert(opened.values[1][3]>.02,'Real aperture blocked transport');record('solid-real-aperture-transport',{values:opened.values});
            const convex={id:1,type:'convex',position:[0,0,0],planes:[[1,0,0,.0015],[-1,0,0,.0015],[0,1,0,3],[0,-1,0,3],[0,0,1,3],[0,0,-1,3]],bounds:[[-.0015,-3,-3],[.0015,3,3]]};
            const hull=await trial([convex]);assert(hull.values.slice(1).every(v=>v[3]<1e-6),'Exact convex slab leaked');record('solid-convex-no-through',{values:hull.values});
            const border=await create({layers:[layer()],emitters:[emitter({position:[2.8,.05,.05]}),wind({position:[3.2,.05,.05]})]});border.setSolidBoundaries([slab({position:[3.2,0,0]})]);await frames(border,24);
            const borderValues=await densityCells(border,[[3.05,.05,.05],[3.35,.05,.05]]);
            assert(borderValues[0][3]>.02&&borderValues[1][3]<1e-6,'Sparse block boundary leaked smoke');
            record('solid-sparse-border-no-through',{borderValues,blockSizeWorld:border.output.layers[0].blockSizeWorld});await border.dispose();
            const isolated=await create({layers:[layer(),{...layer(),id:1}],emitters:[emitter(),wind(),emitter({id:2,layer:1}),wind({id:100,layer:1})]});
            isolated.setSolidBoundaries([slab()]);await frames(isolated,24);
            const sealedLayer=await densityCells(isolated,[[.15,.05,.05]],0),openLayer=await densityCells(isolated,[[.15,.05,.05]],1);
            assert(sealedLayer[0][3]<1e-6&&openLayer[0][3]>.02,'Boundary layer isolation failed');
            record('solid-layer-isolation',{sealedLayer,openLayer});await isolated.dispose();
            for(const shape of[{id:1,type:'sphere',radius:.3},{id:1,type:'plane',quaternion:[0,0,1,0]}]){
                const h=await create({layers:[layer()],emitters:[emitter({position:[0,0,0],halfSize:[.7,.7,.7]})]});h.setSolidBoundaries([shape]);await frames(h,3);
                const values=await densityCells(h,[[.05,.05,.05],[-.55,.05,.05]]);
                assert(values[0].every(v=>v===0)&&values[1][3]>.1,`${shape.type} native scalar exclusion failed`);
                record(`solid-${shape.type}-native-exclusion`,{values});await h.dispose();
            }
            const transaction=await create({layers:[layer()],emitters:[]});transaction.setSolidBoundaries([slab()]);await transaction.step();
            const version=transaction.output.boundaries.version,failures=[];
            for(const invalid of[[slab({id:0})],[slab({layer:2})],[slab(),slab()],[slab({quaternion:[0,0,0,0]})],[slab({halfSize:[-.1,1,1]})]]){
                let rejected=false;try{transaction.setSolidBoundaries(invalid);}catch{rejected=true;}failures.push(rejected);
            }
            assert(failures.every(Boolean),'Invalid boundary transaction admitted');
            const packet=packSolidBoundaries([slab()],new Set([0])).packet;
            const pointer=module._malloc(packet.byteLength*2);assert(pointer,'Native boundary packet allocation failed');let nativeRejected=0;
            try{
                for(const [kind,offset,value] of [['u32',33,0],['u32',0,65534],['f32',12,-1],['f32',11,0]]){
                    module.HEAPU8.set(new Uint8Array(packet),pointer);
                    (kind==='u32'?module.HEAPU32:module.HEAPF32)[pointer/4+offset]=value;
                    nativeRejected+=Number(module._pr_flow_host_solids(transaction.handle,pointer,1,0,0)===0);
                }
                module.HEAPU8.set(new Uint8Array(packet),pointer);module.HEAPU8.set(new Uint8Array(packet),pointer+packet.byteLength);
                nativeRejected+=Number(module._pr_flow_host_solids(transaction.handle,pointer,2,0,0)===0);
                nativeRejected+=Number(module._pr_flow_host_solids(transaction.handle,module.HEAPU8.length-4,1,0,0)===0);
            }finally{module._free(pointer);}
            assert(nativeRejected===6,'Malformed raw native boundary packet was admitted');await transaction.step();
            assert(transaction.output.boundaries.version===version,'Rejected transaction changed native geometry');
            record('solid-transaction-rejection',{rejected:failures.length,nativeRejected,version});await transaction.dispose();
            const moving=await create({layers:[layer()],emitters:[emitter({position:[0,0,0],halfSize:[1,1,1],velocity:[0,0,0]})]});await frames(moving,3);
            const box=slab({position:[-.4,0,0],halfSize:[.12,.4,.4]});moving.setSolidBoundaries([box]);await moving.step();
            moving.setSolidBoundaries([{...box,position:[0,0,0]}]);await moving.step(.05);
            const swept=await densityCells(moving,[[.05,.05,.05],[-.05,.05,.05]]);assert(swept.every(v=>v.every(x=>x===0)),'Translated solid contains native smoke');
            const speed=Array.from(await moving.sampleVelocity(new Float32Array([.05,.05,.05]),{layer:0}));
            assert(speed[0]>0&&speed.every(Number.isFinite),'Moving-wall velocity absent');record('solid-moving-translation',{swept,speed});
            assert(Math.abs(speed[0]-8)<1e-5&&Math.abs(speed[1])<1e-5&&Math.abs(speed[2])<1e-5,'Interior gather must equal exact moving-wall velocity');
            await moving.dispose();
            const gather=await create({layers:[layer()],emitters:[]});gather.setSolidBoundaries([slab()]);await gather.step();
            record('solid-velocity-gather-rejects-cross-wall-donors',await verifyVelocityGather(gather,{boundaries:true}));await gather.dispose();
            const clockTrial=async(exact,dt)=>{
                const h=await create({layers:[layer(false)],emitters:[emitter({position:[0,0,0],halfSize:[.5,.5,.5],smoke:0,velocity:[0,0,0],coupleRateVelocity:0})]});
                if(exact)h.setSolidBoundaries([slab({position:[10,0,0]})]);await h.step(1/60);
                if(exact)h.setSolidBoundaries([]);
                h.setScene({layers:[layer(false)],emitters:[emitter({position:[0,0,0],halfSize:[.5,.5,.5],smoke:1,coupleRateSmoke:10,velocity:[0,0,0],coupleRateVelocity:0})]});
                await h.step(dt);const values=await densityCells(h,[[.05,.05,.05]]);await h.dispose();return values[0][3];
            };
            const exactSmall=await clockTrial(true,.005),exactLarge=await clockTrial(true,.01),legacySmall=await clockTrial(false,.005);
            assert(exactSmall>0&&exactLarge>exactSmall&&exactSmall/exactLarge>.45&&exactSmall/exactLarge<.55&&legacySmall===0,
                `Requested-step isolation failed: ${JSON.stringify({exactSmall,exactLarge,legacySmall})}`);
            record('solid-exact-requested-step-persists-after-removal',{exactSmall,exactLarge,legacySmall});
            const rotating=await create({layers:[layer()],emitters:[emitter({position:[-.4,.2,0],halfSize:[.3,.6,.4],velocity:[0,0,0]})]});
            rotating.setSolidBoundaries([slab()]);await frames(rotating,3);
            const witnesses=[[-.15,.35,.05],[-.35,-.35,.05],[.15,-.15,.05]],before=await densityCells(rotating,witnesses);
            assert(before[0][3]>.1,'Rotation swept witness was not seeded with native smoke');
            rotating.setScene({layers:[layer()],emitters:[]});
            rotating.setSolidBoundaries([slab({quaternion:[0,0,Math.sin(Math.PI/8),Math.cos(Math.PI/8)]})]);await rotating.step(.05);
            const after=await densityCells(rotating,witnesses);
            assert(after[0][3]<1e-6&&after[2].every(v=>Math.abs(v)<1e-6)&&after[1][3]>.01,`Rotating wall exclusion failed: ${JSON.stringify({before,after})}`);
            record('solid-moving-rotation',{before,after,angle:Math.PI/4});await rotating.dispose();
        }
    }finally{for(const host of hosts)await host.dispose();}
    assert(module._pr_flow_host_live()===initial&&hosts.every(h=>h.stats.allocatedBytes===0&&h.resources.size===0),'Solid native resources leaked');
    return {flowSolidBoundaryAbi:1,cleanup:'PASS',cases};
}
