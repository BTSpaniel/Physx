// SPDX-License-Identifier: MIT
#include "PrScalarCommon.hlsli"
[[vk::binding(0,0)]] ConstantBuffer<PrScalarParams> scalarParams;
[[vk::binding(1,0)]] StructuredBuffer<PrScalarRecord> scalarSources;
[[vk::binding(2,0)]] StructuredBuffer<PrScalarCell> scalarCells;
[[vk::binding(3,0)]] StructuredBuffer<uint> scalarMembers;
[[vk::binding(4,0)]] StructuredBuffer<uint4> scalarAdmission;
[[vk::binding(5,0)]] StructuredBuffer<uint> scalarCounts;
[[vk::binding(6,0)]] Texture3D<float4> scalarDensityIn;
[[vk::binding(7,0)]] RWTexture3D<float4> scalarDensityOut;
[[vk::binding(8,0)]] RWStructuredBuffer<PrScalarRatio> scalarRatios;

// Round toward the previous nonnegative texel, so a representability limit
// defers a finite source rather than manufacturing an extra ULP of its budget.
float prDeposit(float previous,float integrated,float volume,out float applied) {
    applied=0.f;if(integrated==0.f)return previous;
    float delta=integrated/volume;
    float next=previous+delta;
    if(!isfinite(next))return previous;
    float z=next-previous;
    float error=(previous-(next-z))+(delta-z);
    if(integrated>0.f && error<0.f && next>previous)next=asfloat(asuint(next)-1u);
    if(integrated<0.f && error>0.f && next<previous)next=asfloat(asuint(next)+1u);
    next=max(0.f,next);
    applied=(next-previous)*volume;
    if((integrated>0.f && applied>integrated)||(integrated<0.f && applied<integrated)){
        next=asfloat(asuint(next)+(integrated>0.f?0xffffffffu:1u));
        applied=(next-previous)*volume;
    }
    if((integrated>0.f && applied<0.f)||(integrated<0.f && applied>0.f)){applied=0.f;return previous;}
    return next;
}
[numthreads(64,1,1)]
void main(uint3 tid:SV_DispatchThreadID) {
    uint index=tid.x;if(index>=scalarParams.counts.z)return;
    PrScalarCell cell=scalarCells[index];float4 positive=float4(0,0,0,0);float negative=0.f;
    uint4 address=uint4(0,0,0,0);
    for(uint i=0u;i<cell.range.y;i++){
        uint sample=scalarMembers[cell.range.x+i];uint4 admitted=scalarAdmission[sample];
        if(admitted.w==0u)continue;
        uint source=sample/64u;float4 amount=scalarSources[source].totalRates*scalarParams.time.x/float(scalarCounts[source]);
        positive+=max(amount,0.f);negative+=max(-amount.x,0.f);address=admitted;
    }
    PrScalarRatio ratios;ratios.positive=float4(0,0,0,0);ratios.negative=float4(0,0,0,0);
    if(address.w!=0u){
        float4 previous=scalarDensityIn.Load(int4(address.xyz,0));float4 result=previous,applied=float4(0,0,0,0);
        for(uint axis=0u;axis<4u;axis++){
            result[axis]=prDeposit(previous[axis],positive[axis],cell.centreVolume.w,applied[axis]);
            ratios.positive[axis]=positive[axis]>0.f?saturate(applied[axis]/positive[axis]):0.f;
        }
        float available=max(0.f,result.x)*cell.centreVolume.w;
        float heatRemoved=0.f;result.x=prDeposit(result.x,-min(negative,available),cell.centreVolume.w,heatRemoved);
        ratios.negative.x=negative>0.f?saturate(-heatRemoved/negative):0.f;
        scalarDensityOut[address.xyz]=result;
    }
    scalarRatios[index]=ratios;
}
