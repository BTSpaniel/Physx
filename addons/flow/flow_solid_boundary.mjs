// SPDX-License-Identifier: MIT
/** Solid boundary ABI1. Public planes use dot(n,x)<=d; GPU planes use n.x+w<=0. */
export function packSolidBoundaries(records, layers) {
    if (!Array.isArray(records) || records.length > 65536) throw new RangeError('Expected at most 65536 solid boundaries');
    const packet = new ArrayBuffer(records.length * 160), words = new Uint32Array(packet), floats = new Float32Array(packet);
    const planeValues = [], ids = new Set(), boundaryLayers = new Map();
    const number = value => {
        if (!Number.isFinite(value) || !Number.isFinite(Math.fround(value))) throw new RangeError('Boundary values must be finite f32 numbers');
        return Math.fround(value);
    };
    const vector = (value, count) => {
        if (!value || value.length !== count || typeof value.every !== 'function') throw new RangeError('Invalid solid boundary vector');
        return Array.from(value, number);
    };
    const flag = value => { if (typeof value !== 'boolean') throw new RangeError('Invalid boundary boolean'); return Number(value); };
    const allowed = new Set(['id','layer','type','position','quaternion','radius','halfSize','planes','bounds','enabled','resetMotion','terminalExchange']);
    let enabledCount = 0;
    records.forEach((record, index) => {
        if (!record || typeof record !== 'object' || Object.keys(record).some(key => !allowed.has(key))) throw new RangeError('Unknown solid boundary field');
        const { id, layer = 0, type, position = [0,0,0], quaternion = [0,0,0,1], enabled = true, resetMotion = false } = record;
        if (!Number.isInteger(id) || id < 1 || id > 0xffffffff || ids.has(id)) throw new RangeError('Boundary IDs must be unique nonzero u32 integers');
        if (!Number.isInteger(layer) || !layers.has(layer)) throw new RangeError('Boundary references an unknown layer');
        const typeId = ['sphere','box','plane','convex'].indexOf(type);
        if (typeId < 0) throw new RangeError('Boundary requires sphere, box, plane or convex geometry');
        const offset = index * 40, q = vector(quaternion,4), norm = Math.hypot(...q);
        if (!(norm > 0)) throw new RangeError('Boundary quaternion cannot be zero');
        words[offset] = layer; words[offset+1] = typeId; words[offset+2] = flag(enabled) | (flag(resetMotion) << 1) | (flag(record.terminalExchange??false) << 2);
        floats.set(vector(position,3),offset+4); floats.set(q.map(v=>v/norm),offset+8);
        floats.set(floats.subarray(offset+4,offset+8),offset+24); floats.set(floats.subarray(offset+8,offset+12),offset+28);
        words[offset+33] = id;
        if (type === 'sphere') {
            const radius=number(record.radius); if (!(radius>0) || !Number.isFinite(Math.fround(radius*radius))) throw new RangeError('Invalid sphere radius');
            floats[offset+7]=radius;
        } else if (type === 'box') {
            const size=vector(record.halfSize,3);if(size.some(v=>v<=0))throw new RangeError('Box half sizes must be positive');
            floats.set(size,offset+12);
        } else if (type === 'convex') {
            if (!Array.isArray(record.planes) || record.planes.length<4 || record.planes.length>256 || !Array.isArray(record.bounds) || record.bounds.length!==2)
                throw new RangeError('Convex boundary needs4..256 planes and local min/max bounds');
            const lower=vector(record.bounds[0],3),upper=vector(record.bounds[1],3);
            if(lower.some((v,i)=>v>=upper[i]))throw new RangeError('Convex bounds must have positive volume');
            floats.set(lower.map((v,i)=>(upper[i]-v)*.5),offset+12);floats.set(lower.map((v,i)=>(upper[i]+v)*.5),offset+36);
            words[offset+3]=record.planes.length;words[offset+32]=planeValues.length/4;
            for(const value of record.planes){const plane=vector(value,4),length=Math.hypot(...plane.slice(0,3));
                if(!(length>0))throw new RangeError('Convex plane normal cannot be zero');
                planeValues.push(plane[0]/length,plane[1]/length,plane[2]/length,-plane[3]/length);
            }
        }
        ids.add(id);boundaryLayers.set(id,layer);enabledCount+=Number(enabled);
    });
    if(planeValues.length>4*1048576)throw new RangeError('Convex plane data exceeds boundary capacity');
    return { packet, planes:new Float32Array(planeValues), boundaryLayers, enabledCount };
}

/** ABI-identical point and segment queries for cached density presentation. */
export function solidBoundaryWGSL({ group = 0, binding = 9, name = 'solidBoundary' } = {}) {
    if(!Number.isInteger(group)||group<0||!Number.isInteger(binding)||binding<0||!/^[_A-Za-z][_A-Za-z0-9]*$/.test(name))throw new RangeError('Invalid boundary shader binding');
    return `
@group(${group}) @binding(${binding}) var<storage,read> ${name}:array<vec4u>;
fn solidRotate(q:vec4f,v:vec3f)->vec3f { let t=2.0*cross(q.xyz,v);return v+q.w*t+cross(q.xyz,t); }
fn solidUnrotate(q:vec4f,v:vec3f)->vec3f { return solidRotate(vec4f(-q.xyz,q.w),v); }
fn solidInsideShape(shape:u32,world:vec3f)->bool {
    let b=${name}[0].z+shape*10u;let shapeData=${name}[b];let centre=bitcast<vec4f>(${name}[b+1u]);
    let p=solidUnrotate(bitcast<vec4f>(${name}[b+2u]),world-centre.xyz);
    if(shapeData.y==0u){return dot(p,p)<centre.w*centre.w;}
    if(shapeData.y==1u){return all(abs(p)<bitcast<vec4f>(${name}[b+3u]).xyz);}
    if(shapeData.y==2u){return p.x<0.0;}
    let offset=${name}[0].w+${name}[b+8u].x;
    for(var i=0u;i<shapeData.w;i++){let plane=bitcast<vec4f>(${name}[offset+i]);if(dot(plane.xyz,p)+plane.w>=0.0){return false;}}
    return true;
}
fn solidIntersectShape(shape:u32,start:vec3f,end:vec3f)->f32 {
    let b=${name}[0].z+shape*10u;let shapeData=${name}[b];let centre=bitcast<vec4f>(${name}[b+1u]);let q=bitcast<vec4f>(${name}[b+2u]);
    let a=solidUnrotate(q,start-centre.xyz);let d=solidUnrotate(q,end-centre.xyz)-a;
    if(shapeData.y==0u){let aa=dot(d,d);let bb=dot(a,d);let cc=dot(a,a)-centre.w*centre.w;
        if(cc<0.0){return 0.0;}if(aa<1e-30){return 1.0;}let disc=bb*bb-aa*cc;if(disc<=0.0){return 1.0;}
        let lower=(-bb-sqrt(disc))/aa;let upper=(-bb+sqrt(disc))/aa;
        return select(1.0,max(0.0,lower),upper>0.0&&lower<1.0);
    }
    var lower=0.0;var upper=1.0;let planeCount=select(select(shapeData.w,1u,shapeData.y==2u),6u,shapeData.y==1u);
    for(var i=0u;i<planeCount;i++){
        var n=vec3f(0);var w=0.0;
        if(shapeData.y==1u){let axis=i/2u;n[axis]=select(1.0,-1.0,(i&1u)!=0u);w=-bitcast<vec4f>(${name}[b+3u])[axis];}
        else if(shapeData.y==2u){n=vec3f(1,0,0);}
        else{let p=bitcast<vec4f>(${name}[${name}[0].w+${name}[b+8u].x+i]);n=p.xyz;w=p.w;}
        let distance=dot(n,a)+w;let rate=dot(n,d);
        if(abs(rate)<1e-30){if(distance>=0.0){return 1.0;}}
        else{let t=-distance/rate;if(rate<0.0){lower=max(lower,t);}else{upper=min(upper,t);}if(lower>upper){return 1.0;}}
    }
    return select(1.0,max(0.0,lower),upper>0.0&&lower<1.0&&upper>lower);
}
fn solidContainingShape(layer:u32,world:vec3f)->u32 {
    var i=0u;loop{if(i>=${name}[0].x){break;}let n=${name}[0].y+i*2u;let lower=${name}[n];let upper=${name}[n+1u];
        if(any(world<bitcast<vec4f>(lower).xyz)||any(world>bitcast<vec4f>(upper).xyz)){i=upper.w;continue;}
        if(lower.w!=0xffffffffu&&${name}[${name}[0].z+lower.w*10u].x==layer&&solidInsideShape(lower.w,world)){return lower.w;}i++;
    }return 0xffffffffu;
}
fn solidInside(layer:u32,world:vec3f)->bool { return solidContainingShape(layer,world)!=0xffffffffu; }
fn solidWallVelocity(layer:u32,world:vec3f)->vec4f {
    let shape=solidContainingShape(layer,world);if(shape==0xffffffffu){return vec4f(0);}
    let b=${name}[0].z+shape*10u;
    let velocity=bitcast<vec4f>(${name}[b+4u]).xyz+cross(bitcast<vec4f>(${name}[b+5u]).xyz,world-bitcast<vec4f>(${name}[b+1u]).xyz);
    return vec4f(velocity,1);
}
fn solidShapeMayOverlapBox(shapeIndex:u32,layer:u32,lowerBound:vec3f,upperBound:vec3f)->bool {
    let shape=${name}[0].z+shapeIndex*10u;let data=${name}[shape];
    if(data.x!=layer){return false;}
    if(data.y!=2u){return true;}
    // Infinite planes have unbounded BVH boxes. Reject only when the entire
    // donor box is provably in the fluid half-space. Keep the same rounding
    // guard for direct broad-phase queries and cached leaf candidates.
    let centre=bitcast<vec4f>(${name}[shape+1u]).xyz;
    let normal=solidRotate(bitcast<vec4f>(${name}[shape+2u]),vec3f(1,0,0));
    let nearest=select(upperBound,lowerBound,normal>=vec3f(0));
    let distance=dot(normal,nearest-centre);
    let rounding=0.00000762939453125*(1.+dot(abs(normal),abs(nearest)+abs(centre)));
    return distance<=rounding;
}
fn solidOverlapsBox(layer:u32,lowerBound:vec3f,upperBound:vec3f)->bool {
    var i=0u;loop{if(i>=${name}[0].x){break;}let n=${name}[0].y+i*2u;let lower=${name}[n];let upper=${name}[n+1u];
        if(any(lowerBound>bitcast<vec4f>(upper).xyz)||any(upperBound<bitcast<vec4f>(lower).xyz)){i=upper.w;continue;}
        if(lower.w!=0xffffffffu&&solidShapeMayOverlapBox(lower.w,layer,lowerBound,upperBound)){return true;}i++;
    }return false;
}
// Node ordinals belong only to this immutable geometry generation. Overflow
// means use the original full BVH; no physical shape is omitted or truncated.
struct SolidStencilCandidates { count:u32, nodes:array<u32,8> }
fn solidStencilCandidates(layer:u32,lowerBound:vec3f,upperBound:vec3f)->SolidStencilCandidates {
    var result:SolidStencilCandidates;var i=0u;
    loop{if(i>=${name}[0].x){break;}let n=${name}[0].y+i*2u;let lower=${name}[n];let upper=${name}[n+1u];
        if(any(lowerBound>bitcast<vec4f>(upper).xyz)||any(upperBound<bitcast<vec4f>(lower).xyz)){i=upper.w;continue;}
        if(lower.w!=0xffffffffu&&solidShapeMayOverlapBox(lower.w,layer,lowerBound,upperBound)){
            if(result.count==8u){result.count=0xffffffffu;return result;}
            result.nodes[result.count]=i;result.count++;
        }i++;
    }return result;
}
fn solidCandidateInside(node:u32,world:vec3f)->bool {
    let n=${name}[0].y+node*2u;let lower=${name}[n];let upper=${name}[n+1u];
    if(any(world<bitcast<vec4f>(lower).xyz)||any(world>bitcast<vec4f>(upper).xyz)){return false;}
    return solidInsideShape(lower.w,world);
}
fn solidCandidateTrace(node:u32,start:vec3f,end:vec3f)->f32 {
    let n=${name}[0].y+node*2u;let lower=${name}[n];let upper=${name}[n+1u];
    if(any(min(start,end)>bitcast<vec4f>(upper).xyz)||any(max(start,end)<bitcast<vec4f>(lower).xyz)){return 1.0;}
    return solidIntersectShape(lower.w,start,end);
}
fn solidTrace(layer:u32,start:vec3f,end:vec3f)->f32 {
    var hit=1.0;var i=0u;loop{if(i>=${name}[0].x){break;}let n=${name}[0].y+i*2u;let lower=${name}[n];let upper=${name}[n+1u];
        if(any(min(start,end)>bitcast<vec4f>(upper).xyz)||any(max(start,end)<bitcast<vec4f>(lower).xyz)){i=upper.w;continue;}
        if(lower.w!=0xffffffffu&&${name}[${name}[0].z+lower.w*10u].x==layer){hit=min(hit,solidIntersectShape(lower.w,start,end));}i++;
    }return hit;
}
`;
}
