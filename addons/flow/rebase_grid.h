// SPDX-License-Identifier: MIT
// Included after the pinned NVIDIA Grid.cpp. No upstream file is edited.
#include "rebase_common.h"
extern "C" bool prFlowGridRebasePeriod(NvFlowGrid* grid,double* out) {
    auto* p=NvFlowGridSimple::cast(grid);
    return prFlowSparseRebasePeriod(p->sparse,out) && prFlowSparseRebasePeriod(p->sparseIsosurface,out);
}
extern "C" bool prFlowGridRebase(NvFlowContext* context,NvFlowGrid* grid,NvFlowOp*(*unwrap)(NvFlowOp*),const PrFlowRebaseShift* shift,PrFlowRebasePlan& plan) {
    auto* p=NvFlowGridSimple::cast(grid);
    // Every helper stages writes only. Readbacks stay mapped in the plan until
    // all validation has succeeded and the synchronous commit has completed.
    return prFlowSparseRebase(context,p->sparse,shift,plan)
        && prFlowSparseRebase(context,p->sparseIsosurface,shift,plan)
        && prFlowSummaryRebase(context,p->mSummary.op,shift,plan)
        && prFlowSphereRebase(unwrap(p->mEmitterSphere.op),p->mEmitterSphereAllocate.op,shift,plan)
        && prFlowBoxRebase(unwrap(p->mEmitterBox.op),shift,plan)
        && prFlowMeshRebase(unwrap(p->mEmitterMesh.op),shift,plan);
}
