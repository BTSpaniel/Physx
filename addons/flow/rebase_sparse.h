// SPDX-License-Identifier: MIT
// Included AFTER the pinned, unmodified NVIDIA Sparse.cpp translation unit.
#include "rebase_common.h"

extern "C" bool prFlowSparseRebasePeriod(NvFlowSparse* sparse,double* out) {
    const auto* p=NvFlowSparseDefault::cast(sparse);
    if(p->layerParams.size==0)return true;
    const unsigned period=p->hashTable.tableDimLessOne+1u;
    for(NvFlowUint64 i=0;i<p->layerParams.size;i++) {
        const auto b=p->layerParams[i].blockSizeWorld;
        const double values[3]={double(b.x)*period,double(b.y)*period,double(b.z)*period};
        for(unsigned a=0;a<3;a++) {
            if(!std::isfinite(values[a]) || !(values[a]>0.))return false;
            if(out[a]==0.)out[a]=values[a];
            else {
                // Native layers normally differ by powers of two. Admit only
                // an exact common multiple; arbitrary noncommensurate layers
                // require an explicit caller shift checked by the full guard.
                const double hi=std::fmax(out[a],values[a]),lo=std::fmin(out[a],values[a]);
                if(hi/lo!=std::trunc(hi/lo) || (hi/lo)*lo!=hi)return false;
                out[a]=hi;
            }
        }
    }
    return true;
}

extern "C" bool prFlowSparseRebase(NvFlowContext* context,NvFlowSparse* sparse,const PrFlowRebaseShift* shift,PrFlowRebasePlan& plan) {
    auto* p=NvFlowSparseDefault::cast(sparse);
    if(!shift || !prRebaseFinite(*shift) || p->resetPending || p->nanoVdbs.size
        || p->tableVersion==std::numeric_limits<NvFlowUint64>::max())return false;
    if(p->hashTable.locations.size==0)return true;
    const unsigned period=p->hashTable.tableDimLessOne+1u;
    auto location=[&](NvFlowInt4& v) {
        for(NvFlowUint64 i=0;i<p->layerParams.size;i++)if(p->layerParams[i].layerAndLevel==v.w) {
            const auto block=p->layerParams[i].blockSizeWorld;
            const double next[3]={double(v.x)*block.x-shift->x,double(v.y)*block.y-shift->y,double(v.z)*block.z-shift->z};
            const float widths[3]={block.x,block.y,block.z};
            for(unsigned a=0;a<3;a++)if(!std::isfinite(float(next[a])) || !std::isfinite(float(next[a]+widths[a])))return false;
            return prRebaseLocation(v,block,*shift,period,plan);
        }
        return false;
    };
    // A hash-period translation retains bucket order and atlas allocation IDs.
    for(NvFlowUint64 i=0;i<p->hashTable.locations.size;i++)if(!location(p->hashTable.locations[i]))return false;
    for(NvFlowUint64 i=0;i<p->allocationActives.size;i++)if(p->allocationActives[i] && !location(p->allocationLocations[i]))return false;
    for(NvFlowUint64 i=0;i<p->layerParamsOld.size;i++) {
        auto& layer=p->layerParamsOld[i];
        if(!prRebaseLocation(layer.locationMin,layer.blockSizeWorld,*shift,period,plan)
            || !prRebaseLocation(layer.locationMax,layer.blockSizeWorld,*shift,period,plan)
            || !prRebasePosition(layer.worldMin,*shift,plan) || !prRebasePosition(layer.worldMax,*shift,plan))return false;
    }
    plan.assign(p->tableVersion,p->tableVersion+1);
    plan.afterWrites.push_back([p,context,sparse]() {
        p->hashTable.computeStats();
        NvFlowSparseDefault::generateLayerLocationRanges(p,unsigned(p->hashTable.locations.size),p->hashTable.locations.data);
        for(NvFlowUint64 i=0;i<p->levelParams.size;i++) {
            p->levelParams[i].globalLocationMin=p->hashTable.locationMin;
            p->levelParams[i].globalLocationMax=p->hashTable.locationMax;
        }
        NvFlowBufferTransient* buffer=nullptr;
        NvFlowSparseDefault::addPasses(context,sparse,&buffer);
    });
    return true;
}
