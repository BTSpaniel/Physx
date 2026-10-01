// SPDX-License-Identifier: MIT
#include "NvFlowShader.hlsli"
struct PrMomentumParams { NvFlowSparseLevelParams table;uint4 counts; };
struct PrMomentumContact { uint4 cell;float4 worldVolume;float4 velocity;float4 normal;uint4 owner; };
struct PrMomentumUpdate { uint4 cell;float4 velocity; };
