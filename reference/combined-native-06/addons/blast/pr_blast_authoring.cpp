// SPDX-License-Identifier: MIT
// Validated ownership boundary for actual pinned NVIDIA convex Voronoi authoring.
#include <emscripten/emscripten.h>
#include "pr_blast_memory.h"
#include "NvBlastExtAuthoringFractureToolImpl.h"
#include "NvBlastExtAuthoringMeshImpl.h"
#include "NvBlastExtAuthoringBondGeneratorImpl.h"
#include "NvBlastGlobals.h"
#include "NvBlastVolumeIntegrals.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <vector>

namespace {
using namespace Nv::Blast;
using V3 = std::array<double, 3>;
V3 subtract(const NvcVec3& a, const NvcVec3& b) { return {double(a.x)-b.x,double(a.y)-b.y,double(a.z)-b.z}; }
V3 cross(const V3& a, const V3& b) { return {a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]}; }
double dot(const V3& a, const V3& b) { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
bool finite(const NvcVec3& v) { return std::isfinite(v.x) && std::isfinite(v.y) && std::isfinite(v.z); }
bool nativePositive(double v) { return std::isfinite(v) && std::isfinite(float(v)) && float(v) > 0; }

struct Plane { NvcVec3 point; V3 normal; };
struct TriangleQuery {
    const Triangle* triangles;
    uint32_t count;
    size_t faceCount() const { return count; }
    size_t vertexCount(size_t) const { return 3; }
    NvcVec3 vertex(size_t face,size_t index) const {
        const auto& triangle=triangles[face];
        return index==0 ? triangle.a.p : index==1 ? triangle.b.p : triangle.c.p;
    }
};
struct Interface {
    double area=0;
    V3 weightedCenter{}, areaNormal{};
};
struct Chunk {
    float centroidVolume[4];
    std::vector<float> vertices; // Local position, unit normal, UV; 8 floats.
    std::vector<int32_t> materials;
    std::map<int32_t,Interface> interfaces;
};
struct Result {
    std::vector<Chunk> chunks;
    std::vector<uint32_t> pairs;
    std::vector<float> bonds; // Area, original-space centroid, outward normal.
};
std::set<Result*> results;
int32_t lastError = 0;
Result* checked(uintptr_t handle) {
    auto* result = reinterpret_cast<Result*>(handle);
    return results.count(result) ? result : nullptr;
}
template<class T> struct Release { void operator()(T* value) const { if (value) value->release(); } };
struct BlastFree { template<class T> void operator()(T* value) const { NVBLAST_FREE(value); } };

// Weld exactly equal input coordinates across UV/normal seams. Convexity and
// two opposite directed uses per edge reject holes, overlaps and inward faces.
bool validateMesh(const NvcVec3* p, uint32_t count, const uint32_t* indices, uint32_t indexCount,
                  std::vector<Plane>& planes, double& extent, double& volume)
{
    std::map<std::array<float,3>, uint32_t> unique;
    std::vector<uint32_t> welded(count);
    NvcVec3 low = p[0], high = p[0];
    for (uint32_t i=0; i<count; ++i) {
        if (!finite(p[i])) return false;
        auto added = unique.emplace(std::array<float,3>{p[i].x,p[i].y,p[i].z}, unique.size());
        welded[i] = added.first->second;
        low.x=std::min(low.x,p[i].x); low.y=std::min(low.y,p[i].y); low.z=std::min(low.z,p[i].z);
        high.x=std::max(high.x,p[i].x); high.y=std::max(high.y,p[i].y); high.z=std::max(high.z,p[i].z);
    }
    if (unique.size()<4 || unique.size()>255) return false;
    extent = std::max({double(high.x)-low.x,double(high.y)-low.y,double(high.z)-low.z});
    if (!nativePositive(extent) || !nativePositive(extent*extent) || !nativePositive(extent*extent*extent)) return false;
    std::map<std::pair<uint32_t,uint32_t>,std::pair<uint32_t,int32_t>> edges;
    std::set<std::array<uint32_t,3>> faces;
    std::vector<bool> used(count,false);
    volume = 0;
    for (uint32_t i=0; i<indexCount; i+=3) {
        const uint32_t a=indices[i], b=indices[i+1], c=indices[i+2];
        if (a>=count || b>=count || c>=count) return false;
        used[a]=used[b]=used[c]=true;
        const auto n = cross(subtract(p[b],p[a]),subtract(p[c],p[a]));
        const double magnitude = std::sqrt(dot(n,n));
        if (!nativePositive(magnitude)) return false;
        Plane plane{p[a],{n[0]/magnitude,n[1]/magnitude,n[2]/magnitude}};
        for (uint32_t j=0; j<count; ++j)
            if (dot(plane.normal,subtract(p[j],p[a])) > extent*2e-6) return false;
        planes.push_back(plane);
        volume += dot(subtract(p[a],p[0]),cross(subtract(p[b],p[0]),subtract(p[c],p[0]))) / 6.0;
        std::array<uint32_t,3> face{welded[a],welded[b],welded[c]};
        std::sort(face.begin(),face.end());
        if (face[0]==face[1] || face[1]==face[2] || !faces.insert(face).second) return false;
        const uint32_t vertices[3] = {welded[a],welded[b],welded[c]};
        for (uint32_t j=0; j<3; ++j) {
            const auto first=vertices[j], second=vertices[(j+1)%3];
            auto& edge = edges[{std::min(first,second),std::max(first,second)}];
            ++edge.first; edge.second += first<second ? 1 : -1;
        }
    }
    if (!nativePositive(volume) || std::find(used.begin(),used.end(),false)!=used.end()) return false;
    for (const auto& edge:edges) if (edge.second.first!=2 || edge.second.second!=0) return false;
    return true;
}
}

extern "C" EMSCRIPTEN_KEEPALIVE uint32_t pr_blast_authoring_abi() { return 1; }
extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_authoring_last_error() { return lastError; }
extern "C" EMSCRIPTEN_KEEPALIVE uint32_t pr_blast_authoring_live_results() { return results.size(); }

extern "C" EMSCRIPTEN_KEEPALIVE uintptr_t pr_blast_authoring_create(
    uint32_t vertexCount, const NvcVec3* positions, const NvcVec3* normals, const NvcVec2* uvs,
    uint32_t indexCount, const uint32_t* indices, uint32_t siteCount, const NvcVec3* sites, int32_t interiorMaterialId)
{
    lastError = 1;
    // A closed triangulated convex surface with V<=255 welded hull vertices
    // has F=2V-4 triangles. Even fully split UV seams use at most3F vertices.
    constexpr uint32_t maximumTriangleIndices=3*(2*255-4);
    if (vertexCount<4 || vertexCount>maximumTriangleIndices || indexCount<12 || indexCount>maximumTriangleIndices ||
        indexCount%3 || siteCount<2 || siteCount>4096 ||
        !validSpan(positions,vertexCount) || !validSpan(indices,indexCount) || !validSpan(sites,siteCount) ||
        (normals && !validSpan(normals,vertexCount)) || (uvs && !validSpan(uvs,vertexCount))) return 0;
    std::vector<NvcVec3> normalized;
    if (normals) normalized.resize(vertexCount);
    double uvMin[2]={0,0},uvMax[2]={0,0};
    for (uint32_t i=0; i<vertexCount; ++i) {
        if ((normals && !finite(normals[i])) || (uvs && (!std::isfinite(uvs[i].x)||!std::isfinite(uvs[i].y)))) return 0;
        if (normals) {
            const double length=std::hypot(double(normals[i].x),double(normals[i].y),double(normals[i].z));
            normalized[i]=length>0 ? NvcVec3{float(normals[i].x/length),float(normals[i].y/length),float(normals[i].z/length)} : normals[i];
        }
        if (uvs) {
            const double value[2]={uvs[i].x,uvs[i].y};
            for (uint32_t axis=0;axis<2;++axis) {
                uvMin[axis]=i ? std::min(uvMin[axis],value[axis]):value[axis];
                uvMax[axis]=i ? std::max(uvMax[axis],value[axis]):value[axis];
                if (!std::isfinite(float(uvMax[axis]-uvMin[axis]))) return 0;
            }
        }
    }
    std::vector<Plane> planes;
    double extent=0, sourceVolume=0;
    lastError = 2;
    if (!validateMesh(positions,vertexCount,indices,indexCount,planes,extent,sourceVolume)) return 0;
    lastError = 3;
    for (uint32_t i=0; i<siteCount; ++i) {
        if (!finite(sites[i])) return 0;
        for (const auto& plane:planes) if (dot(plane.normal,subtract(sites[i],plane.point)) > extent*2e-6) return 0;
        for (uint32_t j=0; j<i; ++j) {
            const auto d=subtract(sites[i],sites[j]);
            if (dot(d,d)<=extent*extent*1e-12) return 0;
        }
    }
    std::unique_ptr<Mesh,Release<Mesh>> mesh(new MeshImpl(positions,normals ? normalized.data():nullptr,uvs,vertexCount,indices,indexCount));
    std::unique_ptr<FractureTool,Release<FractureTool>> tool(new FractureToolImpl);
    const Mesh* source[] = {mesh.get()};
    lastError = 4;
    if (!mesh->isValid() || tool->isMeshContainOpenEdges(mesh.get()) || !tool->setSourceMeshes(source,1)) return 0;
    tool->setInteriorMaterialId(interiorMaterialId);
    tool->setRemoveIslands(false);
    if (tool->voronoiFracturing(tool->getChunkId(0),siteCount,sites,false)!=0) return 0;
    tool->finalizeFracturing();
    auto result = std::make_unique<Result>();
    const auto toolCount = tool->getChunkCount();
    std::unique_ptr<bool[]> support(new bool[toolCount]{});
    std::vector<uint32_t> mapping(toolCount,UINT32_MAX);
    double sumVolume=0;
    lastError = 5;
    for (uint32_t i=0; i<toolCount; ++i) {
        const auto& info = tool->getChunkInfo(i);
        if (!info.isLeaf) continue;
        support[i]=true; mapping[i]=result->chunks.size();
        Triangle* raw=nullptr;
        const uint32_t triangleCount=tool->getBaseMesh(i,raw);
        std::unique_ptr<Triangle[]> triangles(raw);
        if (!triangleCount || !raw || triangleCount>UINT32_MAX/24) { lastError=52; return 0; }
        // Boolean facets may contain multiple edge loops; use the finalized
        // world-space triangles, exactly as upstream ProcessFracture does for
        // its geometry-volume fallback, rather than assuming each facet is one fan.
        NvcVec3 center{};
        const double volume=calculateMeshVolumeAndCentroid(center,TriangleQuery{raw,triangleCount});
        if (!nativePositive(volume) || !finite(center)) { lastError=51; return 0; }
        Chunk chunk;
        chunk.centroidVolume[0]=center.x; chunk.centroidVolume[1]=center.y;
        chunk.centroidVolume[2]=center.z; chunk.centroidVolume[3]=float(volume);
        std::set<std::array<float,3>> points;
        for (uint32_t triangle=0; triangle<triangleCount; ++triangle) {
            const auto& t=raw[triangle];
            const auto n=cross(subtract(t.b.p,t.a.p),subtract(t.c.p,t.a.p));
            const double magnitude=std::sqrt(dot(n,n));
            if (!nativePositive(magnitude)) { lastError=53; return 0; }
            if (t.userData) {
                auto& face=chunk.interfaces[t.userData];
                const double area=magnitude*.5;
                face.area+=area;
                const float* points[3]={&t.a.p.x,&t.b.p.x,&t.c.p.x};
                for (uint32_t axis=0;axis<3;++axis) {
                    face.weightedCenter[axis]+=area*(double(points[0][axis])+points[1][axis]+points[2][axis])/3;
                    face.areaNormal[axis]+=n[axis]*.5;
                }
            }
            for (const auto* vertex:{&t.a,&t.b,&t.c}) {
                if (!finite(vertex->p) || !finite(vertex->n) || !std::isfinite(vertex->uv[0].x) || !std::isfinite(vertex->uv[0].y)) { lastError=54; return 0; }
                const float p[3]={vertex->p.x-center.x,vertex->p.y-center.y,vertex->p.z-center.z};
                points.insert({p[0],p[1],p[2]});
                const double norm=std::hypot(double(vertex->n.x),double(vertex->n.y),double(vertex->n.z));
                chunk.vertices.insert(chunk.vertices.end(), {p[0],p[1],p[2],
                    norm>0 ? float(vertex->n.x/norm):float(n[0]/magnitude),
                    norm>0 ? float(vertex->n.y/norm):float(n[1]/magnitude),
                    norm>0 ? float(vertex->n.z/norm):float(n[2]/magnitude),vertex->uv[0].x,vertex->uv[0].y});
            }
            chunk.materials.push_back(t.materialId);
        }
        // PhysX convex hulls have an actual 255-vertex cooking limit.
        if (points.size()>255) { lastError=55; return 0; }
        sumVolume+=volume;
        result->chunks.push_back(std::move(chunk));
    }
    if (result->chunks.size()!=siteCount) { lastError=56; return 0; }
    if (std::abs(sumVolume-sourceVolume)>sourceVolume*2e-4) { lastError=57; return 0; }
    std::unique_ptr<BlastBondGenerator,Release<BlastBondGenerator>> generator(new BlastBondGeneratorImpl(nullptr));
    NvBlastBondDesc* rawBonds=nullptr; NvBlastChunkDesc* rawChunks=nullptr;
    const int32_t bondCount=generator->buildDescFromInternalFracture(tool.get(),support.get(),rawBonds,rawChunks);
    std::unique_ptr<NvBlastBondDesc,BlastFree> ownedBonds(rawBonds);
    std::unique_ptr<NvBlastChunkDesc,BlastFree> ownedChunks(rawChunks);
    lastError=6;
    if (bondCount<1 || bondCount>65536 || !rawBonds) return 0;
    std::set<std::pair<uint32_t,uint32_t>> seen;
    for (int32_t i=0; i<bondCount; ++i) {
        const auto& b=rawBonds[i];
        if (b.chunkIndices[0]>=toolCount || b.chunkIndices[1]>=toolCount) return 0;
        uint32_t a=mapping[b.chunkIndices[0]], c=mapping[b.chunkIndices[1]];
        if (a==UINT32_MAX || c==UINT32_MAX || a==c || !nativePositive(b.bond.area)) return 0;
        if (a>c) std::swap(a,c);
        if (!seen.insert({a,c}).second) return 0;
        const auto& pa=result->chunks[a].centroidVolume;
        const auto& pc=result->chunks[c].centroidVolume;
        double norm2=0, direction=0;
        for (uint32_t axis=0; axis<3; ++axis) {
            if (!std::isfinite(b.bond.normal[axis]) || !std::isfinite(b.bond.centroid[axis])) return 0;
            norm2+=double(b.bond.normal[axis])*b.bond.normal[axis];
            direction+=double(b.bond.normal[axis])*(double(pc[axis])-pa[axis]);
        }
        if (norm2<=0 || !std::isfinite(norm2) || direction==0) return 0;
        const double factor=(direction>0 ? 1.0:-1.0)/std::sqrt(norm2);
        // Upstream internal bonding reports a triangle-count-weighted centroid.
        // Use the actual interface first moment for the physical application
        // point. Plane IDs, connectivity, normal and area remain NVIDIA-authored.
        const Interface* interface=nullptr;
        for (const auto& entry:result->chunks[a].interfaces) {
            if (entry.first==INT32_MIN) continue;
            const auto other=result->chunks[c].interfaces.find(-entry.first);
            if (other==result->chunks[c].interfaces.end()) continue;
            const auto& face=entry.second;
            const auto& opposite=other->second;
            if (std::abs(face.area-b.bond.area)>b.bond.area*2e-4 ||
                std::abs(opposite.area-b.bond.area)>b.bond.area*2e-4) return 0;
            const double alignment=face.areaNormal[0]*b.bond.normal[0]*factor+
                face.areaNormal[1]*b.bond.normal[1]*factor+face.areaNormal[2]*b.bond.normal[2]*factor;
            if (std::abs(alignment-face.area)>face.area*2e-4 || interface) return 0;
            for (uint32_t axis=0;axis<3;++axis)
                if (std::abs(face.weightedCenter[axis]/face.area-opposite.weightedCenter[axis]/opposite.area)>extent*2e-4) return 0;
            interface=&face;
        }
        if (!interface) return 0;
        result->pairs.insert(result->pairs.end(),{a,c});
        result->bonds.insert(result->bonds.end(),{b.bond.area,
            float(interface->weightedCenter[0]/interface->area),float(interface->weightedCenter[1]/interface->area),float(interface->weightedCenter[2]/interface->area),
            float(b.bond.normal[0]*factor),float(b.bond.normal[1]*factor),float(b.bond.normal[2]*factor)});
    }
    // Do not publish a disconnected or incomplete graph.
    std::vector<bool> connected(siteCount,false); connected[0]=true;
    bool changed=true;
    while (changed) { changed=false; for (uint32_t i=0;i<result->pairs.size();i+=2) {
        const auto a=result->pairs[i],b=result->pairs[i+1];
        if (connected[a]!=connected[b]) { connected[a]=connected[b]=true;changed=true; }
    }}
    if (std::find(connected.begin(),connected.end(),false)!=connected.end()) return 0;
    auto* handle=result.release(); results.insert(handle); lastError=0;
    return reinterpret_cast<uintptr_t>(handle);
}

extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_authoring_counts(uintptr_t handle,uint32_t* out,uint32_t capacity) {
    const auto* result=checked(handle);
    if (!result || capacity<4 || !validSpan(out,4)) return -1;
    uint64_t vertices=0,triangles=0;
    for (const auto& chunk:result->chunks) { vertices+=chunk.vertices.size()/8;triangles+=chunk.materials.size(); }
    if (vertices>UINT32_MAX || triangles>UINT32_MAX) return -1;
    out[0]=result->chunks.size();out[1]=result->pairs.size()/2;out[2]=vertices;out[3]=triangles;return 0;
}
extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_authoring_chunk(uintptr_t handle,uint32_t index,float* out,uint32_t* counts) {
    const auto* result=checked(handle);
    if (!result || index>=result->chunks.size() || !validSpan(out,4) || !validSpan(counts,2)) return -1;
    const auto& chunk=result->chunks[index];
    std::copy(chunk.centroidVolume,chunk.centroidVolume+4,out);
    counts[0]=chunk.vertices.size()/8;counts[1]=chunk.materials.size();return 0;
}
extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_authoring_mesh(uintptr_t handle,uint32_t index,float* vertices,
    uint32_t* indices,int32_t* materials,uint32_t vertexCapacity,uint32_t triangleCapacity) {
    const auto* result=checked(handle);
    if (!result || index>=result->chunks.size()) return -1;
    const auto& chunk=result->chunks[index];const auto count=chunk.vertices.size()/8;
    if (vertexCapacity<count || triangleCapacity<chunk.materials.size() || !validSpan(vertices,chunk.vertices.size()) ||
        !validSpan(indices,count) || !validSpan(materials,chunk.materials.size())) return -1;
    std::copy(chunk.vertices.begin(),chunk.vertices.end(),vertices);
    for (uint32_t i=0;i<count;++i) indices[i]=i;
    std::copy(chunk.materials.begin(),chunk.materials.end(),materials);return 0;
}
extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_authoring_bonds(uintptr_t handle,uint32_t* pairs,float* data,uint32_t capacity) {
    const auto* result=checked(handle);
    if (!result || capacity<result->pairs.size()/2 || !validSpan(pairs,result->pairs.size()) || !validSpan(data,result->bonds.size())) return -1;
    std::copy(result->pairs.begin(),result->pairs.end(),pairs);std::copy(result->bonds.begin(),result->bonds.end(),data);
    return result->pairs.size()/2;
}
extern "C" EMSCRIPTEN_KEEPALIVE int32_t pr_blast_authoring_destroy(uintptr_t handle) {
    auto* result=checked(handle);if (!result) return -1;results.erase(result);delete result;return 0;
}
