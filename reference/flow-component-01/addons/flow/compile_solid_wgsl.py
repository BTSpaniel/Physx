#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build licensed Flow operator variants with analytic solid boundaries.

Upstream files are copied into a temporary build tree. Exact transformations
fail closed if the pinned operator source changes; no vendor file is edited.
"""
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import argparse
from pathlib import Path
from compile_wgsl import ROOT, DEFAULT_FLOW_ROOT, DEFAULT_SLANGC, include_paths, shader_sources, narrow_storage_access, license_header, sha256

HERE = Path(__file__).resolve().parent
NAMES = (
    'AdvectionDensity1CS', 'AdvectionDensity2CS', 'AdvectionVelocity1CS',
    'AdvectionVelocity2CS', 'AdvectionDownsampleCS', 'AdvectionFadeDensityCS',
    'AdvectionFadeVelocityCS', 'AdvectionSimpleCS', 'PressureDivergenceCS',
    'PressureJacobiCS', 'PressureSubtractCS', 'EmitterSimpleCS', 'EmitterBoxCS',
    'EmitterMeshApplyCS', 'EmitterNanoVdb2CS', 'EmitterPoint3CS', 'EmitterPoint4CS',
    'EmitterPointClearDownsampleCS', 'EmitterPointClearMarkedCS', 'EmitterTextureCS',
    'Vorticity2CS',
)


def replace_once(code, old, new):
    if code.count(old) != 1:
        raise RuntimeError('Pinned Flow source changed: ' + old[:90])
    return code.replace(old, new)


def common(code):
    code = replace_once(code, '#include "AdvectionParams.h"', '#include "AdvectionParams.h"\n#include "PrSolidBoundary.hlsli"')
    # Explicit reference positions are per invocation, not mutable global state.
    code = replace_once(code,
        'float4 velocity = advectReadLinear4f(params.globalFetch, velocityIn, velocitySampler, table, velocityTableParams, blockIdx, velocityFetchIdxf);',
        'uint prLayer; float3 prReference = prWorld(table, valueTableParams, blockIdx, float3(threadIdx)+.5f, prLayer);\n'
        '    float4 velocity = prReadLinear4f(velocityIn, velocitySampler, table, velocityTableParams, blockIdx, velocityFetchIdxf, prReference, true);')
    code = replace_once(code,
        'float4 value = advectReadLinear4f(params.globalFetch, valueIn, valueSampler, table, valueTableParams, blockIdx, fetchIdxf);',
        'float4 value = prReadLinear4f(valueIn, valueSampler, table, valueTableParams, blockIdx, fetchIdxf, prReference, prBoundaryControl.mode!=0u);')
    # MacCormack is retained where its complete trajectory and donor stencil
    # are visible. At an obstacle stencil use the bounded first-order predictor.
    code = replace_once(code, '    if (shouldCorrect)\n    {', '''    uint prLayer; float3 prReference=prWorld(table,valueTableParams,blockIdx,float3(threadIdx)+.5f,prLayer);
    if(prBoundary[0].x!=0u && shouldCorrect) {
        float3 vFetch=params.base.valueToVelocityBlockScale*(float3(threadIdx)+.5f);
        float3 v=prReadLinear4f(velocityIn,velocitySampler,table,velocityTableParams,blockIdx,vFetch,prReference,true).xyz;
        float3 shift=computeDisplacement(params.base,v);
        shouldCorrect=prStencilVisible(table,valueTableParams,blockIdx,float3(threadIdx)+.5f-shift,prReference)
            && prStencilVisible(table,valueTableParams,blockIdx,float3(threadIdx)+.5f+shift,prReference);
    }
    if (shouldCorrect)
    {''')
    code = replace_once(code,
        'float4 velocity = advectReadLinear4f(params.base.globalFetch, velocityIn, velocitySampler, table, velocityTableParams, blockIdx, velocityFetchIdxf);',
        'float4 velocity = prReadLinear4f(velocityIn,velocitySampler,table,velocityTableParams,blockIdx,velocityFetchIdxf,prReference,true);')
    code = replace_once(code,
        'float4 predictRev = advectReadLinear4f(params.base.globalFetch, predictIn, predictSampler, table, valueTableParams, blockIdx, revFetchIdxf);',
        'float4 predictRev = prReadLinear4f(predictIn,predictSampler,table,valueTableParams,blockIdx,revFetchIdxf,prReference,prBoundaryControl.mode!=0u,false);')
    return code


def transform(name, code):
    if '#include "AdvectionCommon.hlsli"' not in code:
        code = replace_once(code, '#include "NvFlowShader.hlsli"', '#include "NvFlowShader.hlsli"\n#include "PrSolidBoundary.hlsli"')
    if name in ('AdvectionDownsampleCS', 'AdvectionVelocity2CS'):
        code = replace_once(code,
            'float4 density = NvFlowLocalReadLinear4f(densityIn, valueSampler, tableIn, globalParamsIn.tableDensity, blockIdx, densityFetchIdxf);',
            'uint prLayer; float3 prReference=prWorld(tableIn,globalParamsIn.table,blockIdx,float3(threadIdx)+.5f,prLayer);\n'
            '    float4 density=prReadLinear4f(densityIn,valueSampler,tableIn,globalParamsIn.tableDensity,blockIdx,densityFetchIdxf,prReference,false,false);')
    if name.startswith('Pressure'):
        if name in ('PressureDivergenceCS', 'PressureJacobiCS'):
            access = 'RWStructuredBuffer' if name == 'PressureDivergenceCS' else 'StructuredBuffer'
            code = replace_once(code, '[numthreads(128, 1, 1)]',
                f'[[vk::binding(32,0)]] {access}<uint> prPressureFaces;\n\n[numthreads(128, 1, 1)]')
        start = code.index('    // can ignore .w')
        code = code[:start] + (HERE / (name + '.body.hlsli')).read_text() + '\n}\n'
    else:
        # These are canonical field writes, including post-pressure emitters.
        pattern = re.compile(r'NvFlowLocalWrite4f\((\w+), (\w+), ([\w.]+), (\w+), (\w+), (\w+)\);')
        matches = list(pattern.finditer(code))
        if not matches:
            raise RuntimeError('No canonical field write in ' + name)
        def masked(match):
            tex, table, params, block, cell, value = match.groups()
            fn = 'prScalarBoundary' if tex.startswith('density') else 'prValueBoundary'
            return f'NvFlowLocalWrite4f({tex}, {table}, {params}, {block}, {cell}, {fn}({table}, {params}, {block}, {cell}, {value}));'
        code = pattern.sub(masked, code)
    return code


def build(slangc=DEFAULT_SLANGC):
    output=ROOT/'dist/flow-wgsl/addons/solid'
    output.mkdir(parents=True,exist_ok=True)
    rows=[]
    sources=shader_sources(DEFAULT_FLOW_ROOT)
    by_name={p.stem:p for p in sources}
    with tempfile.TemporaryDirectory(prefix='pr-flow-solid-') as temporary:
        tree=Path(temporary)
        for folder in ('include','source'):
            shutil.copytree(DEFAULT_FLOW_ROOT/folder,tree/folder)
        common_path=tree/'source/nvflow/shaders/AdvectionCommon.hlsli'
        common_path.write_text(common(common_path.read_text()))
        shutil.copy2(HERE/'PrSolidBoundary.hlsli',tree/'include/nvflow/shaders/PrSolidBoundary.hlsli')
        includes=[tree/p.relative_to(DEFAULT_FLOW_ROOT) for p in include_paths(DEFAULT_FLOW_ROOT,sources)]
        for name in NAMES:
            original=by_name[name]
            derived=tree/original.relative_to(DEFAULT_FLOW_ROOT)
            derived.write_text(transform(name,derived.read_text()))
            dst=output/('Solid'+name+'.wgsl')
            reflection=dst.with_suffix('.reflection.json')
            cmd=[str(slangc),str(derived),'-lang','hlsl','-target','wgsl','-entry','main','-stage','compute',
                 '-matrix-layout-column-major','-preserve-params','-reflection-json',str(reflection)]
            for include in includes:cmd += ['-I',str(include)]
            cmd += ['-o',str(dst)]
            result=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            if result.returncode:raise RuntimeError(name+':\n'+result.stdout)
            normalized,narrowed=narrow_storage_access(dst.read_text())
            dst.write_text(license_header(original.read_text())+'// Derived boundary operator, generated by pinned Slang.\n'+normalized)
            rows.append({'source':original.relative_to(DEFAULT_FLOW_ROOT).as_posix(),'sourceSha256':sha256(original),
                         'derivedSourceSha256':sha256(derived),'wgsl':dst.relative_to(ROOT/'dist/flow-wgsl').as_posix(),
                         'wgslSha256':sha256(dst),'reflection':reflection.relative_to(ROOT/'dist/flow-wgsl').as_posix(),
                         'reflectionSha256':sha256(reflection),'storageAccessNarrowed':narrowed})
            print('PASS: Solid'+name,flush=True)
    report={'abi':1,'component':'Flow analytic solid boundary operators','shaders':rows,
            'sourceHashes':{p.name:sha256(p) for p in sorted(HERE.glob('*')) if p.is_file() and (p.name.startswith('PrSolid') or p.name.startswith('Pressure') or p.name=='compile_solid_wgsl.py')}}
    (output/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slangc',type=Path,default=DEFAULT_SLANGC)
    build(parser.parse_args().slangc)
