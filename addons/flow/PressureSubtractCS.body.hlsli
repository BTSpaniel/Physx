// SPDX-License-Identifier: MIT
    int3 readIdx=NvFlowSingleVirtualToReal(gTable,gParams.table,blockIdx,threadIdx).xyz;
    uint layer;float3 world=prWorld(gTable,gParams.table,blockIdx,float3(threadIdx)+.5f,layer);
    float3 size=prCellSize(gTable,gParams.table,blockIdx);
    float centre=pressureIn[readIdx].x;float4 velocity=velocityIn[readIdx];
    for(uint axis=0u;axis<3u;axis++){
        int3 offset=int3(0,0,0);offset[axis]=1;
        float positive=prFace(layer,world,float3(offset)*size).open;
        float negative=prFace(layer,world,-float3(offset)*size).open;
        float forward=positive*(pressureIn[readIdx+offset].x-centre);
        float backward=negative*(centre-pressureIn[readIdx-offset].x);
        velocity[axis]-=.5f*(forward+backward);
    }
    velocity=prVelocityBoundary(gTable,gParams.table,blockIdx,threadIdx,velocity);
    NvFlowLocalWrite4f(velocityOut,gTable,gParams.table,blockIdx,threadIdx,velocity);
