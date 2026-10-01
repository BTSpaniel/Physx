// SPDX-License-Identifier: MIT
#ifndef PR_SCALAR_COMMON
#define PR_SCALAR_COMMON
#include "NvFlowShader.hlsli"
struct PrScalarRecord { uint4 meta; float4 position; float4 quaternion; float4 halfSize; float4 totalRates; };
struct PrScalarSample { float4 point; int4 location; float4 centreVolume; uint4 meta; };
struct PrScalarCell { int4 location; float4 centreVolume; uint4 range; };
struct PrScalarParams { NvFlowSparseLevelParams level; uint4 counts; float4 time; };
struct PrScalarRatio { float4 positive; float4 negative; };
struct PrScalarReceipt { uint4 meta; float4 requested; float4 applied; float4 deferred; };
#endif
