// SPDX-License-Identifier: MIT
// Included after the pinned NVIDIA Summary.cpp. The browser has completed all
// submitted work and copied its readbacks before this transaction is admitted.
#include "rebase_common.h"
extern "C" bool prFlowSummaryRebase(NvFlowContext* context,NvFlowOp* op,const PrFlowRebaseShift* shift,PrFlowRebasePlan& plan) {
    auto* p=reinterpret_cast<Summary*>(op);
    auto& reads=p->summaryReadback;
    for(NvFlowUint64 i=0;i<reads.activeBuffers.size;i++) {
        auto& inst=reads.buffers[reads.activeBuffers[i]];
        if(inst.completedFrame>p->contextInterface.getLastFrameCompleted(context))return false;
        if(!inst.validNumBytes)continue;
        if(inst.validNumBytes<sizeof(SummaryCS_Header))return false;
        auto* data=static_cast<unsigned char*>(plan.map(p->contextInterface,context,inst.buffer));
        if(!data)return false;
        auto* header=reinterpret_cast<SummaryCS_Header*>(data);
        const uint64_t offset=sizeof(SummaryCS_Header)+uint64_t(header->layerCount)*sizeof(SummaryCS_HeaderLayer);
        if(offset>inst.validNumBytes || uint64_t(header->numLocations)*sizeof(SummaryCS_Location)>inst.validNumBytes-offset)return false;
        auto* layers=reinterpret_cast<SummaryCS_HeaderLayer*>(data+sizeof(SummaryCS_Header));
        auto* locations=reinterpret_cast<SummaryCS_Location*>(data+offset);
        for(unsigned j=0;j<header->numLocations;j++) {
            auto& location=locations[j].location;bool found=false;
            for(unsigned k=0;k<header->layerCount;k++)if(layers[k].layerAndLevel==location.w) {
                if(!prRebaseLocation(location,layers[k].blockSizeWorld,*shift,1,plan))return false;
                found=true;break;
            }
            if(!found)return false;
        }
    }
    return true;
}
