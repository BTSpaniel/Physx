// SPDX-License-Identifier: MIT
    int3 readIdx=NvFlowSingleVirtualToReal(gTable,gParams.table,blockIdx,threadIdx).xyz;
    uint layer;float3 world=prWorld(gTable,gParams.table,blockIdx,float3(threadIdx)+.5f,layer);
    float3 size=prCellSize(gTable,gParams.table,blockIdx);
    float4 centre=velocityIn[readIdx];float divergence=-gParams.dextScale*centre.w;uint openFaces=0u;
    if(prInside(layer,world)==PR_INVALID){
        for(uint axis=0u;axis<3u;axis++)for(uint side=0u;side<2u;side++){
            int sign=side==0u?-1:1;int3 offset=int3(0,0,0);offset[axis]=sign;
            PrFace face=prFace(layer,world,float3(offset)*size);
            openFaces|=uint(face.open)<<(axis*2u+side);
            float fluid=.5f*(centre[axis]+velocityIn[readIdx+offset][axis]);
            divergence+=float(sign)*lerp(face.wall[axis],fluid,face.open);
        }
    }else divergence=0.f;
    prPressureFaces[blockIdx*gParams.table.threadsPerBlock+dispatchThreadID.x]=openFaces;
    NvFlowLocalWrite2f(pressureOut,gTable,gParams.table,blockIdx,threadIdx,float2(0.f,divergence));
