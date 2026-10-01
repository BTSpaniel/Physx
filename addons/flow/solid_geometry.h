// SPDX-License-Identifier: MIT
#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>

namespace prflow {
using V3 = std::array<float, 3>;
using V4 = std::array<float, 4>;
struct SolidRecord {
    uint32_t layer, type, flags, planeCount;
    V4 positionRadius, rotation, halfSize, linearVelocity, angularVelocity;
    V4 previousPosition, previousRotation;
    uint32_t planeOffset, id, reserved0, reserved1;
    V4 boundsCenter;
};
static_assert(sizeof(SolidRecord) == 160, "Solid boundary ABI1 record");
struct Bounds { V3 lower, upper; };
struct Node { Bounds bounds; uint32_t shape, escape; };
constexpr uint32_t innerNode = 0xffffffffu;
inline V3 xyz(const V4& a) { return {a[0], a[1], a[2]}; }
inline V3 add(V3 a, V3 b) { return {a[0]+b[0], a[1]+b[1], a[2]+b[2]}; }
inline V3 sub(V3 a, V3 b) { return {a[0]-b[0], a[1]-b[1], a[2]-b[2]}; }
inline V3 mul(V3 a, float b) { return {a[0]*b, a[1]*b, a[2]*b}; }
inline float dot(V3 a, V3 b) { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
inline V3 cross(V3 a, V3 b) { return {a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]}; }
inline V3 rotate(V4 q, V3 v) { const auto t=mul(cross(xyz(q),v),2.f); return add(v,add(mul(t,q[3]),cross(xyz(q),t))); }
inline V3 inverseRotate(V4 q,V3 v) { q[0]=-q[0];q[1]=-q[1];q[2]=-q[2];return rotate(q,v); }
inline bool finite(V4 a) { for(float v:a) if(!std::isfinite(v))return false;return true; }
inline bool unit(V4 q) { if(!finite(q))return false;float n=0;for(float v:q)n+=v*v;return std::abs(n-1.f)<1.e-3f; }
inline bool valid(const SolidRecord& s, const std::vector<V4>& planes) {
    if(s.layer>65535 || s.type>3 || s.flags>7 || s.reserved0 || s.reserved1 || !finite(s.positionRadius)
        || !unit(s.rotation) || !finite(s.halfSize) || !finite(s.linearVelocity) || !finite(s.angularVelocity)
        || !finite(s.previousPosition) || !unit(s.previousRotation) || !finite(s.boundsCenter)) return false;
    if(s.type==0 && s.positionRadius[3]<=0.f)return false;
    if((s.type==1 || s.type==3) && (s.halfSize[0]<=0.f || s.halfSize[1]<=0.f || s.halfSize[2]<=0.f))return false;
    if(s.type!=3)return s.planeCount==0 && s.planeOffset==0;
    if(s.planeCount<4 || s.planeCount>256 || s.planeOffset>planes.size() || s.planeCount>planes.size()-s.planeOffset)return false;
    for(uint32_t i=0;i<s.planeCount;i++) {
        const auto p=planes[s.planeOffset+i];if(!finite(p) || std::abs(dot(xyz(p),xyz(p))-1.f)>1.e-3f)return false;
    }
    return true;
}
inline Bounds bounds(const SolidRecord& s) {
    if(s.type==2) { const float f=std::numeric_limits<float>::max();return {{-f,-f,-f},{f,f,f}}; }
    V3 e={s.positionRadius[3],s.positionRadius[3],s.positionRadius[3]};
    V3 p=xyz(s.positionRadius);
    if(s.type!=0) {
        p=add(p,rotate(s.rotation,xyz(s.boundsCenter)));e={0,0,0};
        for(int axis=0;axis<3;axis++) { V3 v={0,0,0};v[axis]=s.halfSize[axis];v=rotate(s.rotation,v);for(int k=0;k<3;k++)e[k]+=std::abs(v[k]); }
    }
    Bounds result={sub(p,e),add(p,e)};
    bool sameRotation=s.rotation==s.previousRotation;
    if(!sameRotation){sameRotation=true;for(unsigned i=0;i<4;i++)sameRotation&=s.rotation[i]==-s.previousRotation[i];}
    if(!(s.flags&2u) && (xyz(s.positionRadius)!=xyz(s.previousPosition) || !sameRotation)){
        // Broadphase encloses the entire rotational sweep. Narrowphase uses
        // space-time poses, so this conservative bound never becomes geometry.
        const float radius=s.type==0?s.positionRadius[3]:std::sqrt(dot(xyz(s.halfSize),xyz(s.halfSize)))+std::sqrt(dot(xyz(s.boundsCenter),xyz(s.boundsCenter)));
        for(int i=0;i<3;i++){
            result.lower[i]=std::min(result.lower[i],std::min(s.positionRadius[i],s.previousPosition[i])-radius);
            result.upper[i]=std::max(result.upper[i],std::max(s.positionRadius[i],s.previousPosition[i])+radius);
        }
    }
    return result;
}
inline Bounds merge(Bounds a,Bounds b) { for(int i=0;i<3;i++){a.lower[i]=std::min(a.lower[i],b.lower[i]);a.upper[i]=std::max(a.upper[i],b.upper[i]);}return a; }
inline bool overlap(Bounds a,Bounds b) { for(int i=0;i<3;i++)if(a.lower[i]>b.upper[i] || a.upper[i]<b.lower[i])return false;return true; }
inline std::vector<Node> buildBvh(const std::vector<SolidRecord>& shapes) {
    std::vector<uint32_t> indices;for(uint32_t i=0;i<shapes.size();i++)if(shapes[i].flags&1)indices.push_back(i);
    std::vector<Node> nodes;nodes.reserve(indices.empty()?0:indices.size()*2-1);
    auto build=[&](auto&& self,size_t first,size_t end)->void {
        const auto at=uint32_t(nodes.size());Bounds b=bounds(shapes[indices[first]]);
        for(size_t i=first+1;i<end;i++)b=merge(b,bounds(shapes[indices[i]]));
        nodes.push_back({b,innerNode,0});
        if(end-first==1)nodes[at].shape=indices[first];
        else {
            int axis=0;for(int i=1;i<3;i++)if(double(b.upper[i])-b.lower[i]>double(b.upper[axis])-b.lower[axis])axis=i;
            const auto mid=first+(end-first)/2;
            std::nth_element(indices.begin()+first,indices.begin()+mid,indices.begin()+end,[&](uint32_t a,uint32_t c){
                const auto ba=bounds(shapes[a]),bc=bounds(shapes[c]);
                const double va=double(ba.lower[axis])+ba.upper[axis],vc=double(bc.lower[axis])+bc.upper[axis];
                return va==vc?a<c:va<vc;
            });
            self(self,first,mid);self(self,mid,end);
        }
        nodes[at].escape=uint32_t(nodes.size());
    };
    if(!indices.empty())build(build,0,indices.size());return nodes;
}
inline bool inside(const SolidRecord& s,const std::vector<V4>& planes,V3 world) {
    const auto p=inverseRotate(s.rotation,sub(world,xyz(s.positionRadius)));
    if(s.type==0)return dot(p,p)<s.positionRadius[3]*s.positionRadius[3];
    if(s.type==1)return std::abs(p[0])<s.halfSize[0] && std::abs(p[1])<s.halfSize[1] && std::abs(p[2])<s.halfSize[2];
    if(s.type==2)return p[0]<0.f;
    for(uint32_t i=0;i<s.planeCount;i++) { const auto plane=planes[s.planeOffset+i];if(dot(xyz(plane),p)+plane[3]>=0.f)return false; }
    return true;
}
// Returns first entry parameter, or1 when the open segment never enters solid.
// Analytic intervals catch a thin slab even when BOTH endpoints are outside.
inline float intersect(const SolidRecord& s,const std::vector<V4>& planes,V3 a,V3 b) {
    a=inverseRotate(s.rotation,sub(a,xyz(s.positionRadius)));b=inverseRotate(s.rotation,sub(b,xyz(s.positionRadius)));
    const V3 d=sub(b,a);float lo=0.f,hi=1.f;
    if(s.type==0) {
        const float aa=dot(d,d),bb=dot(a,d),cc=dot(a,a)-s.positionRadius[3]*s.positionRadius[3];
        if(cc<0.f)return 0.f;if(aa<1.e-30f)return 1.f;
        const float disc=bb*bb-aa*cc;if(disc<=0.f)return 1.f;
        lo=(-bb-std::sqrt(disc))/aa;hi=(-bb+std::sqrt(disc))/aa;
        return hi>0.f && lo<1.f?std::max(0.f,lo):1.f;
    }
    auto clip=[&](V3 n,float w)->bool {
        const float q=dot(n,a)+w,r=dot(n,d);
        if(std::abs(r)<1.e-30f)return q<0.f;
        const float t=-q/r;if(r<0.f)lo=std::max(lo,t);else hi=std::min(hi,t);
        return lo<=hi;
    };
    if(s.type==1) { for(int axis=0;axis<3;axis++){V3 n={0,0,0};n[axis]=1;if(!clip(n,-s.halfSize[axis]))return 1;n[axis]=-1;if(!clip(n,-s.halfSize[axis]))return 1;} }
    else if(s.type==2) { if(!clip({1,0,0},0))return 1; }
    else for(uint32_t i=0;i<s.planeCount;i++){const auto p=planes[s.planeOffset+i];if(!clip(xyz(p),p[3]))return 1;}
    return hi>0.f && lo<1.f && hi>lo?std::max(0.f,lo):1.f;
}
inline float trace(const std::vector<SolidRecord>& shapes,const std::vector<V4>& planes,const std::vector<Node>& nodes,uint32_t layer,V3 a,V3 b) {
    Bounds query;for(int i=0;i<3;i++){query.lower[i]=std::min(a[i],b[i]);query.upper[i]=std::max(a[i],b[i]);}
    float hit=1;for(uint32_t i=0;i<nodes.size();) {const auto& n=nodes[i];if(!overlap(n.bounds,query)){i=n.escape;continue;}
        if(n.shape!=innerNode && shapes[n.shape].layer==layer)hit=std::min(hit,intersect(shapes[n.shape],planes,a,b));++i;}
    return hit;
}
}
