// SPDX-License-Identifier: MIT
#include "rebase_common.h"

static bool prHostRebase(BrowserFlow* f,const PrFlowRebaseShift& shift,bool commit) {
    // Rebase only at a completed shared-clock boundary. Legacy velocity-only
    // colliders have independent native histories not covered by this ABI.
    if(!f || !prRebaseFinite(shift) || !f->colliders.empty()
        || f->context.completed+1!=f->context.frame || !f->context.completed)return false;
    PrFlowRebasePlan plan;
    auto sources=[&]() {
        if(!f->customScene && !prRebasePosition(f->emitter.position,shift,plan))return false;
        for(auto& e:f->spheres)if(!prRebaseTransform(e.localToWorld,shift,plan))return false;
        for(auto& e:f->boxes)if(!prRebaseTransform(e.localToWorld,shift,plan))return false;
        for(auto& e:f->points)if(!prRebaseTransform(e.params.localToWorld,shift,plan))return false;
        for(auto& e:f->meshes)if(!prRebaseTransform(e.params.localToWorld,shift,plan))return false;
        const double d[3]={shift.x,shift.y,shift.z};
        for(auto& s:f->solids.shapes)for(unsigned a=0;a<3;a++) {
            if(!prRebasePosition(s.positionRadius[a],d[a],plan)
                || !prRebasePosition(s.previousPosition[a],d[a],plan))return false;
        }
        for(auto& s:f->scalarSources.records)for(unsigned a=0;a<3;a++)
            if(!prRebasePosition(s.position[a],d[a],plan))return false;
        return true;
    };
    if(f->solids.version==std::numeric_limits<unsigned>::max() || f->context.frame==std::numeric_limits<NvFlowUint64>::max())return false;
    if(!sources() || !prFlowGridRebase(&f->context,f->grid,prSolidUnderlyingOp,&shift,plan))return false;
    if(!commit)return true;
    // Stage derived BVH storage before changing any published state.
    auto shiftedShapes=f->solids.shapes;
    for(auto& shape:shiftedShapes)for(unsigned a=0;a<3;a++) {
        const double delta=a==0?shift.x:a==1?shift.y:shift.z;
        shape.positionRadius[a]=float(double(shape.positionRadius[a])-delta);
        shape.previousPosition[a]=float(double(shape.previousPosition[a])-delta);
    }
    auto shiftedNodes=prflow::buildBvh(shiftedShapes);
    plan.commit();
    f->solids.nodes.swap(shiftedNodes);
    f->solids.uploadedFrame=0;f->solids.version++;
    // Scalar sampling is rebuilt on the next ordinary step from owned records.
    NvFlowGridRenderData render={};f->gridIface->getRenderData(&f->context,f->grid,&render);
    publishFlowOutput(f,render);
    return true;
}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_rebase_abi(){return 1;}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_rebase_period(unsigned handle,unsigned output) {
    auto* f=checked(handle);
    if(!f || !output || output%8 || output>emscripten_get_heap_size()-24)return 0;
    double period[3]={0.,0.,0.};
    if(!prFlowGridRebasePeriod(f->grid,period) || period[0]<=0. || period[1]<=0. || period[2]<=0.)return 0;
    std::memcpy(reinterpret_cast<void*>(output),period,24);return 1;
}
extern "C" EMSCRIPTEN_KEEPALIVE unsigned pr_flow_host_rebase(unsigned handle,double x,double y,double z,unsigned commit) {
    if(commit>1)return 0;
    return prHostRebase(checked(handle),{x,y,z},commit!=0)?1:0;
}
