// SPDX-License-Identifier: MIT
#pragma once
struct PrMomentumContact { unsigned cell[4];float worldVolume[4],velocity[4],normal[4];unsigned owner[4]; };
struct PrMomentumUpdate { unsigned cell[4];float velocity[4]; };
struct PrMomentumParams { NvFlowSparseLevelParams table;unsigned counts[4]; };
static_assert(sizeof(PrMomentumContact)==80 && sizeof(PrMomentumUpdate)==32 && sizeof(PrMomentumParams)==176,"Momentum packet layout");
struct PrMomentumState {
    NvFlowBuffer *params=nullptr,*contacts=nullptr,*counter=nullptr,*readback=nullptr,*updates=nullptr;
    unsigned pipeline[2]={},capacity=0,sequence=0,solidVersion=0,textureId=0;
    NvFlowUint64 frame=0,geometryRevision=0;
    bool prepared=false;
};
