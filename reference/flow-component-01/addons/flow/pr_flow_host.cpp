// SPDX-License-Identifier: MIT
// Browser context for NVIDIA Flow's unmodified host graph and simulation ops.
#include <emscripten.h>
#include <emscripten/heap.h>
#include "NvFlowExt.h"
#include <vector>
#include <set>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <limits>
#include <utility>
#include <map>
#include <memory>
#include <string>
#include "solid_geometry.h"

EM_JS(unsigned, flow_gpu, (unsigned context, unsigned op, unsigned a, unsigned b, unsigned c, unsigned d, unsigned e, unsigned f), {
    return Module.prFlowContexts.get(context).call(op, a, b, c, d, e, f) || 0;
});

struct NvFlowBuffer { unsigned id, refs = 1; NvFlowMemoryType memory; NvFlowBufferDesc desc; std::vector<unsigned char> mapped; };
#include "scalar_sources_types.h"
#include "momentum_exchange_types.h"
struct NvFlowBufferTransient : NvFlowBuffer {};
struct NvFlowBufferAcquire { NvFlowBufferTransient* buffer; };
struct NvFlowTexture { unsigned id, refs = 1; NvFlowTextureDesc desc; };
struct NvFlowTextureTransient : NvFlowTexture {};
struct NvFlowTextureAcquire { NvFlowTextureTransient* texture; };
struct NvFlowSampler { unsigned id; };
struct NvFlowComputePipeline { unsigned id,solidId=0,pressureFaces=0; };
struct PrSolidState {
    std::vector<prflow::SolidRecord> shapes;
    std::vector<prflow::V4> planes;
    std::vector<prflow::Node> nodes;
    std::vector<std::array<unsigned,4>> layers;
    NvFlowBuffer* packed=nullptr;NvFlowBuffer* control[2]={nullptr,nullptr};
    NvFlowBuffer* pressureFaces=nullptr;
    unsigned packedBytes=0,version=0;NvFlowUint64 uploadedFrame=0;
};
struct NvFlowContext {
    unsigned id; NvFlowUint64 frame = 1, completed = 0;
    std::set<NvFlowBuffer*> buffers; std::set<NvFlowTexture*> textures;
    std::vector<NvFlowBuffer*> transientBuffers; std::vector<NvFlowTexture*> transientTextures;
    NvFlowSampler* defaultSampler = nullptr;
    PrSolidState* solids=nullptr;unsigned solidMode=0;
    PrScalarState* scalarSources=nullptr;
};
static void prScalarApply(NvFlowContext*,NvFlowSparseTexture&,float);
static bool solidShader(const std::string& name);
static unsigned gpu(NvFlowContext* c, unsigned op, unsigned a=0, unsigned b=0, unsigned d=0, unsigned e=0, unsigned f=0, unsigned g=0) { return flow_gpu(c->id,op,a,b,d,e,f,g); }
static unsigned address(const void* p) { return reinterpret_cast<uintptr_t>(p); }
static NvFlowBuffer* createBuffer(NvFlowContext* c, NvFlowMemoryType memory, const NvFlowBufferDesc* desc) {
    auto* b = new NvFlowBufferTransient(); b->memory=memory; b->desc=*desc;
    if (memory != eNvFlowMemoryType_device) b->mapped.resize(desc->sizeInBytes);
    b->id=gpu(c,1,desc->sizeInBytes,desc->usageFlags,memory,address(b->mapped.data())); c->buffers.insert(b); return b;
}
static void destroyBuffer(NvFlowContext* c,NvFlowBuffer* b) { if (b && !--b->refs) { gpu(c,2,b->id); c->buffers.erase(b); delete static_cast<NvFlowBufferTransient*>(b); } }
static NvFlowBufferTransient* getBufferTransient(NvFlowContext* c,const NvFlowBufferDesc* desc) { auto* b=createBuffer(c,eNvFlowMemoryType_device,desc); c->transientBuffers.push_back(b); return static_cast<NvFlowBufferTransient*>(b); }
static NvFlowBufferTransient* registerBuffer(NvFlowContext* c,NvFlowBuffer* b) { if (!b) return nullptr; b->refs++; c->transientBuffers.push_back(b); return static_cast<NvFlowBufferTransient*>(b); }
static NvFlowTexture* createTexture(NvFlowContext* c,const NvFlowTextureDesc* desc) { auto* t=new NvFlowTextureTransient(); t->desc=*desc; t->id=gpu(c,4,address(desc)); c->textures.insert(t); return t; }
static void destroyTexture(NvFlowContext* c,NvFlowTexture* t) { if(t && !--t->refs) { gpu(c,2,t->id); c->textures.erase(t); delete static_cast<NvFlowTextureTransient*>(t); } }
static NvFlowTextureTransient* getTextureTransient(NvFlowContext* c,const NvFlowTextureDesc* desc) { auto* t=createTexture(c,desc); c->transientTextures.push_back(t); return static_cast<NvFlowTextureTransient*>(t); }
static NvFlowTextureTransient* registerTexture(NvFlowContext* c,NvFlowTexture* t) { if (!t) return nullptr; t->refs++; c->transientTextures.push_back(t); return static_cast<NvFlowTextureTransient*>(t); }
static NvFlowSampler* createSampler(NvFlowContext* c,const NvFlowSamplerDesc* desc) { return new NvFlowSampler{gpu(c,5,address(desc))}; }
static void destroySampler(NvFlowContext* c,NvFlowSampler* s) { if(s) {gpu(c,2,s->id);delete s;} }
static void logPrint(NvFlowLogLevel level,const char* format,...) { char text[2048]; va_list args;va_start(args,format);vsnprintf(text,sizeof(text),format,args);va_end(args);printf("[Flow:%d] %s\n",int(level),text); }
static NvFlowContextInterface makeInterface() {
    NvFlowContextInterface i={NV_FLOW_REFLECT_INTERFACE_INIT(NvFlowContextInterface)};
    i.getContextConfig=[](NvFlowContext*,NvFlowContextConfig* cfg){cfg->api=eNvFlowContextApi_abstract;cfg->textureBinding=eNvFlowTextureBindingType_separateSampler;};
    i.isFeatureSupported=[](NvFlowContext*,NvFlowContextFeature)->NvFlowBool32{return NV_FLOW_FALSE;};
    i.getCurrentFrame=i.getCurrentGlobalFrame=[](NvFlowContext* c){return c->frame;};
    i.getLastFrameCompleted=i.getLastGlobalFrameCompleted=[](NvFlowContext* c){return c->completed;};
    i.getLogPrint=[](NvFlowContext*)->NvFlowLogPrint_t{return logPrint;};
    i.executeTasks=[](NvFlowContext*,NvFlowUint count,NvFlowUint,NvFlowContextThreadPoolTask_t task,void* user){ for(unsigned n=0;n<count;n++)task(n,0,nullptr,user); };
    i.createBuffer=createBuffer;i.destroyBuffer=destroyBuffer;i.getBufferTransient=getBufferTransient;i.registerBufferAsTransient=registerBuffer;
    i.aliasBufferTransient=[](NvFlowContext*,NvFlowBufferTransient* b,NvFlowFormat,NvFlowUint){return b;};
    i.enqueueAcquireBuffer=[](NvFlowContext*,NvFlowBufferTransient* b){b->refs++;return new NvFlowBufferAcquire{b};};
    i.getAcquiredBuffer=[](NvFlowContext*,NvFlowBufferAcquire* a,NvFlowBuffer** out)->NvFlowBool32{*out=a->buffer;delete a;return NV_FLOW_TRUE;};
    i.mapBuffer=[](NvFlowContext*,NvFlowBuffer* b)->void*{return b->mapped.data();};
    i.unmapBuffer=[](NvFlowContext* c,NvFlowBuffer* b){if(b->memory==eNvFlowMemoryType_upload)gpu(c,3,b->id,address(b->mapped.data()),b->desc.sizeInBytes);};
    i.getBufferTransientById=[](NvFlowContext* c,NvFlowUint64 id)->NvFlowBufferTransient*{for(auto* b:c->buffers)if(b->id==id)return registerBuffer(c,b);return nullptr;};
    i.getBufferExternalHandle=[](NvFlowContext*,NvFlowBuffer*,NvFlowInteropHandle* out){*out=NvFlowInteropHandle_default;};
    i.closeBufferExternalHandle=[](NvFlowContext* c,NvFlowBuffer*,const NvFlowInteropHandle*){gpu(c,99);};
    i.createBufferFromExternalHandle=[](NvFlowContext* c,const NvFlowBufferDesc*,const NvFlowInteropHandle*)->NvFlowBuffer*{gpu(c,99);return nullptr;};
    i.createTexture=createTexture;i.destroyTexture=destroyTexture;i.getTextureTransient=getTextureTransient;i.registerTextureAsTransient=registerTexture;
    i.aliasTextureTransient=[](NvFlowContext* c,NvFlowTextureTransient* t,NvFlowFormat f){if(t->desc.format!=f)gpu(c,99);return t;};
    i.enqueueAcquireTexture=[](NvFlowContext*,NvFlowTextureTransient* t){t->refs++;return new NvFlowTextureAcquire{t};};
    i.getAcquiredTexture=[](NvFlowContext*,NvFlowTextureAcquire* a,NvFlowTexture** out)->NvFlowBool32{*out=a->texture;delete a;return NV_FLOW_TRUE;};
    i.getTextureTransientById=[](NvFlowContext* c,NvFlowUint64 id)->NvFlowTextureTransient*{for(auto* t:c->textures)if(t->id==id)return registerTexture(c,t);return nullptr;};
    i.createSampler=createSampler;i.destroySampler=destroySampler;
    i.getDefaultSampler=[](NvFlowContext* c){if(!c->defaultSampler){NvFlowSamplerDesc desc={eNvFlowSamplerAddressMode_clamp,eNvFlowSamplerAddressMode_clamp,eNvFlowSamplerAddressMode_clamp,eNvFlowSamplerFilterMode_linear};c->defaultSampler=createSampler(c,&desc);}return c->defaultSampler;};
    i.createComputePipeline=[](NvFlowContext* c,const NvFlowComputePipelineDesc* desc){
        auto* p=new NvFlowComputePipeline{gpu(c,6,address(desc->bytecode.data))};
        const std::string token=static_cast<const char*>(desc->bytecode.data);const auto start=token.find_last_of('/');
        const auto name=token.substr(start==std::string::npos?0:start+1,token.size()-(start==std::string::npos?0:start+1)-5);
        if(solidShader(name)){const std::string variant="addons/solid/Solid"+name+".wgsl";p->solidId=gpu(c,6,address(variant.c_str()));}
        p->pressureFaces=name=="PressureDivergenceCS"?1u:name=="PressureJacobiCS"?2u:0u;
        return p;};
    i.destroyComputePipeline=[](NvFlowContext* c,NvFlowComputePipeline* p){if(p){gpu(c,2,p->id);if(p->solidId)gpu(c,2,p->solidId);delete p;}};
    i.addPassCompute=[](NvFlowContext* c,const NvFlowPassComputeParams* p){
        std::vector<unsigned> bindings;for(unsigned n=0;n<p->numDescriptorWrites;n++){
            const auto& r=p->resources[n];bindings.push_back(p->descriptorWrites[n].write.vulkan.binding);bindings.push_back(p->descriptorWrites[n].type);
            bindings.push_back(r.bufferTransient?r.bufferTransient->id:r.textureTransient?r.textureTransient->id:r.sampler?r.sampler->id:0);
        }
        const bool boundary=p->pipeline->solidId && c->solids && !c->solids->nodes.empty();
        if(boundary){auto& s=*c->solids;
            bindings.insert(bindings.end(),{30,unsigned(eNvFlowDescriptorType_structuredBuffer),s.packed->id,
                31,unsigned(eNvFlowDescriptorType_constantBuffer),s.control[c->solidMode]->id});
            if(p->pipeline->pressureFaces)bindings.insert(bindings.end(),{32,
                unsigned(p->pipeline->pressureFaces==1u?eNvFlowDescriptorType_rwStructuredBuffer:eNvFlowDescriptorType_structuredBuffer),s.pressureFaces->id});}
        gpu(c,7,boundary?p->pipeline->solidId:p->pipeline->id,address(bindings.data()),unsigned(bindings.size()/3),p->gridDim.x,p->gridDim.y,p->gridDim.z);
    };
    i.addPassCopyBuffer=[](NvFlowContext* c,const NvFlowPassCopyBufferParams* p){gpu(c,8,p->src->id,p->dst->id,p->srcOffset,p->dstOffset,p->numBytes);};
    i.addPassCopyBufferToTexture=[](NvFlowContext* c,const NvFlowPassCopyBufferToTextureParams* p){unsigned a[]={unsigned(p->bufferOffset),p->bufferRowPitch,p->bufferDepthPitch,p->textureMipLevel,p->textureOffset.x,p->textureOffset.y,p->textureOffset.z,p->textureExtent.x,p->textureExtent.y,p->textureExtent.z};gpu(c,9,p->src->id,p->dst->id,address(a));};
    i.addPassCopyTextureToBuffer=[](NvFlowContext* c,const NvFlowPassCopyTextureToBufferParams* p){unsigned a[]={unsigned(p->bufferOffset),p->bufferRowPitch,p->bufferDepthPitch,p->textureMipLevel,p->textureOffset.x,p->textureOffset.y,p->textureOffset.z,p->textureExtent.x,p->textureExtent.y,p->textureExtent.z};gpu(c,10,p->src->id,p->dst->id,address(a));};
    i.addPassCopyTexture=[](NvFlowContext* c,const NvFlowPassCopyTextureParams* p){unsigned a[]={p->srcMipLevel,p->srcOffset.x,p->srcOffset.y,p->srcOffset.z,p->dstMipLevel,p->dstOffset.x,p->dstOffset.y,p->dstOffset.z,p->extent.x,p->extent.y,p->extent.z};gpu(c,11,p->src->id,p->dst->id,address(a));};
    return i;
}
#include "solid_operators.h"
#include "scalar_sources_impl.h"

// Array storage belongs to the scene, never to the caller's temporary ABI packet.
struct ScenePointStorage {
    NvFlowEmitterPointParams params=NvFlowEmitterPointParams_default;
    std::vector<NvFlowFloat3> positions,velocities;
};
struct SceneMeshStorage {
    NvFlowEmitterMeshParams params=NvFlowEmitterMeshParams_default;
    std::vector<NvFlowFloat3> positions,velocities;
    std::vector<int> indices,faceCounts;
};
// Additive collision ABI 1. These are velocity obstacles, never scalar emitters
// or two-way force feedback. Each ID owns native transform history independently.
struct SceneCollider {
    unsigned id,layer,type,flags;
    float position[3],radiusOrHalfX,halfY,halfZ,quaternion[4],rateVelocity;
    unsigned reserved[5];
};
static_assert(sizeof(SceneCollider)==80,"Flow collision ABI layout");
struct ColliderState {
    SceneCollider params={};
    NvFlowEmitterSphere sphere={};
    NvFlowEmitterBox box={};
    bool initialized=false,resetPending=true;
};
struct BrowserFlow {
    NvFlowContext context; NvFlowContextInterface iface=makeInterface(); NvFlowGridInterface* gridIface=NvFlowGetGridInterfaceNoOpt(); NvFlowGrid* grid=nullptr;
    NvFlowGridSimulateLayerParams layer=NvFlowGridSimulateLayerParams_default;
    NvFlowEmitterSphereParams emitter=NvFlowEmitterSphereParams_default;
    std::vector<NvFlowGridSimulateLayerParams> layers;
    std::vector<NvFlowEmitterSphereParams> spheres;
    std::vector<NvFlowEmitterBoxParams> boxes;
    std::vector<ScenePointStorage> points;
    std::vector<SceneMeshStorage> meshes;
    std::map<unsigned,std::unique_ptr<ColliderState>> colliders;
    NvFlowUint64 geometryRevision=0;
    bool customScene=false;
    PrSolidState solids;
    PrScalarState scalarSources;
    PrMomentumState momentum;
    // New coupling APIs select one exact requested timestep for this lifetime.
    // Clearing shapes/sources never revives the legacy fixed-step accumulator.
    bool exactStep=false;
    double time=0;
};
static void applyColliders(BrowserFlow*,const NvFlowGridRenderData&,float);
static void destroyCollider(BrowserFlow*,ColliderState&);
static void prMomentumDestroy(BrowserFlow*);
static std::set<BrowserFlow*> flows;
static BrowserFlow* checked(unsigned h){auto* f=reinterpret_cast<BrowserFlow*>(h);return flows.count(f)?f:nullptr;}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_create(unsigned gpuId,unsigned maxBlocks,float cellSize){
    if(!gpuId||maxBlocks<16||maxBlocks>4096||!std::isfinite(cellSize)||cellSize<=0)return 0;
    auto* f=new BrowserFlow();f->context.id=gpuId;f->context.solids=&f->solids;f->context.scalarSources=&f->scalarSources;f->layer.luid=1;f->layer.densityCellSize=cellSize;
    f->layer.enableHighPrecisionVelocity=NV_FLOW_TRUE;f->layer.enableHighPrecisionDensity=NV_FLOW_TRUE;
    f->layer.advection.gravity={0.f,-9.81f,0.f};f->emitter.luid=2;f->emitter.radius=cellSize*3.f;f->emitter.velocity={0.f,2.f,0.f};
    f->emitter.temperature=1.f;f->emitter.fuel=.8f;f->emitter.smoke=.5f;f->emitter.coupleRateSmoke=2.f;
    NvFlowGridDesc desc={maxBlocks,maxBlocks};f->grid=f->gridIface->createGrid(&f->iface,&f->context,solidOpList(),solidExtOpList(),&desc);
    if(!f->grid){delete f;return 0;}flows.insert(f);return address(f);
}
static void publishFlowOutput(BrowserFlow* f,const NvFlowGridRenderData& render){
    NvFlowSparseTexture boundaryField={};boundaryField.sparseParams=render.sparseParams;solidPrepare(&f->context,boundaryField,0);
    // Grid.cpp stores velocity at sparse level 1 (density is level 0).
    if(render.densityTexture)gpu(&f->context,12,render.densityTexture->id,render.velocityTexture?render.velocityTexture->id:0,f->gridIface->getActiveBlockCount(f->grid),render.sparseBuffer->id,address(&render.sparseParams.levels[1]),render.sparseParams.layerCount?address(&render.sparseParams.layers[0]):0);
    struct LayerOutput{unsigned id;float blockSizeWorld[3];unsigned layerAndLevel;unsigned reserved[3];};
    std::vector<LayerOutput> outputs;
    for(unsigned i=0;i<render.sparseParams.layerCount;i++){
        const auto& layer=render.sparseParams.layers[i];
        const auto location=NvFlow_unpackLayerAndLevel(layer.layerAndLevel);
        if(location.y==0)outputs.push_back({unsigned(location.x),{layer.blockSizeWorld.x,layer.blockSizeWorld.y,layer.blockSizeWorld.z},unsigned(layer.layerAndLevel),{0,0,0}});
    }
    gpu(&f->context,13,address(outputs.data()),outputs.size());
    gpu(&f->context,14,f->solids.shapes.empty()?0:f->solids.packed->id,f->solids.shapes.empty()?0:f->solids.packedBytes,f->solids.version);
}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_step(unsigned handle,float dt){
    auto* f=checked(handle);if(!f||!std::isfinite(dt)||dt<=0||dt>.1f)return 0;
    f->time+=dt;
    if(f->exactStep){f->layer.enableVariableTimeStep=NV_FLOW_TRUE;for(auto& layer:f->layers)layer.enableVariableTimeStep=NV_FLOW_TRUE;}
    solidMotion(f->solids,dt);
    std::vector<NvFlowUint8*> layers,spheres,boxes,points,meshes;
    if(f->customScene){
        for(auto& value:f->layers)layers.push_back(reinterpret_cast<NvFlowUint8*>(&value));
        for(auto& value:f->spheres)spheres.push_back(reinterpret_cast<NvFlowUint8*>(&value));
        for(auto& value:f->boxes)boxes.push_back(reinterpret_cast<NvFlowUint8*>(&value));
        for(auto& value:f->points)points.push_back(reinterpret_cast<NvFlowUint8*>(&value.params));
        for(auto& value:f->meshes)meshes.push_back(reinterpret_cast<NvFlowUint8*>(&value.params));
    }else{layers.push_back(reinterpret_cast<NvFlowUint8*>(&f->layer));spheres.push_back(reinterpret_cast<NvFlowUint8*>(&f->emitter));}
    NvFlowDatabaseTypeSnapshot types[]={
        {f->context.frame,&NvFlowGridSimulateLayerParams_NvFlowReflectDataType,layers.data(),layers.size()},
        {f->context.frame,&NvFlowGridEmitterSphereParams_NvFlowReflectDataType,spheres.data(),spheres.size()},
        {f->context.frame,&NvFlowGridEmitterBoxParams_NvFlowReflectDataType,boxes.data(),boxes.size()},
        {f->context.frame,&NvFlowGridEmitterPointParams_NvFlowReflectDataType,points.data(),points.size()},
        {f->context.frame,&NvFlowGridEmitterMeshParams_NvFlowReflectDataType,meshes.data(),meshes.size()}};
    NvFlowGridParamsDescSnapshot snapshot={};snapshot.snapshot={f->context.frame,types,5};snapshot.absoluteSimTime=f->time;snapshot.deltaTime=dt;
    NvFlowGridParamsDesc params={&snapshot,1};f->gridIface->simulate(&f->context,f->grid,&params,NV_FLOW_FALSE);
    NvFlowGridRenderData render={};f->gridIface->getRenderData(&f->context,f->grid,&render);
    if(f->scalarSources.appliedFrame!=f->context.frame){
        NvFlowSparseTexture emptyField={};emptyField.sparseParams=render.sparseParams;emptyField.sparseBuffer=render.sparseBuffer;
        emptyField.textureTransient=render.densityTexture;emptyField.format=eNvFlowFormat_r32g32b32a32_float;
        prScalarApply(&f->context,emptyField,dt);
    }
    prScalarPublish(&f->context,dt);
    applyColliders(f,render,dt);
    publishFlowOutput(f,render);
    solidCommitMotion(f->solids);
    return f->context.frame;
}
extern "C" EMSCRIPTEN_KEEPALIVE void pr_flow_host_complete(unsigned handle){auto* f=checked(handle);if(!f)return;auto* c=&f->context;c->completed=c->frame++;for(auto* b:c->transientBuffers)destroyBuffer(c,b);for(auto* t:c->transientTextures)destroyTexture(c,t);c->transientBuffers.clear();c->transientTextures.clear();}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_emitter(unsigned handle,float x,float y,float z,float radius,float temperature,float fuel,float smoke,unsigned enabled){auto* f=checked(handle);if(!f||f->customScene||!std::isfinite(x+y+z+radius+temperature+fuel+smoke)||radius<=0)return 0;f->emitter.position={x,y,z};f->emitter.radius=radius;f->emitter.temperature=temperature;f->emitter.fuel=fuel;f->emitter.smoke=smoke;f->emitter.enabled=enabled?NV_FLOW_TRUE:NV_FLOW_FALSE;return 1;}
extern "C" EMSCRIPTEN_KEEPALIVE void pr_flow_host_destroy(unsigned handle){auto* f=checked(handle);if(!f)return;flows.erase(f);auto* c=&f->context;for(auto& item:f->colliders)destroyCollider(f,*item.second);f->colliders.clear();f->gridIface->destroyGrid(c,f->grid);prScalarDestroy(c);prMomentumDestroy(f);for(auto* b:c->transientBuffers)destroyBuffer(c,b);for(auto* t:c->transientTextures)destroyTexture(c,t);destroySampler(c,c->defaultSampler);while(!c->buffers.empty()){auto* b=*c->buffers.begin();b->refs=1;destroyBuffer(c,b);}while(!c->textures.empty()){auto* t=*c->textures.begin();t->refs=1;destroyTexture(c,t);}delete f;}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_abi(){return 1;}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_live(){return flows.size();}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_airflow(unsigned handle,float x,float y,float z){auto* f=checked(handle);if(!f||f->customScene||!std::isfinite(x)||!std::isfinite(y)||!std::isfinite(z))return 0;f->emitter.velocity={x,y,z};return 1;}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_controls(unsigned handle,unsigned pressure,unsigned combustion,float vorticity){auto* f=checked(handle);if(!f||f->customScene||!std::isfinite(vorticity)||vorticity<0||vorticity>10)return 0;f->layer.pressure.enabled=pressure?NV_FLOW_TRUE:NV_FLOW_FALSE;f->layer.advection.combustionEnabled=combustion?NV_FLOW_TRUE:NV_FLOW_FALSE;f->layer.vorticity.enabled=vorticity>0?NV_FLOW_TRUE:NV_FLOW_FALSE;f->layer.vorticity.forceScale=vorticity;return 1;}

// Additive scene ABI; existing host ABI 1 entry points retain their behavior.
struct SceneLayer { unsigned id;float cellSize,gravity[3];unsigned pressure,combustion;float vorticity; };
struct SceneEmitter {
    unsigned id,layer,type,flags;
    float position[3],radiusOrHalfX,halfY,halfZ,quaternion[4],velocity[3];
    float temperature,fuel,smoke,rateVelocity,rateTemperature,rateFuel,rateSmoke;
    float allocationScale,divergence,rateDivergence,burn,rateBurn;
    unsigned reserved[3];
};
// Geometry ABI 1 supplements scene ABI 1; the original setter remains sphere/box only.
// widths/widthScale are reserved: upstream point widths select LOD, not splat radius.
struct SceneGeometry {
    unsigned id,positions,positionCount,widths,widthCount,indices,indexCount,flags;
    float widthScale,minDistance,maxDistance;
    unsigned velocities,velocityCount,reserved[3];
};
static_assert(sizeof(SceneLayer)==32 && sizeof(SceneEmitter)==128 && sizeof(SceneGeometry)==64,"Flow scene ABI layout");
static bool validSpan(unsigned pointer,unsigned count,unsigned stride){
    const auto heap=emscripten_get_heap_size();
    return !count || (pointer && !(pointer&3u) && pointer<=heap && count<=(heap-pointer)/stride);
}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_solid_abi(){return 1;}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_scalar_abi(){return 1;}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_scalar_sources(unsigned handle,unsigned records,unsigned count){
    auto* f=checked(handle);if(!f || count>4096 || !validSpan(records,count,sizeof(PrScalarRecord)))return 0;
    const auto* input=reinterpret_cast<const PrScalarRecord*>(records);std::set<unsigned> layers;
    if(f->customScene)for(const auto& layer:f->layers)layers.insert(unsigned(layer.layer));else layers.insert(0);
    for(unsigned i=0;i<count;i++)if(!layers.count(input[i].layer))return 0;
    if(!prScalarSet(&f->context,input,count))return 0;f->exactStep=true;return 1;
}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_scalar_receipt(unsigned handle){auto* f=checked(handle);return f?address(prScalarReceiptData(&f->context)):0;}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_scalar_count(unsigned handle){auto* f=checked(handle);return f?f->scalarSources.publishedCount:0;}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_scalar_frame(unsigned handle){auto* f=checked(handle);return f?unsigned(f->scalarSources.publishedFrame):0;}
extern "C" EMSCRIPTEN_KEEPALIVE float pr_flow_host_scalar_dt(unsigned handle){auto* f=checked(handle);return f?f->scalarSources.publishedDt:0.f;}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_solids(unsigned handle,unsigned records,unsigned count,unsigned planeData,unsigned planeCount){
    auto* f=checked(handle);
    if(!f || count>65536 || planeCount>1048576 || !validSpan(records,count,sizeof(prflow::SolidRecord))
        || !validSpan(planeData,planeCount,sizeof(prflow::V4)) || (!f->colliders.empty() && count))return 0;
    std::vector<prflow::SolidRecord> shapes(count);std::vector<prflow::V4> planes(planeCount);
    if(count)std::memcpy(shapes.data(),reinterpret_cast<const void*>(records),count*sizeof(prflow::SolidRecord));
    if(planeCount)std::memcpy(planes.data(),reinterpret_cast<const void*>(planeData),planeCount*sizeof(prflow::V4));
    std::set<unsigned> ids,layers;std::map<unsigned,const prflow::SolidRecord*> previous;
    if(f->customScene)for(const auto& layer:f->layers)layers.insert(unsigned(layer.layer));else layers.insert(0);
    for(const auto& s:f->solids.shapes)previous[s.id]=&s;
    for(auto& s:shapes){
        if(!s.id || !layers.count(s.layer) || !prflow::valid(s,planes) || !ids.insert(s.id).second)return 0;
        auto found=previous.find(s.id);
        if(found!=previous.end() && !(s.flags&2u) && found->second->type==s.type && found->second->layer==s.layer){
            s.previousPosition=found->second->positionRadius;s.previousRotation=found->second->rotation;
        }else{s.previousPosition=s.positionRadius;s.previousRotation=s.rotation;s.flags|=2u;}
        s.linearVelocity={0,0,0,0};s.angularVelocity={0,0,0,0};
    }
    auto nodes=prflow::buildBvh(shapes);
    f->solids.shapes=std::move(shapes);f->solids.planes=std::move(planes);f->solids.nodes=std::move(nodes);
    f->solids.uploadedFrame=0;f->solids.version++;f->exactStep=true;return 1;
}
template<class T> static void configureEmitter(T& out,const SceneEmitter& in){
    out.luid=(NvFlowUint64(2+in.type)<<32)|in.id;
    out.layer=int(in.layer);out.enabled=(in.flags&1)?NV_FLOW_TRUE:NV_FLOW_FALSE;
    out.applyPostPressure=(in.flags&2)?NV_FLOW_TRUE:NV_FLOW_FALSE;
    // Row-vector transform: rotate a local box around its center, then translate.
    const float x=in.quaternion[0],y=in.quaternion[1],z=in.quaternion[2],w=in.quaternion[3];
    out.localToWorld={{1-2*(y*y+z*z),2*(x*y+z*w),2*(x*z-y*w),0},
        {2*(x*y-z*w),1-2*(x*x+z*z),2*(y*z+x*w),0},
        {2*(x*z+y*w),2*(y*z-x*w),1-2*(x*x+y*y),0},
        {in.position[0],in.position[1],in.position[2],1}};
    out.velocityIsWorldSpace=NV_FLOW_TRUE;
    out.velocity={in.velocity[0],in.velocity[1],in.velocity[2]};
    out.temperature=in.temperature;out.fuel=in.fuel;out.smoke=in.smoke;out.burn=in.burn;
    out.divergence=in.divergence;
    out.coupleRateVelocity=in.rateVelocity;out.coupleRateTemperature=in.rateTemperature;
    out.coupleRateFuel=in.rateFuel;out.coupleRateSmoke=in.rateSmoke;
    out.coupleRateDivergence=in.rateDivergence;out.coupleRateBurn=in.rateBurn;
}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_scene_abi(){return 1;}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_geometry_abi(){return 1;}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_collision_abi(){return 1;}

static void destroyCollider(BrowserFlow* f,ColliderState& state){
    if(!state.initialized)return;
    if(state.params.type==0){
        NvFlowEmitterSpherePinsIn in={};NvFlowEmitterSpherePinsOut out={};
        in.contextInterface=&f->iface;in.context=&f->context;
        NvFlowEmitterSphere_destroy(&state.sphere,&in,&out);state.sphere={};
    }else{
        NvFlowEmitterBoxPinsIn in={};NvFlowEmitterBoxPinsOut out={};
        in.contextInterface=&f->iface;in.context=&f->context;
        NvFlowEmitterBox_destroy(&state.box,&in,&out);state.box={};
    }
    state.initialized=false;
}

extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_colliders(unsigned handle,unsigned pointer,unsigned count){
    auto* f=checked(handle);
    if(!f||count>1024||!validSpan(pointer,count,sizeof(SceneCollider))||(!f->solids.shapes.empty()&&count))return 0;
    const auto* input=reinterpret_cast<const SceneCollider*>(pointer);
    std::set<unsigned> ids,layers;
    if(f->customScene)for(const auto& layer:f->layers)layers.insert(unsigned(layer.layer));
    else layers.insert(0);
    // Validate every row before mutating any descriptor or native operator.
    for(unsigned i=0;i<count;i++){
        const auto& row=input[i];
        if(!row.id||!ids.insert(row.id).second||!layers.count(row.layer)||row.type>1||row.flags>7)return 0;
        for(unsigned value:row.reserved)if(value)return 0;
        float values[11];std::memcpy(values,row.position,sizeof(values));
        for(float value:values)if(!std::isfinite(value))return 0;
        if(!std::isfinite(row.rateVelocity)||row.rateVelocity<0||row.radiusOrHalfX<=0
            ||(row.type==0&&(row.halfY!=0||row.halfZ!=0))
            ||(row.type==1&&(row.halfY<=0||row.halfZ<=0)))return 0;
        const float* q=row.quaternion;const double norm=double(q[0])*q[0]+double(q[1])*q[1]+double(q[2])*q[2]+double(q[3])*q[3];
        if(std::abs(norm-1.)>1e-4||!std::isfinite(row.radiusOrHalfX*row.radiusOrHalfX)
            ||!std::isfinite(row.halfY*row.halfY)||!std::isfinite(row.halfZ*row.halfZ))return 0;
        const double extent=double(row.radiusOrHalfX)+row.halfY+row.halfZ;
        float cellSize=f->layer.densityCellSize;
        if(f->customScene)for(const auto& layer:f->layers)if(unsigned(layer.layer)==row.layer)cellSize=layer.densityCellSize;
        for(float axis:row.position)if(!std::isfinite(float(std::abs(double(axis))+extent))
            ||(std::abs(double(axis))+extent)/double(cellSize)>double(1u<<28))return 0;
    }
    // Stage new allocations first. Existing operators survive failed validation
    // and caller array reordering. Shape/layer replacements explicitly re-prime.
    std::map<unsigned,std::unique_ptr<ColliderState>> added;
    for(unsigned i=0;i<count;i++)if(!f->colliders.count(input[i].id))added.emplace(input[i].id,std::make_unique<ColliderState>());
    for(auto it=f->colliders.begin();it!=f->colliders.end();){
        if(!ids.count(it->first)){destroyCollider(f,*it->second);it=f->colliders.erase(it);}else ++it;
    }
    f->colliders.merge(added);
    for(unsigned i=0;i<count;i++){
        const auto& row=input[i];auto& state=*f->colliders.at(row.id);
        if(state.params.type!=row.type||state.params.layer!=row.layer||(row.flags&2)){
            destroyCollider(f,state);state.resetPending=true;
        }
        state.params=row;
    }
    return 1;
}

template<class T> static void configureCollider(T& out,const ColliderState& state){
    const auto& row=state.params;SceneEmitter emitter={};
    emitter.id=row.id;emitter.layer=row.layer;emitter.type=row.type;emitter.flags=(row.flags&1)|2;
    std::memcpy(emitter.position,row.position,sizeof(row.position));
    std::memcpy(emitter.quaternion,row.quaternion,sizeof(row.quaternion));
    emitter.rateVelocity=row.rateVelocity;configureEmitter(out,emitter);
    out.position={0,0,0};out.allocationScale=0.f;
    out.physicsVelocityScale=state.resetPending?0.f:1.f;out.multisample=(row.flags&4)?NV_FLOW_TRUE:NV_FLOW_FALSE;
}

static void applyColliders(BrowserFlow* f,const NvFlowGridRenderData& render,float dt){
    if(f->colliders.empty()||!render.velocityTexture||!render.sparseBuffer)return;
    NvFlowSparseTexture velocity={render.velocityTexture,render.sparseBuffer,render.sparseParams,1,render.velocityTexture->desc.format};
    // Operators execute sequentially and finish on the original atlas. Their
    // two half-passes may share one temporary atlas regardless of collider count.
    NvFlowSparseTexture scratch={};NvFlowSparseTexture_duplicate(&f->iface,&f->context,&scratch,&velocity);
    // Sphere's unmodified shader binds a readonly tracing velocity even when
    // numTraceSamples=0. WebGPU disallows binding the destination for reading
    // in its second half-pass, so supply one immutable pre-collision snapshot.
    NvFlowSparseTexture tracingVelocity=velocity;
    for(const auto& item:f->colliders)if(item.second->params.type==0){
        NvFlowSparseTexture_duplicate(&f->iface,&f->context,&tracingVelocity,&velocity);
        NvFlowPassCopyTextureParams copy={};copy.src=velocity.textureTransient;copy.dst=tracingVelocity.textureTransient;
        const auto& desc=render.velocityTexture->desc;copy.extent={desc.width,desc.height,desc.depth};
        f->iface.addPassCopyTexture(&f->context,&copy);break;
    }
    for(auto& item:f->colliders){
        auto& state=*item.second;
        // Native sphere/box operators each perform two half-dt ping-pong passes.
        // Their final output aliases the real Grid velocity variable returned by
        // getRenderData(), so the next advection step observes this correction.
        // Summary/allocation feedback was already computed by Grid for this step.
        if(state.params.type==0){
            NvFlowEmitterSphereParams params=NvFlowEmitterSphereParams_default;configureCollider(params,state);
            params.radius=state.params.radiusOrHalfX;params.numSubSteps=1;params.numTraceSamples=0;
            const NvFlowEmitterSphereParams* array[]={&params};
            NvFlowEmitterSpherePinsIn in={};NvFlowEmitterSpherePinsOut out={};
            in.contextInterface=&f->iface;in.context=&f->context;in.deltaTime=dt;
            in.velocityParams=array;in.velocityParamCount=1;in.value=velocity;in.valueTemp=scratch;in.velocity=tracingVelocity;in.isPostPressure=NV_FLOW_TRUE;out.value=velocity;
            if(!state.initialized){NvFlowEmitterSphere_init(&state.sphere,NvFlowGetExtOpList()->pEmitterSphere(),&in,&out);state.initialized=true;}
            NvFlowEmitterSphere_execute(&state.sphere,&in,&out);
        }else{
            NvFlowEmitterBoxParams params=NvFlowEmitterBoxParams_default;configureCollider(params,state);
            params.halfSize={state.params.radiusOrHalfX,state.params.halfY,state.params.halfZ};
            const NvFlowEmitterBoxParams* array[]={&params};
            NvFlowEmitterBoxPinsIn in={};NvFlowEmitterBoxPinsOut out={};
            in.contextInterface=&f->iface;in.context=&f->context;in.deltaTime=dt;
            in.velocityParams=array;in.velocityParamCount=1;in.value=velocity;in.valueTemp=scratch;in.isPostPressure=NV_FLOW_TRUE;out.value=velocity;
            if(!state.initialized){NvFlowEmitterBox_init(&state.box,NvFlowGetExtOpList()->pEmitterBox(),&in,&out);state.initialized=true;}
            NvFlowEmitterBox_execute(&state.box,&in,&out);
        }
        state.resetPending=false;
    }
}
static bool validGeometry(const SceneGeometry& in,unsigned type,NvFlowUint64& ownedBytes){
    // Native buffers round upward from 64 KiB by powers of two; 4 GiB cannot
    // cross this context's u32 byte-length ABI. The browser applies tighter
    // device storage limits before invoking this setter.
    const NvFlowUint64 maxRoundedBufferBytes=1ull<<31;
    if(!in.positionCount||!validSpan(in.positions,in.positionCount,sizeof(NvFlowFloat3))
        ||in.widths||in.widthCount||in.widthScale!=1.f||in.flags>(type==2?1u:3u)
        ||in.reserved[0]||in.reserved[1]||in.reserved[2]
        ||(in.velocityCount!=0&&in.velocityCount!=in.positionCount)
        ||(!in.velocityCount&&in.velocities)||!validSpan(in.velocities,in.velocityCount,sizeof(NvFlowFloat3)))return false;
    // The context ABI transports GPU buffer byte lengths as u32. Device-specific
    // storage/dispatch limits are additionally admitted by the browser wrapper.
    if(type==2){
        if(in.indices||in.indexCount||in.minDistance!=0.f||in.maxDistance!=0.f||128ull*in.positionCount>maxRoundedBufferBytes)return false;
    }else{
        if(!in.indexCount||in.indexCount%3u||!validSpan(in.indices,in.indexCount,sizeof(unsigned))
            ||!std::isfinite(in.minDistance)||!std::isfinite(in.maxDistance)||in.minDistance>=in.maxDistance
            ||in.positionCount>unsigned(std::numeric_limits<int>::max())
            ||in.indexCount>unsigned(std::numeric_limits<int>::max())
            // The upstream mesh tree has three 256-way reductions and one root.
            ||in.indexCount/3u>(1u<<24))return false;
    }
    const NvFlowUint64 packedBytes=12ull*(in.positionCount+NvFlowUint64(in.velocityCount))+4ull*in.indexCount+8ull*(in.indexCount/3u);
    if(packedBytes>maxRoundedBufferBytes||32ull*(in.indexCount/3u)>maxRoundedBufferBytes)return false;
    const auto* positions=reinterpret_cast<const NvFlowFloat3*>(in.positions);
    const auto* velocities=reinterpret_cast<const NvFlowFloat3*>(in.velocities);
    for(unsigned i=0;i<in.positionCount;i++)if(!std::isfinite(positions[i].x)||!std::isfinite(positions[i].y)||!std::isfinite(positions[i].z))return false;
    for(unsigned i=0;i<in.velocityCount;i++)if(!std::isfinite(velocities[i].x)||!std::isfinite(velocities[i].y)||!std::isfinite(velocities[i].z))return false;
    if(type==3){
        const auto* indices=reinterpret_cast<const unsigned*>(in.indices);
        for(unsigned i=0;i<in.indexCount;i++)if(indices[i]>=in.positionCount)return false;
        for(unsigned i=0;i<in.indexCount;i+=3){
            const auto& a=positions[indices[i]];const auto& b=positions[indices[i+1]];const auto& c=positions[indices[i+2]];
            const double ax=double(b.x)-a.x,ay=double(b.y)-a.y,az=double(b.z)-a.z;
            const double bx=double(c.x)-a.x,by=double(c.y)-a.y,bz=double(c.z)-a.z;
            if(ay*bz-az*by==0.&&az*bx-ax*bz==0.&&ax*by-ay*bx==0.)return false;
        }
    }
    const NvFlowUint64 bytes=12ull*(in.positionCount+NvFlowUint64(in.velocityCount))+4ull*in.indexCount+4ull*(in.indexCount/3u);
    // Aggregate against the actual wasm address-space limit, not a geometry-count cap.
    const NvFlowUint64 heapMax=emscripten_get_heap_max();
    if(bytes>heapMax||ownedBytes>heapMax-bytes)return false;
    ownedBytes+=bytes;
    return true;
}
static unsigned applyScene(unsigned handle,unsigned layerPointer,unsigned layerCount,unsigned emitterPointer,unsigned emitterCount,
    unsigned geometryPointer,unsigned geometryCount,bool geometryEnabled){
    auto* f=checked(handle);
    if(!f||!layerCount||layerCount>32||emitterCount>1024
        ||geometryCount>emitterCount||f->geometryRevision==std::numeric_limits<NvFlowUint64>::max()
        ||!validSpan(layerPointer,layerCount,sizeof(SceneLayer))||!validSpan(emitterPointer,emitterCount,sizeof(SceneEmitter))
        ||!validSpan(geometryPointer,geometryCount,sizeof(SceneGeometry)))return 0;
    const auto* inputLayers=reinterpret_cast<const SceneLayer*>(layerPointer);
    const auto* inputEmitters=reinterpret_cast<const SceneEmitter*>(emitterPointer);
    const auto* inputGeometry=reinterpret_cast<const SceneGeometry*>(geometryPointer);
    std::set<unsigned> layerIds,emitterIds;
    std::vector<NvFlowGridSimulateLayerParams> layers;
    std::vector<NvFlowEmitterSphereParams> spheres;
    std::vector<NvFlowEmitterBoxParams> boxes;
    std::vector<ScenePointStorage> points;
    std::vector<SceneMeshStorage> meshes;
    std::set<unsigned> geometryIds;
    NvFlowUint64 ownedBytes=0;
    const NvFlowUint64 revision=f->geometryRevision+1;
    for(unsigned i=0;i<geometryCount;i++)if(!inputGeometry[i].id||!geometryIds.insert(inputGeometry[i].id).second)return 0;
    for(unsigned i=0;i<layerCount;i++){
        const auto& in=inputLayers[i];
        if(in.id>65535||!layerIds.insert(in.id).second||!std::isfinite(in.cellSize)||in.cellSize<=0
            ||!std::isfinite(in.gravity[0])||!std::isfinite(in.gravity[1])||!std::isfinite(in.gravity[2])
            ||in.pressure>1||in.combustion>1||!std::isfinite(in.vorticity)||in.vorticity<0||in.vorticity>10)return 0;
        auto out=NvFlowGridSimulateLayerParams_default;
        out.luid=(NvFlowUint64(1)<<32)|in.id;out.layer=int(in.id);out.densityCellSize=in.cellSize;
        out.enableHighPrecisionVelocity=NV_FLOW_TRUE;out.enableHighPrecisionDensity=NV_FLOW_TRUE;
        out.advection.gravity={in.gravity[0],in.gravity[1],in.gravity[2]};
        out.advection.combustionEnabled=in.combustion;out.pressure.enabled=in.pressure;
        out.vorticity.enabled=in.vorticity>0;out.vorticity.forceScale=in.vorticity;
        layers.push_back(out);
    }
    for(unsigned i=0;i<emitterCount;i++){
        const auto& in=inputEmitters[i];
        if(!in.id||!emitterIds.insert(in.id).second||!layerIds.count(in.layer)||in.type>(geometryEnabled?3u:1u)||in.flags>3
            ||in.reserved[0]||in.reserved[1]||in.reserved[2])return 0;
        // Validate each floating ABI word before touching the persistent scene.
        float values[25];std::memcpy(values,&in.position[0],sizeof(values));
        for(float value:values)if(!std::isfinite(value))return 0;
        const float* q=in.quaternion;const double norm=double(q[0])*q[0]+double(q[1])*q[1]+double(q[2])*q[2]+double(q[3])*q[3];
        if((in.type<2&&in.radiusOrHalfX<=0)||(in.type==1&&(in.halfY<=0||in.halfZ<=0))||std::abs(norm-1.)>1e-4
            ||in.temperature<0||in.fuel<0||in.smoke<0||in.burn<0||in.allocationScale<0
            ||in.rateVelocity<0||in.rateTemperature<0||in.rateFuel<0||in.rateSmoke<0||in.rateDivergence<0||in.rateBurn<0)return 0;
        if(in.type==0){auto out=NvFlowEmitterSphereParams_default;configureEmitter(out,in);out.position={0,0,0};out.allocationScale=in.allocationScale;out.radius=in.radiusOrHalfX;spheres.push_back(out);}
        else if(in.type==1){auto out=NvFlowEmitterBoxParams_default;configureEmitter(out,in);out.position={0,0,0};out.allocationScale=in.allocationScale;out.halfSize={in.radiusOrHalfX,in.halfY,in.halfZ};boxes.push_back(out);}
        else{
            if(in.radiusOrHalfX!=0.f||in.halfY!=0.f||in.halfZ!=0.f||in.allocationScale!=1.f)return 0;
            const SceneGeometry* geometry=nullptr;
            for(unsigned n=0;n<geometryCount;n++)if(inputGeometry[n].id==in.id){geometry=inputGeometry+n;break;}
            if(!geometry||!validGeometry(*geometry,in.type,ownedBytes))return 0;
            geometryIds.erase(in.id);
            const auto& g=*geometry;
            const auto* positions=reinterpret_cast<const NvFlowFloat3*>(g.positions);
            const auto* velocities=reinterpret_cast<const NvFlowFloat3*>(g.velocities);
            if(in.type==2){
                ScenePointStorage storage;auto& out=storage.params;configureEmitter(out,in);
                storage.positions.assign(positions,positions+g.positionCount);
                if(g.velocityCount)storage.velocities.assign(velocities,velocities+g.velocityCount);
                out.allocateMask=(g.flags&1)?NV_FLOW_TRUE:NV_FLOW_FALSE;
                out.pointPositions=storage.positions.data();out.pointPositionCount=storage.positions.size();out.pointPositionVersion=revision;
                out.pointVelocities=storage.velocities.data();out.pointVelocityCount=storage.velocities.size();out.pointVelocityVersion=revision;
                points.push_back(std::move(storage));
            }else{
                SceneMeshStorage storage;auto& out=storage.params;configureEmitter(out,in);
                storage.positions.assign(positions,positions+g.positionCount);
                if(g.velocityCount)storage.velocities.assign(velocities,velocities+g.velocityCount);
                const auto* indices=reinterpret_cast<const unsigned*>(g.indices);
                storage.indices.assign(indices,indices+g.indexCount);storage.faceCounts.assign(g.indexCount/3u,3);
                out.allocateMask=(g.flags&1)?NV_FLOW_TRUE:NV_FLOW_FALSE;out.orientationLeftHanded=(g.flags&2)?NV_FLOW_TRUE:NV_FLOW_FALSE;
                out.minDistance=g.minDistance;out.maxDistance=g.maxDistance;
                out.meshPositions=storage.positions.data();out.meshPositionCount=storage.positions.size();out.meshPositionVersion=revision;
                out.meshVelocities=storage.velocities.data();out.meshVelocityCount=storage.velocities.size();out.meshVelocityVersion=revision;
                out.meshFaceVertexIndices=storage.indices.data();out.meshFaceVertexIndexCount=storage.indices.size();out.meshFaceVertexIndexVersion=revision;
                out.meshFaceVertexCounts=storage.faceCounts.data();out.meshFaceVertexCountCount=storage.faceCounts.size();out.meshFaceVertexCountVersion=revision;
                meshes.push_back(std::move(storage));
            }
        }
    }
    if(!geometryIds.empty())return 0;
    // Colliders own motion history independently of emitter snapshots. Removing
    // their layer is an explicit error until callers remove those colliders.
    for(const auto& item:f->colliders)if(!layerIds.count(item.second->params.layer))return 0;
    for(const auto& shape:f->solids.shapes)if(!layerIds.count(shape.layer))return 0;
    for(const auto& source:f->scalarSources.records)if(!layerIds.count(source.layer))return 0;
    if(!f->solids.shapes.empty())for(const auto& layer:layers){
        float oldSize=f->layer.densityCellSize;
        if(f->customScene)for(const auto& old:f->layers)if(old.layer==layer.layer)oldSize=old.densityCellSize;
        if(oldSize!=layer.densityCellSize)return 0;
    }
    f->layers.swap(layers);f->spheres.swap(spheres);f->boxes.swap(boxes);f->points.swap(points);f->meshes.swap(meshes);
    f->geometryRevision=revision;f->customScene=true;
    return 1;
}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_scene(unsigned handle,unsigned layerPointer,unsigned layerCount,unsigned emitterPointer,unsigned emitterCount){
    return applyScene(handle,layerPointer,layerCount,emitterPointer,emitterCount,0,0,false);
}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_scene_geometry(unsigned handle,unsigned layerPointer,unsigned layerCount,unsigned emitterPointer,unsigned emitterCount,unsigned geometryPointer,unsigned geometryCount){
    return applyScene(handle,layerPointer,layerCount,emitterPointer,emitterCount,geometryPointer,geometryCount,true);
}

#include "rebase_host.h"
#include "momentum_exchange_host.h"
