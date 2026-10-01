// SPDX-License-Identifier: MIT
#include "PrScalarCommon.hlsli"
[[vk::binding(0,0)]] ConstantBuffer<PrScalarParams> scalarParams;
[[vk::binding(1,0)]] StructuredBuffer<uint4> scalarAdmission;
[[vk::binding(2,0)]] RWStructuredBuffer<uint> scalarCounts;
[numthreads(64,1,1)]
void main(uint3 tid:SV_DispatchThreadID) {
    uint source=tid.x;if(source>=scalarParams.counts.x)return;
    uint count=0u;for(uint i=0u;i<64u;i++)count+=scalarAdmission[source*64u+i].w;
    scalarCounts[source]=count;
}
