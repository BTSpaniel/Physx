// SPDX-License-Identifier: MIT
// Resident-cell admission and exact native halo scatter for a staged paired
// exchange. This bridge does not infer physical mass from smoke/temperature.
static bool prMomentumReady(BrowserFlow* f){return f && f->context.completed && f->context.completed+1==f->context.frame && f->colliders.empty();}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_momentum_abi(){return 1;}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_momentum_prepare(unsigned handle,unsigned capacity){
    auto* f=checked(handle);if(!prMomentumReady(f) || capacity<1 || capacity>393216 || f->momentum.sequence==UINT32_MAX)return 0;
    NvFlowGridRenderData render={};f->gridIface->getRenderData(&f->context,f->grid,&render);
    if(!render.velocityTexture || !render.sparseBuffer || render.sparseParams.levelCount<2 || f->solids.shapes.empty())return 0;
    const auto level=render.sparseParams.levels[1];
    const uint64_t cells=uint64_t(level.numLocations)*level.threadsPerBlock;if(cells>UINT32_MAX)return 0;
    auto* c=&f->context;auto& s=f->momentum;NvFlowSparseTexture field={render.velocityTexture,render.sparseBuffer,render.sparseParams,1,render.velocityTexture->desc.format};
    if(field.format!=eNvFlowFormat_r32g32b32a32_float)return 0;
    solidPrepare(c,field,1);if(!f->solids.packed || !f->solids.control[1])return 0;
    PrMomentumParams params={level,{unsigned(cells),capacity,0,0}};unsigned zero[4]={};
    prscalar::upload(c,s.params,&params,sizeof(params),16,eNvFlowBufferUsage_constantBuffer);
    prscalar::upload(c,s.counter,zero,sizeof(zero),4,eNvFlowBufferUsage_rwStructuredBuffer|eNvFlowBufferUsage_bufferCopySrc);
    prscalar::buffer(c,s.contacts,capacity*sizeof(PrMomentumContact),sizeof(PrMomentumContact),eNvFlowBufferUsage_rwStructuredBuffer|eNvFlowBufferUsage_bufferCopySrc,eNvFlowMemoryType_device);
    prscalar::buffer(c,s.readback,16+capacity*sizeof(PrMomentumContact),16,eNvFlowBufferUsage_bufferCopyDst,eNvFlowMemoryType_readback);
    const char* names[]={"addons/momentum/PrMomentumGatherCS.wgsl","addons/momentum/PrMomentumApplyCS.wgsl"};
    for(unsigned i=0;i<2;i++)if(!s.pipeline[i])s.pipeline[i]=gpu(c,6,address(names[i]));
    prscalar::dispatch(c,s.pipeline[0],unsigned(cells),{0,1,s.params->id,1,2,render.sparseBuffer->id,2,4,render.velocityTexture->id,
        3,6,s.contacts->id,4,6,s.counter->id,30,2,f->solids.packed->id,31,1,f->solids.control[1]->id});
    gpu(c,8,s.counter->id,s.readback->id,0,0,16);gpu(c,8,s.contacts->id,s.readback->id,0,16,capacity*sizeof(PrMomentumContact));
    s.capacity=capacity;s.sequence++;s.frame=c->frame;s.solidVersion=f->solids.version;s.geometryRevision=f->geometryRevision;
    s.textureId=render.velocityTexture->id;s.prepared=true;return s.sequence;
}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_momentum_data(unsigned handle,unsigned sequence){
    auto* f=checked(handle);if(!f || !f->momentum.prepared || f->momentum.sequence!=sequence || !f->momentum.readback)return 0;
    return address(f->momentum.readback->mapped.data());
}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_momentum_commit(unsigned handle,unsigned sequence,unsigned pointer,unsigned count){
    auto* f=checked(handle);if(!prMomentumReady(f) || !validSpan(pointer,count,sizeof(PrMomentumUpdate)) || count>65536)return 0;
    auto& s=f->momentum;auto* c=&f->context;
    if(!s.prepared || sequence!=s.sequence || c->frame!=s.frame || f->solids.version!=s.solidVersion || f->geometryRevision!=s.geometryRevision)return 0;
    NvFlowGridRenderData render={};f->gridIface->getRenderData(c,f->grid,&render);
    if(!render.velocityTexture || render.velocityTexture->id!=s.textureId || render.sparseParams.levelCount<2)return 0;
    const auto level=render.sparseParams.levels[1];
    const auto* header=reinterpret_cast<const unsigned*>(s.readback->mapped.data());if(header[0]>s.capacity)return 0;
    const auto* contacts=reinterpret_cast<const PrMomentumContact*>(header+4);
    std::map<std::array<unsigned,4>,float> admitted;
    for(unsigned i=0;i<header[0];i++){
        const auto& record=contacts[i];std::array<unsigned,4> key={record.cell[0],record.cell[1],record.cell[2],record.cell[3]};
        if(key[0]>=level.numLocations || key[1]>level.blockDimLessOne.x || key[2]>level.blockDimLessOne.y || key[3]>level.blockDimLessOne.z)return 0;
        if(!std::isfinite(record.velocity[3]))return 0;admitted.emplace(key,record.velocity[3]);
    }
    if(admitted.size()!=count)return 0;
    const auto* updates=reinterpret_cast<const PrMomentumUpdate*>(pointer);
    for(unsigned i=0;i<count;i++){
        const auto& update=updates[i];std::array<unsigned,4> key={update.cell[0],update.cell[1],update.cell[2],update.cell[3]};
        auto found=admitted.find(key);if(found==admitted.end() || update.velocity[3]!=found->second)return 0;
        for(float part:update.velocity)if(!std::isfinite(part))return 0;admitted.erase(found);
    }
    // All address/ownership/value admission precedes upload and texture writes.
    if(!count){s.prepared=false;return 1;}
    PrMomentumParams params={level,{count,0,0,0}};
    prscalar::upload(c,s.params,&params,sizeof(params),16,eNvFlowBufferUsage_constantBuffer);
    prscalar::upload(c,s.updates,updates,count*sizeof(PrMomentumUpdate),sizeof(PrMomentumUpdate));
    prscalar::dispatch(c,s.pipeline[1],count,{0,1,s.params->id,1,2,render.sparseBuffer->id,2,2,s.updates->id,3,8,render.velocityTexture->id});
    s.prepared=false;return 1;
}
static void prMomentumDestroy(BrowserFlow* f){
    auto& s=f->momentum;auto* c=&f->context;
    for(auto** buffer:{&s.params,&s.contacts,&s.counter,&s.readback,&s.updates}){destroyBuffer(c,*buffer);*buffer=nullptr;}
    for(auto& pipeline:s.pipeline){if(pipeline)gpu(c,2,pipeline);pipeline=0;}s.prepared=false;
}
