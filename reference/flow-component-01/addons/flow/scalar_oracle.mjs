// SPDX-License-Identifier: MIT
import { FlowHostWebGpu } from './flow_host_webgpu.mjs';
import { packScalarSources } from './flow_scalar_sources.mjs';
const assert=(condition,message)=>{if(!condition)throw new Error(message);};
const close=(actual,expected,label,relative=3e-5)=>assert(Math.abs(actual-expected)<=1e-9+relative*Math.max(Math.abs(actual),Math.abs(expected)),`${label}: ${actual} != ${expected}`);
const source=(id,extra={})=>({id,layer:0,type:'box',position:[.025,.035,.045],quaternion:[0,0,0,1],halfSize:[.003,.003,.003],totalRates:[.02,.04,.006,.008],...extra});
const layer=(cellSize=.1)=>({id:0,cellSize,gravity:[0,0,0],pressure:false,combustion:false,vorticity:0});
const allocation={id:1,type:'box',layer:0,position:[0,0,0],halfSize:[.4,.4,.4],velocity:[0,0,0],
    temperature:0,fuel:0,burn:0,smoke:0,coupleRateVelocity:0,coupleRateTemperature:0,coupleRateFuel:0,coupleRateBurn:0,coupleRateSmoke:0};

/** Independently integrate every resident native density cell, excluding atlas
 * halos. This reads the actual solver texture, without a presentation mask.
 * Flow exposes velocity level1; level0 shares locations and precedes its
 * global mapping by numLocations (verified pinned Sparse.cpp convention).
 */
export async function scalarFieldIntegral(host){
    const {device,output}=host,[width,height,depth]=output.density.size;
    assert(output.density.format==='rgba32float','Scalar proof requires native float32 density');
    const pitch=Math.ceil(width*16/256)*256;
    const tableRead=device.createBuffer({size:output.sparse.size,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ});
    const atlasRead=device.createBuffer({size:pitch*height*depth,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ});
    try{
        const encoder=device.createCommandEncoder();encoder.copyBufferToBuffer(output.sparse.buffer,0,tableRead,0,output.sparse.size);
        encoder.copyTextureToBuffer({texture:output.density.texture},{buffer:atlasRead,bytesPerRow:pitch,rowsPerImage:height},output.density.size);
        device.queue.submit([encoder.finish()]);await Promise.all([tableRead,atlasRead].map(buffer=>buffer.mapAsync(GPUMapMode.READ)));
        const table=new Uint32Array(tableRead.getMappedRange()),atlas=new Float32Array(atlasRead.getMappedRange());
        const level=output.level,block=level.slice(0,3).map(value=>2*(value+1)),mapping=level[18]-level[7];
        assert(mapping>=0&&mapping===Math.ceil((level[17]+level[7])/32)*32,'Invalid pinned native density mapping');
        const integral=[0,0,0,0],minimum=[Infinity,Infinity,Infinity,Infinity];let cells=0;
        for(let index=0;index<level[7];index++){
            const packed=table[mapping+index];if(!(packed&0x80000000))continue;
            const location=level[15]+index*4,layer=output.layers.find(item=>item.layerAndLevel===table[location+3]);
            if(!layer)continue;
            const volume=layer.blockSizeWorld.reduce((product,value,axis)=>product*value/block[axis],1);
            for(let z=0;z<block[2];z++)for(let y=0;y<block[1];y++)for(let x=0;x<block[0];x++){
                const px=(((packed<<1)|1)+x)&4095,py=(((packed>>>10)|1)+y)&2047,pz=(((packed>>>20)|1)+z)&2047;
                assert(px<width&&py<height&&pz<depth,'Native mapping exceeds actual density atlas');
                const offset=(pz*height+py)*pitch/4+px*4;
                for(let channel=0;channel<4;channel++){
                    const value=atlas[offset+channel];assert(Number.isFinite(value),'Nonfinite native scalar cell');
                    integral[channel]+=value*volume;minimum[channel]=Math.min(minimum[channel],value);
                }
                cells++;
            }
        }
        return {integral,minimum:minimum.map(value=>Number.isFinite(value)?value:0),cells,atlasSize:[width,height,depth]};
    }finally{for(const buffer of[tableRead,atlasRead]){if(buffer.mapState==='mapped')buffer.unmap();buffer.destroy();}}
}

function verifyReceipt(host,expected,dt){
    const receipt=host.scalarSourceReceipt;
    assert(receipt&&receipt.sources.length===expected.length,'Native scalar receipt omitted a source');
    close(receipt.dt,dt,'Native receipt timestep');
    for(const input of expected){
        const actual=receipt.sources.find(item=>item.id===input.id);assert(actual,'Native scalar receipt id mismatch');
        for(let channel=0;channel<4;channel++){
            const requested=input.enabled===false?0:input.totalRates[channel]*dt;
            close(actual.requested[channel],requested,'Integrated native request');
            close(actual.applied[channel]+actual.deferred[channel],actual.requested[channel],'Native receipt closure');
            const sign=Math.sign(requested);
            assert(sign*actual.applied[channel]>=-1e-12&&sign*actual.deferred[channel]>=-1e-12,'Native source overspent or changed sign');
            if(!sign)assert(actual.applied[channel]===0&&actual.deferred[channel]===0,'Zero channel became a source');
        }
    }
    return structuredClone(receipt);
}

export async function verifyScalarSources(module,device,shaderRoot){
    assert(module._pr_flow_host_scalar_abi?.()===1,'Native additive scalar ABI1 missing');
    const initial=module._pr_flow_host_live(),hosts=[],cases=globalThis.flowScalarProgress=[],cleanup={status:'RUNNING',nativeContextsBefore:initial};
    const make=async({cellSize=.1,allocate=true,boundaries=[]}={})=>{
        const host=await FlowHostWebGpu.create(module,device,shaderRoot,{maxBlocks:16,cellSize});hosts.push(host);
        host.setScene({layers:[layer(cellSize)],emitters:allocate?[allocation]:[]});host.setSolidBoundaries(boundaries);
        await host.step(1/60);return host;
    };
    const run=async(name,action)=>{const evidence=await action();cases.push({name,status:'PASS',evidence});console.log(`[scalar-native] ${name}: PASS`);};
    const trial=async(options,sources,dt=.025)=>{
        const host=await make(options);const before=await scalarFieldIntegral(host);
        assert(before.integral.every(value=>value===0),'Allocation control must have a cold zero field');
        host.setScalarSources(sources);await host.step(dt);
        const receipt=verifyReceipt(host,sources,dt),field=await scalarFieldIntegral(host);
        for(let channel=0;channel<4;channel++)close(field.integral[channel],receipt.sources.reduce((sum,item)=>sum+item.applied[channel],0),`Actual native atlas channel ${channel}`,8e-5);
        assert(field.minimum.every(value=>value>=0),'Signed source made a negative native scalar');
        await host.dispose();return {receipt,field};
    };
    try{
        await run('scalar-tiny-six-millimetre-source-does-not-require-a-cell-centre',async()=>{
            const result=await trial({},[source(1)]);
            assert(result.receipt.sources[0].applied[1]>0,'Subcell source failed admission');
            close(result.receipt.sources[0].applied[1],.04*.025,'Finite fuel admitted');return result;
        });
        await run('scalar-integrals-do-not-depend-on-cell-volume-or-timestep',async()=>{
            const coarse=await trial({cellSize:.1},[source(1)],.025),fine=await trial({cellSize:.05},[source(1,{totalRates:[.01,.02,.003,.004]})],.05);
            for(let channel=0;channel<4;channel++)close(coarse.field.integral[channel],fine.field.integral[channel],'Cell-volume/time normalization',8e-5);
            return {coarse,fine};
        });
        await run('scalar-overlapping-sources-add-and-cooling-defers-unavailable-heat',async()=>{
            const result=await trial({},[source(1,{totalRates:[.02,.04,0,0]}),source(2,{totalRates:[.03,.01,0,0]}),source(3,{totalRates:[-.08,0,0,0]})]);
            close(result.field.integral[0],0,'Available heat fully cooled');close(result.field.integral[1],.05*.025,'Overlapping fuel adds');
            close(result.receipt.sources[2].applied[0],-.05*.025,'Cooling actual applied');
            close(result.receipt.sources[2].deferred[0],-.03*.025,'Unmet cooling remains deferred');return result;
        });
        await run('scalar-no-resident-fluid-fully-defers-finite-obligations',async()=>{
            const result=await trial({allocate:false},[source(1)]);
            assert(result.field.cells===0&&result.receipt.sources[0].applied.every(value=>value===0),'Nonresident source was claimed deposited');
            const empty=await make({allocate:false});empty.setScalarSources([source(1)]);await empty.step(.025);
            verifyReceipt(empty,[source(1)],.025);empty.setScalarSources([]);await empty.step(.05);
            const removed=verifyReceipt(empty,[],.05);await empty.dispose();return {...result,removed};
        });
        await run('scalar-solid-interior-and-occluded-cell-admission-are-rejected',async()=>{
            const box={id:1,type:'box',position:[0,0,0],halfSize:[.2,.2,.2]};
            const result=await trial({boundaries:[box]},[source(1)]);
            assert(result.receipt.sources[0].applied.every(value=>value===0),'Source deposited inside a real solid');return result;
        });
        await run('scalar-exposed-face-renormalizes-only-over-real-fluid-samples',async()=>{
            const plane={id:1,type:'plane',position:[0,0,0],quaternion:[0,0,0,1]};
            const mixed=source(1,{position:[0,.05,.05],halfSize:[.1,.02,.02]});
            const result=await trial({boundaries:[plane]},[mixed]);
            close(result.receipt.sources[0].applied[1],mixed.totalRates[1]*.025,'Partly exposed face fuel');
            return result;
        });
        await run('scalar-invalid-native-setter-is-atomic-and-retains-prior-source',async()=>{
            const host=await make();const original=source(7);host.setScalarSources([original]);let rejected=0;
            const variants=[{id:0},{totalRates:[1,-1,0,0]},{halfSize:[0,.1,.1]},{quaternion:[0,0,0,0]},{layer:65534}];
            for(const change of variants){try{host.setScalarSources([source(8,change)]);}catch{rejected++;}}
            assert(rejected===variants.length,'Invalid scalar setter was admitted');
            const packet=packScalarSources([source(8)],new Set([0])).packet;
            const pointer=module._malloc(packet.byteLength*2);assert(pointer,'Native rejection packet allocation failed');let nativeRejected=0;
            try{
                for(const [kind,offset,value] of [['u32',0,0],['u32',1,65534],['f32',17,-1],['f32',12,0],['f32',11,0]]){
                    module.HEAPU8.set(new Uint8Array(packet),pointer);
                    (kind==='u32'?module.HEAPU32:module.HEAPF32)[pointer/4+offset]=value;
                    nativeRejected+=Number(module._pr_flow_host_scalar_sources(host.handle,pointer,1)===0);
                }
                module.HEAPU8.set(new Uint8Array(packet),pointer);module.HEAPU8.set(new Uint8Array(packet),pointer+packet.byteLength);
                nativeRejected+=Number(module._pr_flow_host_scalar_sources(host.handle,pointer,2)===0);
                nativeRejected+=Number(module._pr_flow_host_scalar_sources(host.handle,module.HEAPU8.length-4,1)===0);
            }finally{module._free(pointer);}
            assert(nativeRejected===7,'Malformed raw native source packet was admitted');
            const frame=host.stats.frames;let badStep=false;try{await host.step(2);}catch{badStep=true;}
            assert(badStep&&host.stats.frames===frame,'Out-of-range source timestep changed native state');await host.step(.025);
            const receipt=verifyReceipt(host,[original],.025);await host.dispose();return {rejected,nativeRejected,receipt};
        });
        await run('scalar-overflowing-positive-cell-sum-remains-finite-and-deferred',async()=>{
            const inputs=Array.from({length:32},(_,index)=>source(index+1,{totalRates:[3e38,0,0,0]}));
            const result=await trial({},inputs,.1);
            assert(result.receipt.sources.every(item=>item.applied.every(value=>value===0)
                &&item.deferred.every(Number.isFinite)),'Finite individual budgets overflowed or disappeared');return result;
        });
        return {status:'PASS',flowScalarSourceAbi:1,cases,cleanup};
    }catch(error){return {status:'FAIL',flowScalarSourceAbi:1,cases,cleanup,error:error.stack||String(error)};}
    finally{
        for(const host of hosts)await host.dispose();
        assert(module._pr_flow_host_live()===initial,'Scalar proof leaked a native Flow context');
        assert(hosts.every(host=>host.resources.size===0),'Scalar proof leaked owned GPU resources');
        Object.assign(cleanup,{status:'PASS',nativeContextsAfter:module._pr_flow_host_live(),ownedGpuResources:hosts.reduce((sum,host)=>sum+host.resources.size,0)});
    }
}
