// SPDX-License-Identifier: MIT
// Public OpList adapters. NVIDIA operators retain scheduling and ownership;
// selected shader variants receive one shared, threaded analytic shape BVH.
#pragma once
static bool solidShader(const std::string& name) {
    static const std::set<std::string> names={
        "AdvectionDensity1CS","AdvectionDensity2CS","AdvectionVelocity1CS","AdvectionVelocity2CS",
        "AdvectionDownsampleCS","AdvectionFadeDensityCS","AdvectionFadeVelocityCS","AdvectionSimpleCS",
        "PressureDivergenceCS","PressureJacobiCS","PressureSubtractCS","EmitterSimpleCS","EmitterBoxCS",
        "EmitterMeshApplyCS","EmitterNanoVdb2CS","EmitterPoint3CS","EmitterPoint4CS",
        "EmitterPointClearDownsampleCS","EmitterPointClearMarkedCS","EmitterTextureCS","Vorticity2CS"};
    return names.count(name)!=0;
}
static NvFlowBuffer* solidUpload(NvFlowContext* c,NvFlowBuffer*& buffer,const void* data,unsigned size,unsigned usage) {
    if(!buffer || buffer->desc.sizeInBytes<size){
        NvFlowBufferDesc desc={};desc.sizeInBytes=size;desc.usageFlags=NvFlowBufferUsageFlags(usage);desc.format=eNvFlowFormat_unknown;desc.structureStride=16;
        NvFlowBuffer* replacement=createBuffer(c,eNvFlowMemoryType_upload,&desc);
        destroyBuffer(c,buffer);buffer=replacement;
    }
    std::memcpy(buffer->mapped.data(),data,size);gpu(c,3,buffer->id,address(data),size);return buffer;
}
static void solidPrepare(NvFlowContext* c,const NvFlowSparseTexture& field,unsigned mode) {
    auto& s=*c->solids;c->solidMode=mode;
    if(s.shapes.empty())return;
    std::vector<std::array<unsigned,4>> layers;
    for(unsigned i=0;i<field.sparseParams.layerCount;i++){
        const auto& p=field.sparseParams.layers[i];std::array<unsigned,4> row;
        std::memcpy(row.data(),&p.blockSizeWorld,12);row[3]=unsigned(NvFlow_unpackLayerAndLevel(p.layerAndLevel).x);layers.push_back(row);
    }
    if(s.uploadedFrame==c->frame && layers==s.layers)return;
    s.layers=std::move(layers);
    const unsigned nodeOffset=2,shapeOffset=nodeOffset+unsigned(s.nodes.size())*2;
    const unsigned planeOffset=shapeOffset+unsigned(s.shapes.size())*10,layerOffset=planeOffset+unsigned(s.planes.size());
    std::vector<std::array<unsigned,4>> rows(layerOffset+s.layers.size());
    rows[0]={unsigned(s.nodes.size()),nodeOffset,shapeOffset,planeOffset};
    rows[1]={layerOffset,unsigned(s.layers.size()),unsigned(s.shapes.size()),0};
    for(unsigned i=0;i<s.nodes.size();i++){
        const auto& n=s.nodes[i];auto& lo=rows[nodeOffset+i*2];auto& hi=rows[nodeOffset+i*2+1];
        std::memcpy(lo.data(),n.bounds.lower.data(),12);lo[3]=n.shape;
        std::memcpy(hi.data(),n.bounds.upper.data(),12);hi[3]=n.escape;
    }
    if(!s.shapes.empty())std::memcpy(rows[shapeOffset].data(),s.shapes.data(),s.shapes.size()*sizeof(prflow::SolidRecord));
    if(!s.planes.empty())std::memcpy(rows[planeOffset].data(),s.planes.data(),s.planes.size()*sizeof(prflow::V4));
    if(!s.layers.empty())std::memcpy(rows[layerOffset].data(),s.layers.data(),s.layers.size()*16);
    s.packedBytes=unsigned(rows.size())*16;
    solidUpload(c,s.packed,rows.data(),s.packedBytes,eNvFlowBufferUsage_structuredBuffer);
    for(unsigned i=0;i<2;i++)if(!s.control[i]){const unsigned value[4]={i,0,0,0};solidUpload(c,s.control[i],value,16,eNvFlowBufferUsage_constantBuffer);}
    s.uploadedFrame=c->frame;
}
static void solidMotion(PrSolidState& s,float dt) {
    for(auto& p:s.shapes){
        p.linearVelocity={0,0,0,0};p.angularVelocity={0,0,0,0};
        if(!(p.flags&2u)){
            for(unsigned i=0;i<3;i++)p.linearVelocity[i]=(p.positionRadius[i]-p.previousPosition[i])/dt;
            const auto a=p.rotation,b=p.previousRotation;
            const prflow::V3 av={a[0],a[1],a[2]},bv={-b[0],-b[1],-b[2]};
            auto axis=prflow::add(prflow::add(prflow::mul(bv,a[3]),prflow::mul(av,b[3])),prflow::cross(av,bv));
            float w=a[3]*b[3]-prflow::dot(av,bv);if(w<0){w=-w;axis=prflow::mul(axis,-1.f);}
            const float length=std::sqrt(prflow::dot(axis,axis));
            if(length>1.e-8f){axis=prflow::mul(axis,2.f*std::atan2(length,w)/(length*dt));for(unsigned i=0;i<3;i++)p.angularVelocity[i]=axis[i];}
        }
    }
    s.nodes=prflow::buildBvh(s.shapes);s.uploadedFrame=0;
}
static void solidCommitMotion(PrSolidState& s) {
    for(auto& p:s.shapes){p.previousPosition=p.positionRadius;p.previousRotation=p.rotation;p.flags&=5u;}
}
static void solidFinePressure(NvFlowPressurePinsIn& in) {
    // The original single-level pressure branch performs40Jacobi iterations.
    // Restriction would merge fluid components separated by subcell sheets.
    auto* c=in.context;auto& s=*c->solids;
    if(s.nodes.empty())return;
    in.velocity.sparseParams.levelCount=in.velocity.levelIdx+1;
    // Divergence writes exact six-face visibility once for this invocation.
    // Every Jacobi pass uses the same cells, geometry and immutable mask.
    const auto& level=in.velocity.sparseParams.levels[in.velocity.levelIdx];
    const NvFlowUint64 bytes=std::max<NvFlowUint64>(4,NvFlowUint64(level.numLocations)*level.threadsPerBlock*4);
    if(!s.pressureFaces || s.pressureFaces->desc.sizeInBytes<bytes){
        NvFlowBufferDesc desc={};desc.sizeInBytes=bytes;desc.structureStride=4;desc.format=eNvFlowFormat_unknown;
        desc.usageFlags=eNvFlowBufferUsage_structuredBuffer|eNvFlowBufferUsage_rwStructuredBuffer;
        auto* replacement=createBuffer(c,eNvFlowMemoryType_device,&desc);
        destroyBuffer(c,s.pressureFaces);s.pressureFaces=replacement;
    }
}
template<class T> static void solidFinePressure(T&) {}
template<class T,class U> static void solidAfter(const T&,U&) {}
static void solidAfter(const NvFlowAdvectionCombustionDensityPinsIn& in,NvFlowAdvectionCombustionDensityPinsOut& out){
    prScalarApply(in.context,out.density,in.deltaTime);
}
struct PrSolidWrappedOp { NvFlowOpInterface* original; NvFlowOp* op; };
static NvFlowOp* prSolidUnderlyingOp(NvFlowOp* op) {return reinterpret_cast<PrSolidWrappedOp*>(op)->op;}
#define PR_SOLID_OP(NAME,LIST,FIELD,MODE) \
using Solid##NAME = PrSolidWrappedOp; \
static Solid##NAME* Solid##NAME##_create(const NvFlowOpInterface*,const NvFlow##NAME##PinsIn* in,NvFlow##NAME##PinsOut* out){ \
    auto* p=new Solid##NAME();p->original=LIST()->p##NAME();p->op=p->original->create(p->original,(const NvFlowOpGenericPinsIn*)in,(NvFlowOpGenericPinsOut*)out);return p; } \
static void Solid##NAME##_destroy(Solid##NAME* p,const NvFlow##NAME##PinsIn* in,NvFlow##NAME##PinsOut* out){p->original->destroy(p->op,(const NvFlowOpGenericPinsIn*)in,(NvFlowOpGenericPinsOut*)out);delete p;} \
static void Solid##NAME##_execute(Solid##NAME* p,const NvFlow##NAME##PinsIn* in,NvFlow##NAME##PinsOut* out){ \
    solidPrepare(in->context,in->FIELD,MODE);NvFlow##NAME##PinsIn copy=*in;solidFinePressure(copy); \
    p->original->execute(p->op,(const NvFlowOpGenericPinsIn*)&copy,(NvFlowOpGenericPinsOut*)out);solidAfter(copy,*out); } \
NV_FLOW_OP_IMPL(NvFlow##NAME,Solid##NAME)
PR_SOLID_OP(AdvectionCombustionDensity,NvFlowGetOpList,density,0u)
PR_SOLID_OP(AdvectionCombustionVelocity,NvFlowGetOpList,velocity,1u)
PR_SOLID_OP(Pressure,NvFlowGetOpList,velocity,1u)
PR_SOLID_OP(Vorticity,NvFlowGetOpList,velocity,1u)
PR_SOLID_OP(EmitterSphere,NvFlowGetExtOpList,value,in->value.levelIdx?1u:0u)
PR_SOLID_OP(EmitterBox,NvFlowGetExtOpList,value,in->value.levelIdx?1u:0u)
PR_SOLID_OP(EmitterPoint,NvFlowGetExtOpList,value,in->value.levelIdx?1u:0u)
PR_SOLID_OP(EmitterMesh,NvFlowGetExtOpList,value,in->value.levelIdx?1u:0u)
PR_SOLID_OP(EmitterTexture,NvFlowGetExtOpList,value,in->value.levelIdx?1u:0u)
PR_SOLID_OP(EmitterNanoVdb,NvFlowGetExtOpList,value,in->value.levelIdx?1u:0u)
#undef PR_SOLID_OP
static NvFlowOpList* solidOpList(){static NvFlowOpList list=*NvFlowGetOpList();
    list.pAdvectionCombustionDensity=NvFlowOp_SolidAdvectionCombustionDensity_getOpInterface;
    list.pAdvectionCombustionVelocity=NvFlowOp_SolidAdvectionCombustionVelocity_getOpInterface;
    list.pPressure=NvFlowOp_SolidPressure_getOpInterface;list.pVorticity=NvFlowOp_SolidVorticity_getOpInterface;return &list;}
static NvFlowExtOpList* solidExtOpList(){static NvFlowExtOpList list=*NvFlowGetExtOpList();
    list.pEmitterSphere=NvFlowOp_SolidEmitterSphere_getOpInterface;list.pEmitterBox=NvFlowOp_SolidEmitterBox_getOpInterface;
    list.pEmitterPoint=NvFlowOp_SolidEmitterPoint_getOpInterface;list.pEmitterMesh=NvFlowOp_SolidEmitterMesh_getOpInterface;
    list.pEmitterTexture=NvFlowOp_SolidEmitterTexture_getOpInterface;list.pEmitterNanoVdb=NvFlowOp_SolidEmitterNanoVdb_getOpInterface;return &list;}
