// SPDX-License-Identifier: MIT
// Shared ABI1 analytic geometry. The CPU builder and presentation query export
// use the same threaded BVH and packed rows. Bounds cull; they never collide.
#ifndef PR_SOLID_BOUNDARY
#define PR_SOLID_BOUNDARY
[[vk::binding(30,0)]] StructuredBuffer<uint4> prBoundary;
struct PrSolidControl { uint mode; uint pad0; uint pad1; uint pad2; };
[[vk::binding(31,0)]] ConstantBuffer<PrSolidControl> prBoundaryControl;
static const uint PR_INVALID = 0xffffffffu;
float3 prRotate(float4 q,float3 v) { float3 t=2.f*cross(q.xyz,v);return v+q.w*t+cross(q.xyz,t); }
float3 prInverseRotate(float4 q,float3 v) { return prRotate(float4(-q.xyz,q.w),v); }
uint prShapeBase(uint shape) { return prBoundary[0].z+shape*10u; }
float3 prSurfaceVelocity(uint shape,float3 point) {
    uint b=prShapeBase(shape);return asfloat(prBoundary[b+4u]).xyz+cross(asfloat(prBoundary[b+5u]).xyz,point-asfloat(prBoundary[b+1u]).xyz);
}
bool prInsideShape(uint shape,float3 world) {
    uint b=prShapeBase(shape);uint4 meta=prBoundary[b];float4 centre=asfloat(prBoundary[b+1u]);
    float3 p=prInverseRotate(asfloat(prBoundary[b+2u]),world-centre.xyz);
    if(meta.y==0u)return dot(p,p)<centre.w*centre.w;
    if(meta.y==1u)return all(abs(p)<asfloat(prBoundary[b+3u]).xyz);
    if(meta.y==2u)return p.x<0.f;
    uint offset=prBoundary[0].w+prBoundary[b+8u].x;
    for(uint i=0u;i<meta.w;i++){float4 plane=asfloat(prBoundary[offset+i]);if(dot(plane.xyz,p)+plane.w>=0.f)return false;}
    return true;
}
bool prClip(float3 n,float w,float3 a,float3 d,inout float lo,inout float hi,inout float3 normal) {
    float q=dot(n,a)+w,r=dot(n,d);
    if(abs(r)<1.e-30f)return q<0.f;
    float t=-q/r;if(r<0.f){if(t>=lo){lo=t;normal=n;}}else hi=min(hi,t);
    return lo<=hi;
}
float prIntersectShape(uint shape,float3 start,float3 end,out float3 normal) {
    uint b=prShapeBase(shape);uint4 meta=prBoundary[b];float4 centre=asfloat(prBoundary[b+1u]);float4 rotation=asfloat(prBoundary[b+2u]);
    float3 a=prInverseRotate(rotation,start-centre.xyz),p=prInverseRotate(rotation,end-centre.xyz),d=p-a;
    float lo=0.f,hi=1.f;normal=float3(0,0,0);
    if(meta.y==0u){
        float aa=dot(d,d),bb=dot(a,d),cc=dot(a,a)-centre.w*centre.w;
        if(cc<0.f){normal=prRotate(rotation,normalize(a+float3(1.e-20f,0,0)));return 0.f;}
        if(aa<1.e-30f)return 1.f;float disc=bb*bb-aa*cc;if(disc<=0.f)return 1.f;
        lo=(-bb-sqrt(disc))/aa;hi=(-bb+sqrt(disc))/aa;
        if(hi>0.f && lo<1.f){normal=prRotate(rotation,normalize(a+max(0.f,lo)*d));return max(0.f,lo);}return 1.f;
    }
    if(meta.y==1u){float3 halfSize=asfloat(prBoundary[b+3u]).xyz;
        for(uint axis=0u;axis<3u;axis++){float3 n=float3(0,0,0);n[axis]=1.f;if(!prClip(n,-halfSize[axis],a,d,lo,hi,normal))return 1.f;n[axis]=-1.f;if(!prClip(n,-halfSize[axis],a,d,lo,hi,normal))return 1.f;}
    }else if(meta.y==2u){if(!prClip(float3(1,0,0),0.f,a,d,lo,hi,normal))return 1.f;}
    else {uint offset=prBoundary[0].w+prBoundary[b+8u].x;
        for(uint i=0u;i<meta.w;i++){float4 plane=asfloat(prBoundary[offset+i]);if(!prClip(plane.xyz,plane.w,a,d,lo,hi,normal))return 1.f;}
    }
    normal=prRotate(rotation,normal);
    return hi>0.f && lo<1.f && hi>lo?max(0.f,lo):1.f;
}
bool prBoxOverlap(float3 a,float3 b,float3 lo,float3 hi){return all(min(a,b)<=hi)&&all(max(a,b)>=lo);}
uint prInside(uint layer,float3 point) {
    uint count=prBoundary[0].x;for(uint index=0u;index<count;){uint n=prBoundary[0].y+index*2u;uint4 lo=prBoundary[n],hi=prBoundary[n+1u];
        if(!prBoxOverlap(point,point,asfloat(lo).xyz,asfloat(hi).xyz)){index=hi.w;continue;}
        if(lo.w!=PR_INVALID && prBoundary[prShapeBase(lo.w)].x==layer && prInsideShape(lo.w,point))return lo.w;++index;
    }return PR_INVALID;
}
float prTrace(uint layer,float3 a,float3 b,out uint shape,out float3 normal) {
    float hit=1.f;shape=PR_INVALID;normal=float3(0,0,0);uint count=prBoundary[0].x;
    for(uint index=0u;index<count;){uint n=prBoundary[0].y+index*2u;uint4 lo=prBoundary[n],hi=prBoundary[n+1u];
        if(!prBoxOverlap(a,b,asfloat(lo).xyz,asfloat(hi).xyz)){index=hi.w;continue;}
        if(lo.w!=PR_INVALID && prBoundary[prShapeBase(lo.w)].x==layer){float3 candidateNormal;float t=prIntersectShape(lo.w,a,b,candidateNormal);if(t<hit){hit=t;shape=lo.w;normal=candidateNormal;}}
        ++index;
    }return hit;
}
float3 prPreviousToCurrent(uint shape,float3 world){
    uint b=prShapeBase(shape);
    float3 local=prInverseRotate(asfloat(prBoundary[b+7u]),world-asfloat(prBoundary[b+6u]).xyz);
    return asfloat(prBoundary[b+1u]).xyz+prRotate(asfloat(prBoundary[b+2u]),local);
}
float prDistanceLowerBound(uint shape,float3 local){
    uint b=prShapeBase(shape);uint4 meta=prBoundary[b];
    if(meta.y==0u)return length(local)-asfloat(prBoundary[b+1u]).w;
    if(meta.y==1u){float3 d=abs(local)-asfloat(prBoundary[b+3u]).xyz;return max(d.x,max(d.y,d.z));}
    if(meta.y==2u)return local.x;
    float distance=-3.402823e38f;uint offset=prBoundary[0].w+prBoundary[b+8u].x;
    for(uint i=0u;i<meta.w;i++){float4 p=asfloat(prBoundary[offset+i]);distance=max(distance,dot(p.xyz,local)+p.w);}
    return distance;
}
float prIntersectMotion(uint shape,float3 start,float3 end){
    uint b=prShapeBase(shape);float4 q0=asfloat(prBoundary[b+2u]),q1=asfloat(prBoundary[b+7u]);
    float3 p0=start-asfloat(prBoundary[b+1u]).xyz,p1=end-asfloat(prBoundary[b+6u]).xyz;
    float cosine=dot(q0,q1);if(cosine<0.f){q1=-q1;cosine=-cosine;}
    float3 ignored;
    if(all(q0==q1))return prIntersectShape(shape,start,prPreviousToCurrent(shape,end),ignored);
    // Normalized quaternion lerp has angular-speed bound4*tan(theta/4).
    // The signed supporting-plane distance is a conservative distance bound.
    float difference=length(q1-q0);
    float angularBound=4.f*difference/sqrt(max(4.f-difference*difference,1.e-20f));
    float speed=length(p1-p0)+angularBound*max(length(p0),length(p1));
    float fraction=0.f;
    for(uint iteration=0u;iteration<64u;iteration++){
        float4 q=normalize(lerp(q0,q1,fraction));float3 point=prInverseRotate(q,lerp(p0,p1,fraction));
        float distance=prDistanceLowerBound(shape,point);
        if(distance<=1.e-7f)return fraction;
        if(speed<=1.e-20f)return 1.f;
        float next=fraction+distance/speed;
        if(next>=1.f)return 1.f;
        if(next<=fraction)return fraction;
        fraction=next;
    }
    // Tangential/ill-conditioned paths fail closed instead of tunnelling.
    return fraction;
}
bool prMovingNear(uint layer,float3 start,float3 end){
    for(uint index=0u;index<prBoundary[0].x;){uint n=prBoundary[0].y+index*2u;uint4 lo=prBoundary[n],hi=prBoundary[n+1u];
        if(!prBoxOverlap(start,end,asfloat(lo).xyz,asfloat(hi).xyz)){index=hi.w;continue;}
        if(lo.w!=PR_INVALID){uint b=prShapeBase(lo.w);if(prBoundary[b].x==layer){
            float4 q0=asfloat(prBoundary[b+2u]),q1=asfloat(prBoundary[b+7u]);
            if(any(asfloat(prBoundary[b+1u]).xyz!=asfloat(prBoundary[b+6u]).xyz) || (!all(q0==q1)&&!all(q0==-q1)))return true;
        }}++index;
    }return false;
}
float prTraceMotion(uint layer,float3 start,float3 end){
    float hit=1.f;uint count=prBoundary[0].x;
    for(uint index=0u;index<count;){uint n=prBoundary[0].y+index*2u;uint4 lo=prBoundary[n],hi=prBoundary[n+1u];
        if(!prBoxOverlap(start,end,asfloat(lo).xyz,asfloat(hi).xyz)){index=hi.w;continue;}
        if(lo.w!=PR_INVALID && prBoundary[prShapeBase(lo.w)].x==layer)hit=min(hit,prIntersectMotion(lo.w,start,end));
        ++index;
    }return hit;
}
bool prInsidePrevious(uint layer,float3 point){
    uint count=prBoundary[0].x;
    for(uint index=0u;index<count;){uint n=prBoundary[0].y+index*2u;uint4 lo=prBoundary[n],hi=prBoundary[n+1u];
        if(!prBoxOverlap(point,point,asfloat(lo).xyz,asfloat(hi).xyz)){index=hi.w;continue;}
        if(lo.w!=PR_INVALID && prBoundary[prShapeBase(lo.w)].x==layer && prInsideShape(lo.w,prPreviousToCurrent(lo.w,point)))return true;
        ++index;
    }return false;
}
// One conservative query covers the trajectory and every interpolation donor.
// A negative result skips narrow queries, never changes the physical boundary.
bool prStencilMayTouch(uint layer,float3 lower,float3 upper){
    for(uint index=0u;index<prBoundary[0].x;){uint n=prBoundary[0].y+index*2u;uint4 lo=prBoundary[n],hi=prBoundary[n+1u];
        if(!prBoxOverlap(lower,upper,asfloat(lo).xyz,asfloat(hi).xyz)){index=hi.w;continue;}
        if(lo.w!=PR_INVALID){uint b=prShapeBase(lo.w);uint4 meta=prBoundary[b];if(meta.x==layer){
            if(meta.y!=2u)return true;
            float3 centre=asfloat(prBoundary[b+1u]).xyz;float4 q0=asfloat(prBoundary[b+2u]),q1=asfloat(prBoundary[b+7u]);
            // Retain the original moving-near/MacCormack decision for every
            // moving plane, whose swept broadphase is necessarily unbounded.
            if(any(centre!=asfloat(prBoundary[b+6u]).xyz) || (!all(q0==q1)&&!all(q0==-q1)))return true;
            float3 normal=prRotate(q0,float3(1,0,0));float3 nearest=float3(normal.x>=0.f?lower.x:upper.x,normal.y>=0.f?lower.y:upper.y,normal.z>=0.f?lower.z:upper.z);
            float rounding=0.00000762939453125f*(1.f+dot(abs(normal),abs(nearest)+abs(centre)));
            if(dot(normal,nearest-centre)<=rounding)return true;
        }}++index;
    }return false;
}
float3 prWorld(StructuredBuffer<uint> table,NvFlowSparseLevelParams params,uint block,float3 cell,out uint layer) {
    int4 location;NvFlowBlockIdxToLocation(table,params,block,location);
    uint layerIndex=NvFlowGetLayerParamIdx(table,params,block);uint4 record=prBoundary[prBoundary[1].x+layerIndex];layer=record.w;
    return (float3(location.xyz)+cell/float3(params.blockDimLessOne+1u))*asfloat(record).xyz;
}
float3 prCellSize(StructuredBuffer<uint> table,NvFlowSparseLevelParams params,uint block) {
    uint layerIndex=NvFlowGetLayerParamIdx(table,params,block);return asfloat(prBoundary[prBoundary[1].x+layerIndex]).xyz/float3(params.blockDimLessOne+1u);
}
bool prStencilVisible(StructuredBuffer<uint> table,NvFlowSparseLevelParams params,uint block,float3 query,float3 reference) {
    if(prBoundary[0].x==0u)return true;
    uint layer,shape;float3 normal;float3 world=prWorld(table,params,block,query,layer);
    int3 base=int3(floor(query-.5f));
    float3 first=prWorld(table,params,block,float3(base)+.5f,layer),last=prWorld(table,params,block,float3(base)+1.5f,layer);
    if(!prStencilMayTouch(layer,min(min(first,last),min(reference,world)),max(max(first,last),max(reference,world))))return true;
    if(prTraceMotion(layer,reference,world)<1.f || prMovingNear(layer,reference,world))return false;
    for(uint corner=0u;corner<8u;corner++){
        int3 side=int3(corner&1u,(corner>>1u)&1u,(corner>>2u)&1u);
        float3 donor=prWorld(table,params,block,float3(base+side)+.5f,layer);
        if(prInsidePrevious(layer,donor) || prTraceMotion(layer,reference,donor)<1.f || prMovingNear(layer,reference,donor))return false;
    }
    return true;
}
struct PrFace { float open; float3 wall; };
PrFace prFace(uint layer,float3 world,float3 displacement) {
    uint shape;float3 normal;float t=prTrace(layer,world,world+displacement,shape,normal);
    PrFace result;result.open=t<1.f?0.f:1.f;result.wall=float3(0,0,0);
    if(t<1.f)result.wall=prSurfaceVelocity(shape,world+t*displacement);
    return result;
}
float4 prScalarBoundary(StructuredBuffer<uint> table,NvFlowSparseLevelParams params,uint block,int3 cell,float4 value) {
    if(prBoundary[0].x==0u)return value;uint layer;float3 world=prWorld(table,params,block,float3(cell)+.5f,layer);
    return prInside(layer,world)==PR_INVALID?value:float4(0,0,0,0);
}
float4 prVelocityBoundary(StructuredBuffer<uint> table,NvFlowSparseLevelParams params,uint block,int3 cell,float4 value) {
    if(prBoundary[0].x==0u)return value;uint layer;float3 world=prWorld(table,params,block,float3(cell)+.5f,layer);uint shape=prInside(layer,world);
    if(shape!=PR_INVALID)return float4(prSurfaceVelocity(shape,world),0.f);
    float3 cellSize=prCellSize(table,params,block);
    // Face constraints use exact crossed surfaces; they do not inflate solids.
    for(uint axis=0u;axis<3u;axis++)for(uint side=0u;side<2u;side++){
        float3 delta=float3(0,0,0);delta[axis]=(side==0u?-1.f:1.f)*cellSize[axis];float3 normal;
        float t=prTrace(layer,world,world+delta,shape,normal);
        // Explicit paired mode applies ONE finite-mass terminal impulse.
        // Predictor/corrector states are never counted as physical transfers.
        if(t<1.f && (prBoundary[prShapeBase(shape)].z&4u)==0u){float3 wall=prSurfaceVelocity(shape,world+t*delta);float relative=dot(value.xyz-wall,normal);if(relative<0.f)value.xyz-=relative*normal;}
    }return value;
}
float4 prValueBoundary(StructuredBuffer<uint> table,NvFlowSparseLevelParams params,uint block,int3 cell,float4 value) {
    return prBoundaryControl.mode==0u?prScalarBoundary(table,params,block,cell,value):prVelocityBoundary(table,params,block,cell,value);
}
// Both the trajectory and every interpolation donor must remain on the same
// fluid side. Clipping endpoints alone would still leak through thin sheets.
float4 prReadLinear4f(Texture3D<float4> field,SamplerState samplerIn,StructuredBuffer<uint> table,NvFlowSparseLevelParams params,
    uint block,float3 query,float3 reference,bool velocity,bool temporal=true) {
    if(prBoundary[0].x==0u)return NvFlowLocalReadLinearSafe4f(field,samplerIn,table,params,block,query);
    uint layer;float3 world=prWorld(table,params,block,query,layer);float3 cellSize=prCellSize(table,params,block);uint shape;float3 normal;
    int4 location;NvFlowBlockIdxToLocation(table,params,block,location);float3 local=world/cellSize-float3(location.xyz<<params.blockDimBits);
    int3 base=int3(floor(local-.5f));
    float3 first=prWorld(table,params,block,float3(base)+.5f,layer),last=prWorld(table,params,block,float3(base)+1.5f,layer);
    bool mayTouch=prStencilMayTouch(layer,min(min(first,last),min(reference,world)),max(max(first,last),max(reference,world)));
    if(mayTouch){
        float t=temporal?prTraceMotion(layer,reference,world):prTrace(layer,reference,world,shape,normal);
        if(t<1.f){float distance=length(world-reference);float safe=max(0.f,t-1.e-5f*min(cellSize.x,min(cellSize.y,cellSize.z))/max(distance,1.e-20f));world=lerp(reference,world,safe);}
        uint occupied=prInside(layer,world);
        if(temporal && prInsidePrevious(layer,world))return float4(0,0,0,0);
        if(!temporal && occupied!=PR_INVALID)return velocity?float4(prSurfaceVelocity(occupied,world),0.f):float4(0,0,0,0);
        local=world/cellSize-float3(location.xyz<<params.blockDimBits);base=int3(floor(local-.5f));
    }
    float3 f=frac(local-.5f);float4 value=float4(0,0,0,0);float weight=0.f;
    for(uint corner=0u;corner<8u;corner++){int3 side=int3(corner&1u,(corner>>1u)&1u,(corner>>2u)&1u);int3 donor=base+side;
        if(mayTouch){float3 donorWorld=prWorld(table,params,block,float3(donor)+.5f,layer);float3 ignored;
            bool blocked=temporal?(prInsidePrevious(layer,donorWorld)||prTraceMotion(layer,reference,donorWorld)<1.f)
                :(prInside(layer,donorWorld)!=PR_INVALID||prTrace(layer,reference,donorWorld,shape,ignored)<1.f);
            if(blocked)continue;
        }
        float3 factors=lerp(1.f-f,f,float3(side));float w=factors.x*factors.y*factors.z;
        int4 virtualCell=int4((location.xyz<<params.blockDimBits)+donor,location.w);int4 realCell=NvFlowGlobalVirtualToReal(table,params,virtualCell);
        if(realCell.w!=0)value+=w*field[realCell.xyz];weight+=w;
    }
    return weight>1.e-8f?value/weight:float4(0,0,0,0);
}
#endif
