// SPDX-License-Identifier: MIT
#include "PrScalarCommon.hlsli"
#include "PrSolidBoundary.hlsli"
[[vk::binding(0,0)]] ConstantBuffer<PrScalarParams> scalarParams;
[[vk::binding(1,0)]] StructuredBuffer<PrScalarSample> scalarSamples;
[[vk::binding(2,0)]] StructuredBuffer<uint> scalarTable;
[[vk::binding(3,0)]] RWStructuredBuffer<uint4> scalarAdmission;
[numthreads(64,1,1)]
void main(uint3 tid:SV_DispatchThreadID) {
    uint index=tid.x;if(index>=scalarParams.counts.y)return;
    PrScalarSample s=scalarSamples[index];uint4 result=uint4(0,0,0,0);
    if(s.meta.w!=0u){
        int4 real=NvFlowGlobalVirtualToReal(scalarTable,scalarParams.level,s.location);
        if(real.w!=0 && prInside(s.meta.z,s.point.xyz)==PR_INVALID && prInside(s.meta.z,s.centreVolume.xyz)==PR_INVALID){
            uint shape;float3 normal;
            if(prTrace(s.meta.z,s.point.xyz,s.centreVolume.xyz,shape,normal)>=1.f)result=uint4(uint3(real.xyz),1u);
        }
    }
    scalarAdmission[index]=result;
}
