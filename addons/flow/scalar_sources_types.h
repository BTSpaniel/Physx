// SPDX-License-Identifier: MIT
// Finite additive scalar source ABI. Quantities are normalized channel*m^3/s;
// receipts are integrated channel*m^3, independent of sparse cell dimensions.
#pragma once
#include <array>
#include <vector>
struct PrScalarRecord {
    unsigned id,layer,enabled,pad;
    float position[4],quaternion[4],halfSize[4],totalRates[4];
};
struct PrScalarReceipt {
    unsigned id,admittedSamples,sampleCount,pad;
    float requested[4],applied[4],deferred[4];
};
static_assert(sizeof(PrScalarRecord)==80,"Scalar source ABI1 record");
static_assert(sizeof(PrScalarReceipt)==64,"Scalar source ABI1 receipt");
struct PrScalarState {
    std::vector<PrScalarRecord> records;
    NvFlowBuffer* params=nullptr;
    NvFlowBuffer* sources=nullptr;
    NvFlowBuffer* samples=nullptr;
    NvFlowBuffer* cells=nullptr;
    NvFlowBuffer* members=nullptr;
    NvFlowBuffer* admission=nullptr;
    NvFlowBuffer* counts=nullptr;
    NvFlowBuffer* ratios=nullptr;
    NvFlowBuffer* receipt=nullptr;
    NvFlowBuffer* readback=nullptr;
    NvFlowBuffer* emptyBoundary=nullptr;
    NvFlowBuffer* emptyControl=nullptr;
    unsigned pipelines[4]={};
    unsigned publishedCount=0;
    NvFlowUint64 appliedFrame=0,publishedFrame=0;
    float publishedDt=0;
};
