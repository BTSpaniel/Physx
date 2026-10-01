// SPDX-License-Identifier: MIT
// Included after the native context and solid_operators.h. No foreign operator
// owns this state: BrowserFlow installs and releases it with its context.
#pragma once
#include <unordered_map>
#include <algorithm>
#include <cstdint>
#include <initializer_list>

namespace prscalar {
struct Sample { float point[4]; int location[4]; float centreVolume[4]; unsigned meta[4]; };
struct Cell { int location[4]; float centreVolume[4]; unsigned range[4]; };
struct Params { NvFlowSparseLevelParams level; unsigned counts[4]; float time[4]; };
static_assert(sizeof(Sample)==64 && sizeof(Cell)==48 && sizeof(Params)==192,"Scalar dispatch layout");
struct KeyHash {
    std::size_t operator()(const std::array<int,4>& key) const {
        std::size_t value=2166136261u;
        for(int part:key)value=(value^static_cast<unsigned>(part))*16777619u;
        return value;
    }
};
static void buffer(NvFlowContext* c,NvFlowBuffer*& target,unsigned size,unsigned stride,unsigned usage,NvFlowMemoryType memory) {
    size=std::max(size,std::max(stride,16u));
    if(target && target->desc.sizeInBytes>=size)return;
    NvFlowBufferDesc desc={};desc.sizeInBytes=size;desc.structureStride=stride;
    desc.usageFlags=NvFlowBufferUsageFlags(usage);desc.format=eNvFlowFormat_unknown;
    NvFlowBuffer* replacement=createBuffer(c,memory,&desc);
    destroyBuffer(c,target);target=replacement;
}
static void upload(NvFlowContext* c,NvFlowBuffer*& target,const void* data,unsigned size,unsigned stride,unsigned usage=eNvFlowBufferUsage_structuredBuffer) {
    buffer(c,target,size,stride,usage,eNvFlowMemoryType_upload);
    if(size){std::memcpy(target->mapped.data(),data,size);gpu(c,3,target->id,address(data),size);}
}
static void dispatch(NvFlowContext* c,unsigned pipeline,unsigned count,std::initializer_list<unsigned> bindings) {
    if(count)gpu(c,7,pipeline,address(bindings.begin()),unsigned(bindings.size()/3),(count+63)/64,1,1);
}
static std::array<float,3> point(const PrScalarRecord& source,unsigned sample) {
    const prflow::V3 local={
        source.halfSize[0]*(-.75f+.5f*float(sample&3u)),
        source.halfSize[1]*(-.75f+.5f*float((sample>>2u)&3u)),
        source.halfSize[2]*(-.75f+.5f*float((sample>>4u)&3u))};
    const prflow::V3 q={source.quaternion[0],source.quaternion[1],source.quaternion[2]};
    const auto t=prflow::mul(prflow::cross(q,local),2.f);
    const auto rotated=prflow::add(local,prflow::add(prflow::mul(t,source.quaternion[3]),prflow::cross(q,t)));
    return {source.position[0]+rotated[0],source.position[1]+rotated[1],source.position[2]+rotated[2]};
}
}

static bool prScalarSet(NvFlowContext* c,const PrScalarRecord* records,unsigned count) {
    if(!c || !c->scalarSources || count>4096u || (count && !records))return false;
    std::set<unsigned> ids;
    for(unsigned i=0;i<count;i++){
        const auto& p=records[i];
        if(!p.id || p.layer>65535u || p.enabled>1u || p.pad || p.position[3]!=0.f || p.halfSize[3]!=0.f
            || !ids.insert(p.id).second)return false;
        for(unsigned axis=0;axis<4;axis++){
            if(!std::isfinite(p.position[axis]) || !std::isfinite(p.quaternion[axis])
                || !std::isfinite(p.halfSize[axis]) || !std::isfinite(p.totalRates[axis]))return false;
            if(axis<3 && p.halfSize[axis]<=0.f)return false;
            if(axis>0 && p.totalRates[axis]<0.f)return false;
        }
        double norm=0.;for(float value:p.quaternion)norm+=double(value)*value;
        if(std::abs(norm-1.)>1.e-4)return false;
    }
    std::vector<PrScalarRecord> next;if(count)next.assign(records,records+count);
    c->scalarSources->records.swap(next);return true;
}

static void prScalarApply(NvFlowContext* c,NvFlowSparseTexture& field,float dt) {
    auto* state=c->scalarSources;if(!state || state->appliedFrame==c->frame)return;
    auto& s=*state;const unsigned sourceCount=unsigned(s.records.size());
    // Match the public native host's admitted timestep range. A finite f32
    // rate times dt<=0.1 cannot overflow a per-source integrated receipt.
    if(!std::isfinite(dt) || dt<=0.f || dt>.1f){gpu(c,99);return;}
    if(!sourceCount){s.appliedFrame=c->frame;s.publishedCount=0;s.publishedDt=dt;return;}
    if(field.textureTransient && field.format!=eNvFlowFormat_r32g32b32a32_float){gpu(c,99);return;}
    const bool validField=field.textureTransient && field.sparseBuffer && field.sparseParams.levels
        && field.levelIdx<field.sparseParams.levelCount;
    const NvFlowSparseLevelParams level=validField?field.sparseParams.levels[field.levelIdx]:NvFlowSparseLevelParams{};
    const unsigned sampleCount=sourceCount*64u;
    std::vector<prscalar::Sample> samples(sampleCount);
    std::vector<prscalar::Cell> cells;
    std::vector<std::vector<unsigned>> cellSamples;
    std::unordered_map<std::array<int,4>,unsigned,prscalar::KeyHash> cellIds;
    cellIds.reserve(sampleCount);cells.reserve(sampleCount);
    for(unsigned source=0;source<sourceCount;source++){
        const auto& record=s.records[source];const NvFlowSparseLayerParams* layer=nullptr;
        for(unsigned n=0;validField && n<field.sparseParams.layerCount;n++){
            const auto& candidate=field.sparseParams.layers[n];
            const auto decoded=NvFlow_unpackLayerAndLevel(candidate.layerAndLevel);
            if(unsigned(decoded.x)==record.layer && decoded.y==0){layer=&candidate;break;}
        }
        double cellSize[3]={};float volume=0.f;
        if(layer){
            cellSize[0]=double(layer->blockSizeWorld.x)/double(level.blockDimLessOne.x+1u);
            cellSize[1]=double(layer->blockSizeWorld.y)/double(level.blockDimLessOne.y+1u);
            cellSize[2]=double(layer->blockSizeWorld.z)/double(level.blockDimLessOne.z+1u);
            volume=float(cellSize[0]*cellSize[1]*cellSize[2]);
        }
        for(unsigned n=0;n<64;n++){
            const unsigned index=source*64u+n;auto& sample=samples[index];
            sample.meta[0]=source;sample.meta[2]=record.layer;
            const auto point=prscalar::point(record,n);
            for(unsigned axis=0;axis<3;axis++)sample.point[axis]=point[axis];
            if(!record.enabled || !layer || !(volume>0.f) || !std::isfinite(volume))continue;
            std::array<int,4> key={0,0,0,layer->layerAndLevel};bool valid=true;
            for(unsigned axis=0;axis<3;axis++){
                const double coordinate=std::floor(double(point[axis])/cellSize[axis]);
                if(!std::isfinite(coordinate) || coordinate<=double(INT32_MIN)+1. || coordinate>=double(INT32_MAX)-1.){valid=false;break;}
                key[axis]=int(coordinate);sample.centreVolume[axis]=float((coordinate+.5)*cellSize[axis]);
            }
            if(!valid)continue;
            auto found=cellIds.find(key);unsigned cell;
            if(found==cellIds.end()){
                cell=unsigned(cells.size());cellIds.emplace(key,cell);cells.push_back({});cellSamples.emplace_back();
                auto& target=cells.back();std::copy(key.begin(),key.end(),target.location);
                std::copy(sample.centreVolume,sample.centreVolume+3,target.centreVolume);
                target.centreVolume[3]=volume;target.range[2]=record.layer;
            }else cell=found->second;
            std::copy(key.begin(),key.end(),sample.location);sample.centreVolume[3]=volume;
            sample.meta[1]=cell;sample.meta[3]=1u;cellSamples[cell].push_back(index);
        }
    }
    std::vector<unsigned> members;members.reserve(sampleCount);
    for(unsigned i=0;i<cells.size();i++){
        cells[i].range[0]=unsigned(members.size());cells[i].range[1]=unsigned(cellSamples[i].size());
        members.insert(members.end(),cellSamples[i].begin(),cellSamples[i].end());
    }
    prscalar::Params params={};params.level=level;params.counts[0]=sourceCount;
    params.counts[1]=sampleCount;params.counts[2]=unsigned(cells.size());params.time[0]=dt;
    prscalar::upload(c,s.params,&params,sizeof(params),16,eNvFlowBufferUsage_constantBuffer);
    prscalar::upload(c,s.sources,s.records.data(),sourceCount*sizeof(PrScalarRecord),sizeof(PrScalarRecord));
    prscalar::upload(c,s.samples,samples.data(),sampleCount*sizeof(prscalar::Sample),sizeof(prscalar::Sample));
    prscalar::upload(c,s.cells,cells.data(),unsigned(cells.size()*sizeof(prscalar::Cell)),sizeof(prscalar::Cell));
    prscalar::upload(c,s.members,members.data(),unsigned(members.size()*sizeof(unsigned)),sizeof(unsigned));
    prscalar::buffer(c,s.admission,sampleCount*16,16,eNvFlowBufferUsage_rwStructuredBuffer,eNvFlowMemoryType_device);
    prscalar::buffer(c,s.counts,sourceCount*4,4,eNvFlowBufferUsage_rwStructuredBuffer,eNvFlowMemoryType_device);
    prscalar::buffer(c,s.ratios,unsigned(cells.size())*32,32,eNvFlowBufferUsage_rwStructuredBuffer,eNvFlowMemoryType_device);
    prscalar::buffer(c,s.receipt,sourceCount*sizeof(PrScalarReceipt),sizeof(PrScalarReceipt),eNvFlowBufferUsage_rwStructuredBuffer|eNvFlowBufferUsage_bufferCopySrc,eNvFlowMemoryType_device);
    prscalar::buffer(c,s.readback,sourceCount*sizeof(PrScalarReceipt),sizeof(PrScalarReceipt),eNvFlowBufferUsage_bufferCopyDst,eNvFlowMemoryType_readback);
    static const char* names[]={"addons/scalar/PrScalarAdmitCS.wgsl","addons/scalar/PrScalarCountCS.wgsl","addons/scalar/PrScalarApplyCS.wgsl","addons/scalar/PrScalarReceiptCS.wgsl"};
    for(unsigned i=0;i<4;i++)if(!s.pipelines[i])s.pipelines[i]=gpu(c,6,address(names[i]));
    NvFlowBuffer* boundary=nullptr;NvFlowBuffer* control=nullptr;
    if(c->solids && !c->solids->shapes.empty()){
        solidPrepare(c,field,0);boundary=c->solids->packed;control=c->solids->control[0];
    }else{
        const unsigned zero[8]={};prscalar::upload(c,s.emptyBoundary,zero,sizeof(zero),16);
        prscalar::upload(c,s.emptyControl,zero,16,16,eNvFlowBufferUsage_constantBuffer);
        boundary=s.emptyBoundary;control=s.emptyControl;
    }
    // No native field means no resident fluid. The admission pass still writes
    // all-zero flags and the receipt pass publishes the entire deferred budget.
    const unsigned table=field.sparseBuffer?field.sparseBuffer->id:boundary->id;
    prscalar::dispatch(c,s.pipelines[0],sampleCount,{0,1,s.params->id,1,2,s.samples->id,2,2,table,
        3,6,s.admission->id,30,2,boundary->id,31,1,control->id});
    prscalar::dispatch(c,s.pipelines[1],sourceCount,{0,1,s.params->id,1,2,s.admission->id,2,6,s.counts->id});
    if(!cells.empty()){
        NvFlowTextureDesc desc=field.textureTransient->desc;
        desc.usageFlags|=eNvFlowTextureUsage_rwTexture|eNvFlowTextureUsage_textureCopyDst|eNvFlowTextureUsage_textureCopySrc;
        auto* next=getTextureTransient(c,&desc);
        const unsigned copy[]={0,0,0,0,0,0,0,0,desc.width,desc.height,desc.depth};
        gpu(c,11,field.textureTransient->id,next->id,address(copy));
        prscalar::dispatch(c,s.pipelines[2],unsigned(cells.size()),{0,1,s.params->id,1,2,s.sources->id,2,2,s.cells->id,
            3,2,s.members->id,4,2,s.admission->id,5,2,s.counts->id,6,4,field.textureTransient->id,7,8,next->id,8,6,s.ratios->id});
        field.textureTransient=next;
    }
    prscalar::dispatch(c,s.pipelines[3],sourceCount,{0,1,s.params->id,1,2,s.sources->id,2,2,s.samples->id,
        3,2,s.admission->id,4,2,s.counts->id,5,2,s.ratios->id,6,6,s.receipt->id});
    s.appliedFrame=c->frame;s.publishedCount=sourceCount;s.publishedDt=dt;
}

// Called once after the graph executes. The browser's existing native readback
// queue maps this buffer before completion; callers copy this borrowed receipt.
static void prScalarPublish(NvFlowContext* c,float dt) {
    auto* s=c->scalarSources;if(!s)return;
    if(s->records.empty()){s->publishedCount=0;s->publishedDt=dt;}
    if(!s->records.empty() && s->appliedFrame!=c->frame){gpu(c,99);return;}
    s->publishedFrame=c->frame;
    if(s->publishedCount)gpu(c,8,s->receipt->id,s->readback->id,0,0,s->publishedCount*sizeof(PrScalarReceipt));
}
static const PrScalarReceipt* prScalarReceiptData(const NvFlowContext* c) {
    const auto* s=c->scalarSources;
    return s && s->publishedCount && s->readback?reinterpret_cast<const PrScalarReceipt*>(s->readback->mapped.data()):nullptr;
}
static void prScalarDestroy(NvFlowContext* c) {
    auto* s=c->scalarSources;if(!s)return;
    NvFlowBuffer** buffers[]={&s->params,&s->sources,&s->samples,&s->cells,&s->members,&s->admission,&s->counts,
        &s->ratios,&s->receipt,&s->readback,&s->emptyBoundary,&s->emptyControl};
    for(auto** buffer:buffers){destroyBuffer(c,*buffer);*buffer=nullptr;}
    for(unsigned& pipeline:s->pipelines){if(pipeline)gpu(c,2,pipeline);pipeline=0;}
    s->records.clear();s->publishedCount=0;c->scalarSources=nullptr;
}
