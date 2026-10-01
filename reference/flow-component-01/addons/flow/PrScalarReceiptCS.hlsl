// SPDX-License-Identifier: MIT
#include "PrScalarCommon.hlsli"
[[vk::binding(0,0)]] ConstantBuffer<PrScalarParams> scalarParams;
[[vk::binding(1,0)]] StructuredBuffer<PrScalarRecord> scalarSources;
[[vk::binding(2,0)]] StructuredBuffer<PrScalarSample> scalarSamples;
[[vk::binding(3,0)]] StructuredBuffer<uint4> scalarAdmission;
[[vk::binding(4,0)]] StructuredBuffer<uint> scalarCounts;
[[vk::binding(5,0)]] StructuredBuffer<PrScalarRatio> scalarRatios;
[[vk::binding(6,0)]] RWStructuredBuffer<PrScalarReceipt> scalarReceipts;
[numthreads(64,1,1)]
void main(uint3 tid:SV_DispatchThreadID) {
    uint source=tid.x;if(source>=scalarParams.counts.x)return;
    PrScalarRecord s=scalarSources[source];uint count=scalarCounts[source];
    PrScalarReceipt receipt;receipt.meta=uint4(s.meta.x,count,64u,0u);
    receipt.requested=s.meta.z!=0u?s.totalRates*scalarParams.time.x:float4(0,0,0,0);
    float4 ratioSum=float4(0,0,0,0);
    for(uint i=0u;i<64u;i++){
        uint sample=source*64u+i;if(scalarAdmission[sample].w==0u)continue;
        PrScalarRatio ratio=scalarRatios[scalarSamples[sample].meta.y];
        ratioSum+=float4(receipt.requested.x<0.f?ratio.negative.x:ratio.positive.x,ratio.positive.yzw);
    }
    receipt.applied=count>0u?receipt.requested*saturate(ratioSum/float(count)):float4(0,0,0,0);
    receipt.deferred=receipt.requested-receipt.applied;scalarReceipts[source]=receipt;
}
