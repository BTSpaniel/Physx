// SPDX-License-Identifier: MIT
#include "PrMomentumCommon.hlsli"
[[vk::binding(0,0)]] ConstantBuffer<PrMomentumParams> params;
[[vk::binding(1,0)]] StructuredBuffer<uint> table;
[[vk::binding(2,0)]] StructuredBuffer<PrMomentumUpdate> updates;
[[vk::binding(3,0)]] RWTexture3D<float4> velocity;
[numthreads(64,1,1)]
void main(uint3 tid:SV_DispatchThreadID){
    if(tid.x>=params.counts.x)return;PrMomentumUpdate value=updates[tid.x];
    // Reuse the native halo scatter, so subsequent trilinear reads observe the
    // very same admitted cell update at block faces, edges and corners.
    NvFlowLocalWrite4f(velocity,table,params.table,value.cell.x,int3(value.cell.yzw),value.velocity);
}
