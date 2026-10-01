// SPDX-License-Identifier: MIT
    int3 readIdx=NvFlowSingleVirtualToReal(gTable,gParams.table,blockIdx,threadIdx).xyz;
    uint openFaces=prPressureFaces[blockIdx*gParams.table.threadsPerBlock+dispatchThreadID.x];
    float sum=0.f,diagonal=0.f;float divergence=pressureIn[readIdx].y;
    if(openFaces!=0u){
        for(uint axis=0u;axis<3u;axis++)for(uint side=0u;side<2u;side++){
            int3 offset=int3(0,0,0);offset[axis]=side==0u?-1:1;
            float open=float((openFaces>>(axis*2u+side))&1u);
            sum+=open*pressureIn[readIdx+offset].x;diagonal+=open;
        }
    }
    float pressure=diagonal>0.f?(sum-gParams.dx2*divergence)/diagonal:0.f;
    NvFlowLocalWrite2f(pressureOut,gTable,gParams.table,blockIdx,threadIdx,float2(pressure,divergence));
