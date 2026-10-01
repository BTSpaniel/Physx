// SPDX-License-Identifier: MIT
#include "PrMomentumCommon.hlsli"
#include "PrSolidBoundary.hlsli"
[[vk::binding(0,0)]] ConstantBuffer<PrMomentumParams> params;
[[vk::binding(1,0)]] StructuredBuffer<uint> table;
[[vk::binding(2,0)]] Texture3D<float4> velocity;
[[vk::binding(3,0)]] RWStructuredBuffer<PrMomentumContact> contacts;
[[vk::binding(4,0)]] RWStructuredBuffer<Atomic<uint>> counts;
[numthreads(64,1,1)]
void main(uint3 tid:SV_DispatchThreadID){
    if(tid.x>=params.counts.x)return;
    uint block=tid.x/params.table.threadsPerBlock;
    int3 cell=NvFlowComputeThreadIdx(params.table,tid.x%params.table.threadsPerBlock);
    int4 address=NvFlowSingleVirtualToReal(table,params.table,block,cell);
    if(address.w==0)return;
    uint layer;float3 world=prWorld(table,params.table,block,float3(cell)+.5f,layer);
    if(prInside(layer,world)!=PR_INVALID)return;
    float3 size=prCellSize(table,params.table,block);
    for(uint axis=0u;axis<3u;axis++)for(uint side=0u;side<2u;side++){
        float3 delta=float3(0,0,0);delta[axis]=(side==0u?-1.f:1.f)*size[axis];
        uint shape;float3 normal;float t=prTrace(layer,world,world+delta,shape,normal);
        if(t>=1.f || (prBoundary[prShapeBase(shape)].z&4u)==0u)continue;
        uint index=counts[0].add(1u);if(index>=params.counts.y)continue;
        PrMomentumContact record;
        record.cell=uint4(block,uint3(cell));record.worldVolume=float4(world,size.x*size.y*size.z);
        record.velocity=velocity.Load(int4(address.xyz,0));record.normal=float4(normal,0);
        record.owner=uint4(prBoundary[prShapeBase(shape)+8u].y,layer,axis*2u+side,1u);
        contacts[index]=record;
    }
}
