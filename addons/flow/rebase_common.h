// SPDX-License-Identifier: MIT
#pragma once
#include "NvFlowExt.h"
#include <cmath>
#include <cstdint>
#include <limits>
#include <array>
#include <cstring>
#include <functional>
#include <vector>

// A rebase subtracts this displacement from all local positions. It is not a
// simulation step. Native field values and physical velocities are untouched.
struct PrFlowRebaseShift { double x, y, z; };
struct PrFlowRebasePlan {
    struct Write {void* destination;std::array<unsigned char,8> bytes;unsigned size;};
    struct Mapping {NvFlowContextInterface* iface;NvFlowContext* context;NvFlowBuffer* buffer;};
    std::vector<Write> writes;
    std::vector<Mapping> mappings;
    std::vector<std::function<void()>> afterWrites;
    ~PrFlowRebasePlan(){for(auto i=mappings.rbegin();i!=mappings.rend();++i)i->iface->unmapBuffer(i->context,i->buffer);}
    template<class T> void assign(T& destination,T value) {
        static_assert(sizeof(T)<=8,"Rebase scalar write size");
        Write write={&destination,{},unsigned(sizeof(T))};std::memcpy(write.bytes.data(),&value,sizeof(T));writes.push_back(write);
    }
    void* map(NvFlowContextInterface& iface,NvFlowContext* context,NvFlowBuffer* buffer) {
        void* data=iface.mapBuffer(context,buffer);
        if(data)mappings.push_back({&iface,context,buffer});
        return data;
    }
    void commit() {for(const auto& write:writes)std::memcpy(write.destination,write.bytes.data(),write.size);for(auto& fn:afterWrites)fn();}
};
inline bool prRebaseFinite(const PrFlowRebaseShift& s) {
    return std::isfinite(s.x) && std::isfinite(s.y) && std::isfinite(s.z);
}
inline bool prRebasePosition(float& value, double shift, PrFlowRebasePlan& plan) {
    const double next=double(value)-shift;
    const float narrowed=float(next);
    if(!std::isfinite(value) || !std::isfinite(next) || !std::isfinite(narrowed))return false;
    plan.assign(value,narrowed);
    return true;
}
inline bool prRebasePosition(NvFlowFloat3& p,const PrFlowRebaseShift& s,PrFlowRebasePlan& plan) {
    return prRebasePosition(p.x,s.x,plan) && prRebasePosition(p.y,s.y,plan) && prRebasePosition(p.z,s.z,plan);
}
inline bool prRebaseTransform(NvFlowFloat4x4& m,const PrFlowRebaseShift& s,PrFlowRebasePlan& plan) {
    return prRebasePosition(m.w.x,s.x,plan) && prRebasePosition(m.w.y,s.y,plan) && prRebasePosition(m.w.z,s.z,plan);
}
inline bool prRebaseBlocks(double shift,float block,unsigned period,int& output) {
    if(!std::isfinite(shift) || !std::isfinite(block) || !(block>0.f) || !period)return false;
    const double blocks=shift/double(block);
    if(!std::isfinite(blocks) || blocks!=std::trunc(blocks) || blocks*double(block)!=shift
        || blocks<double(std::numeric_limits<int>::min()) || blocks>double(std::numeric_limits<int>::max()))return false;
    const auto value=int64_t(blocks);
    if(value%int64_t(period))return false;
    output=int(value);return true;
}
inline bool prRebaseLocation(NvFlowInt4& p,NvFlowFloat3 block,const PrFlowRebaseShift& s,unsigned period,PrFlowRebasePlan& plan) {
    int shifts[3];
    if(!prRebaseBlocks(s.x,block.x,period,shifts[0]) || !prRebaseBlocks(s.y,block.y,period,shifts[1])
        || !prRebaseBlocks(s.z,block.z,period,shifts[2]))return false;
    int* axes[3]={&p.x,&p.y,&p.z};
    for(unsigned i=0;i<3;i++) {
        const int64_t next=int64_t(*axes[i])-shifts[i];
        // Sparse shaders shift coordinates by up to five cell bits. Reject
        // before that signed arithmetic can overflow; this is a local domain.
        if(next<-(int64_t(1)<<25) || next>=(int64_t(1)<<25))return false;
        plan.assign(*axes[i],int(next));
    }
    return true;
}
extern "C" bool prFlowSparseRebase(NvFlowContext*,NvFlowSparse*,const PrFlowRebaseShift*,PrFlowRebasePlan&);
extern "C" bool prFlowSparseRebasePeriod(NvFlowSparse*,double*);
extern "C" bool prFlowSummaryRebase(NvFlowContext*,NvFlowOp*,const PrFlowRebaseShift*,PrFlowRebasePlan&);
extern "C" bool prFlowSphereRebase(NvFlowOp*,NvFlowOp*,const PrFlowRebaseShift*,PrFlowRebasePlan&);
extern "C" bool prFlowBoxRebase(NvFlowOp*,const PrFlowRebaseShift*,PrFlowRebasePlan&);
extern "C" bool prFlowMeshRebase(NvFlowOp*,const PrFlowRebaseShift*,PrFlowRebasePlan&);
extern "C" bool prFlowGridRebase(NvFlowContext*,NvFlowGrid*,NvFlowOp*(*)(NvFlowOp*),const PrFlowRebaseShift*,PrFlowRebasePlan&);
extern "C" bool prFlowGridRebasePeriod(NvFlowGrid*,double*);
