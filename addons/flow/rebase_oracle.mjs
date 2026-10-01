// SPDX-License-Identifier: MIT
import {FlowHostWebGpu} from './flow_host_webgpu.mjs';
import {scalarFieldIntegral} from './scalar_oracle.mjs';
const assert=(value,message)=>{if(!value)throw Error(message);};
const close=(a,b,label,tolerance=2e-5)=>assert(Math.abs(a-b)<=tolerance*Math.max(1,Math.abs(a),Math.abs(b)),`${label}: ${a} != ${b}`);
const layer=id=>({id,cellSize:id===2?.25:.125,gravity:[0,0,0],pressure:true,combustion:false,vorticity:0});
const source=(id=1,layer=0)=>({id,layer,type:'box',position:[.25,.25,.25],halfSize:[.375,.375,.375],velocity:[.25,.125,0],temperature:0,fuel:0,burn:0,smoke:.4,
    coupleRateVelocity:12,coupleRateTemperature:0,coupleRateFuel:0,coupleRateBurn:0,coupleRateSmoke:12});
const wall={id:1,layer:0,type:'box',position:[.875,0,0],halfSize:[.015625,2,2]};
const scalar={id:1,layer:0,type:'box',position:[.25,.25,.25],halfSize:[.03125,.03125,.03125],totalRates:[.01,.02,0,.03]};

export async function rebaseFieldBytes(host) {
    const {device,output}=host,reads=[];
    try {
        const encoder=device.createCommandEncoder();
        for(const resource of [output.density,output.velocity]) {
            const [width,height,depth]=resource.size,pitch=Math.ceil(width*16/256)*256;
            const buffer=device.createBuffer({size:pitch*height*depth,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ});
            reads.push(buffer);encoder.copyTextureToBuffer({texture:resource.texture},{buffer,bytesPerRow:pitch,rowsPerImage:height},resource.size);
        }
        const table=device.createBuffer({size:output.sparse.size,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ});reads.push(table);
        encoder.copyBufferToBuffer(output.sparse.buffer,0,table,0,output.sparse.size);device.queue.submit([encoder.finish()]);
        await Promise.all(reads.map(buffer=>buffer.mapAsync(GPUMapMode.READ)));
        return reads.map(buffer=>new Uint8Array(buffer.getMappedRange()).slice());
    }finally{for(const buffer of reads){if(buffer.mapState==='mapped')buffer.unmap();buffer.destroy();}}
}
const fieldBytes=rebaseFieldBytes;
const equalBytes=(a,b,label)=>assert(a.length===b.length&&a.every((value,index)=>value===b[index]),label);

export async function verifyFlowRebase(module,device,shaderRoot) {
    const cases=globalThis.flowRebaseProgress=[],hosts=[],before=module._pr_flow_host_live();
    const record=(name,evidence)=>cases.push({name,status:'PASS',evidence});
    const make=async({multi=false}={})=>{
        const host=await FlowHostWebGpu.create(module,device,shaderRoot,{maxBlocks:32,cellSize:.125});hosts.push(host);
        host.setScene({layers:multi?[layer(0),layer(2)]:[layer(0)],emitters:multi?[source(),source(2,2)]:[source()]});
        host.setSolidBoundaries([wall]);host.setScalarSources([scalar]);
        for(let i=0;i<3;i++)await host.step(1/64);
        return host;
    };
    try {
        assert(module._pr_flow_host_rebase_abi?.()===1,'Rebase ABI1 required');
        const host=await make(),period=host.rebasePeriod(),shift=period.map((value,index)=>value*[2,-3,1][index]);
        const original=await fieldBytes(host),integral=await scalarFieldIntegral(host),frames=host.stats.frames;
        const positions=new Float32Array([.1875,.1875,.1875,.5625,.3125,.3125,.875,0,0]);
        const velocities=await host.sampleVelocity(positions),receipt=structuredClone(host.scalarSourceReceipt);
        for(const invalid of [[period[0]/2,0,0],[NaN,0,0],[Infinity,0,0],[1e300,0,0]]) {
            assert(module._pr_flow_host_rebase(host.handle,...invalid,0)===0,'Raw invalid shift admitted');
            assert(module._pr_flow_host_rebase(host.handle,...invalid,1)===0,'Raw invalid shift mutated state');
        }
        const rejected=await fieldBytes(host);rejected.forEach((bytes,index)=>equalBytes(bytes,original[index],'Rejected rebase changed GPU state'));
        record('rebase-invalid-shift-is-atomic',{invalidCases:4,frames,period});
        const result=await host.rebase(shift),rebased=await fieldBytes(host),afterIntegral=await scalarFieldIntegral(host);
        equalBytes(original[0],rebased[0],'Rebase resampled density');equalBytes(original[1],rebased[1],'Rebase changed velocity atlas');
        equalBytes(new Uint8Array(new Float64Array(integral.integral).buffer),new Uint8Array(new Float64Array(afterIntegral.integral).buffer),'Rebase changed scalar integral');
        assert(host.stats.frames===frames&&result.advancedTime===0&&host.output.coordinateEpoch===1,'Rebase advanced the simulation clock');
        assert(JSON.stringify(host.scalarSourceReceipt)===JSON.stringify(receipt),'Rebase changed an accepted scalar receipt');
        const translated=new Float32Array(Array.from(positions,(value,index)=>value-shift[index%3]));
        const afterVelocities=await host.sampleVelocity(translated);afterVelocities.forEach((value,index)=>close(value,velocities[index],'Rebased velocity gather'));
        record('rebase-preserves-atlas-integrals-and-gather',{shift,period,atlasBytes:original.slice(0,2).map(v=>v.length),integral:integral.integral,result});
        await host.rebase(shift.map(value=>-value));const roundTrip=await fieldBytes(host);
        roundTrip.forEach((bytes,index)=>equalBytes(bytes,original[index],'Rebase inverse changed sparse/field bytes'));
        record('rebase-inverse-restores-exact-sparse-state',{coordinateEpoch:host.output.coordinateEpoch});
        const control=await make(),moved=await make();const nextShift=moved.rebasePeriod().map(value=>value*2);
        await moved.rebase(nextShift);
        for(let i=0;i<4;i++){await control.step(1/64);await moved.step(1/64);}
        const a=await scalarFieldIntegral(control),b=await scalarFieldIntegral(moved);
        a.integral.forEach((value,index)=>close(value,b.integral[index],'Continued rebased scalar field'));
        for(let index=0;index<4;index++)close(control.scalarSourceReceipt.sources[0].applied[index],moved.scalarSourceReceipt.sources[0].applied[index],'Rebased source receipt');
        const shifted=new Float32Array(Array.from(positions,(value,index)=>value-nextShift[index%3]));
        const va=await control.sampleVelocity(positions),vb=await moved.sampleVelocity(shifted);va.forEach((value,index)=>close(value,vb[index],'Continued rebased velocity',8e-5));
        record('rebase-preserves-source-and-summary-motion-history',{integrals:[a.integral,b.integral],velocityMaximumDifference:Math.max(...va.map((v,i)=>Math.abs(v-vb[i])))});
        const multi=await make({multi:true}),common=multi.rebasePeriod(),saved=await fieldBytes(multi);
        await multi.rebase(common.map(value=>-value));const updated=await fieldBytes(multi);
        equalBytes(saved[0],updated[0],'Multi-layer density changed');equalBytes(saved[1],updated[1],'Multi-layer velocity changed');
        assert(multi.output.layers.length===2,'Rebase lost a layer');
        await multi.step(1/64);record('rebase-supports-commensurate-native-layers',{period:common,layers:multi.output.layers.map(v=>v.id)});
        for(const value of hosts)await value.dispose();
        assert(module._pr_flow_host_live()===before,'Rebase leaked native contexts');
        assert(hosts.every(value=>value.resources.size===0&&value.stats.allocatedBytes===0),'Rebase leaked borrowed-device resources');
        return {cases,cleanup:{status:'PASS',nativeContextsBefore:before,nativeContextsAfter:module._pr_flow_host_live(),ownedGpuResources:0}};
    }finally{for(const host of hosts)await host.dispose();}
}
