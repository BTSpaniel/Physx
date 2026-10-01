// SPDX-License-Identifier: MIT
// Selected one branch per pinned NVIDIA translation unit by the generator.
#include "rebase_common.h"
#if defined(PR_FLOW_REBASE_SPHERE)
extern "C" bool prFlowSphereRebase(NvFlowOp* emitter,NvFlowOp* allocate,const PrFlowRebaseShift* s,PrFlowRebasePlan& plan) {
    auto* e=reinterpret_cast<EmitterSphere*>(emitter);
    auto* a=reinterpret_cast<EmitterSphereAllocate*>(allocate);
    for(auto* history:{&e->oldLocalToWorlds,&a->oldLocalToWorlds})
        for(NvFlowUint64 i=0;i<history->size;i++)if(!prRebaseTransform((*history)[i],*s,plan))return false;
    return true;
}
#elif defined(PR_FLOW_REBASE_BOX)
extern "C" bool prFlowBoxRebase(NvFlowOp* emitter,const PrFlowRebaseShift* s,PrFlowRebasePlan& plan) {
    auto* e=reinterpret_cast<EmitterBox*>(emitter);
    for(NvFlowUint64 i=0;i<e->oldLocalToWorlds.size;i++)if(!prRebaseTransform(e->oldLocalToWorlds[i],*s,plan))return false;
    return true;
}
#elif defined(PR_FLOW_REBASE_MESH)
extern "C" bool prFlowMeshRebase(NvFlowOp* emitter,const PrFlowRebaseShift* s,PrFlowRebasePlan& plan) {
    auto* e=reinterpret_cast<EmitterMesh*>(emitter);
    for(NvFlowUint64 i=0;i<e->instances.size;i++)if(!prRebaseTransform(e->instances[i]->oldLocalToWorld,*s,plan))return false;
    return true;
}
#endif
