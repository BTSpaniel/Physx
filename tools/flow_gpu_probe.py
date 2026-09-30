#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Measure the actual 1024-lane WebGPU capability required by NVIDIA Flow."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from serve import create_server


def launch_options(browser: Path | None, hardware: bool, software_vulkan: bool,
                   lavapipe_icd: Path | None = None) -> tuple[dict, dict]:
    """Select a runner backend without changing limits or the caller environment."""
    if hardware and software_vulkan:
        raise ValueError('--hardware and --software-vulkan are mutually exclusive')
    if lavapipe_icd and not software_vulkan:
        raise ValueError('--lavapipe-icd requires --software-vulkan')
    options = {'headless': True, 'executable_path': str(browser) if browser else None,
               'args': [] if hardware else ['--enable-unsafe-webgpu',
                   '--enable-features=Vulkan,WebGPUDeveloperFeatures',
                   '--use-angle=swiftshader', '--disable-vulkan-surface']}
    metadata = {'requestedBackend': 'hardware' if hardware else 'swiftshader',
                'gpuMode': 'hardware' if hardware else 'software-WebGPU'}
    if not software_vulkan:
        return options, metadata
    if not sys.platform.startswith('linux'):
        raise ValueError('--software-vulkan requires Linux and Mesa lavapipe')
    chosen = lavapipe_icd or os.environ.get('VK_ICD_FILENAMES') or os.environ.get('VK_DRIVER_FILES')
    if chosen:
        if isinstance(chosen, str) and os.pathsep in chosen:
            raise ValueError('Select exactly one lavapipe ICD descriptor')
        path = Path(chosen)
    else:
        candidates = sorted(Path('/usr/share/vulkan/icd.d').glob('lvp_icd*.json'))
        if len(candidates) != 1:
            raise ValueError('Install mesa-vulkan-drivers and select exactly one lvp_icd*.json')
        path = candidates[0]
    if path.suffix != '.json' or path.is_symlink() or not path.is_file():
        raise ValueError('Lavapipe ICD must be an ordinary JSON file')
    path = path.resolve(strict=True)
    descriptor = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(descriptor, dict) or not isinstance(descriptor.get('ICD'), dict):
        raise ValueError('Lavapipe ICD must contain an ICD object')
    library = descriptor['ICD'].get('library_path')
    if not isinstance(library, str) or not library or not Path(library).name.startswith('libvulkan_lvp.so'):
        raise ValueError('Selected ICD does not describe Mesa lavapipe')
    environment = dict(os.environ)
    environment.update(VK_ICD_FILENAMES=str(path), VK_DRIVER_FILES=str(path))
    options.update(env=environment, args=['--enable-unsafe-webgpu',
        '--enable-features=Vulkan,WebGPUDeveloperFeatures', '--use-angle=vulkan',
        '--use-vulkan=native', '--disable-vulkan-surface'])
    metadata.update(requestedBackend='mesa-lavapipe', icd={
        'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'libraryPath': library, 'apiVersion': descriptor['ICD'].get('api_version')})
    return options, metadata


PROBE_JS = r'''async expectedBackend => {
    const result={status:'RUNNING',requestedBackend:expectedBackend,
        requiredFeatures:['float32-filterable'],requiredLimits:{maxComputeInvocationsPerWorkgroup:1024,maxComputeWorkgroupSizeX:1024},
        errors:[],cleanup:'NOT_CREATED'};
    let device,storage,read,scoped=false;
    try {
        if(!navigator.gpu)throw Error('WebGPU is unavailable');
        const adapter=await navigator.gpu.requestAdapter({powerPreference:'high-performance'});
        if(!adapter)throw Error('No WebGPU adapter');
        const info=adapter.info;
        result.adapter=Object.fromEntries(['vendor','architecture','device','description'].map(key=>[key,info?.[key]??'']));
        result.supportedFeatures=Array.from(adapter.features).sort();
        result.supportedLimits={};
        for(const key of ['maxComputeInvocationsPerWorkgroup','maxComputeWorkgroupSizeX','maxComputeWorkgroupSizeY','maxComputeWorkgroupSizeZ','maxComputeWorkgroupStorageSize'])result.supportedLimits[key]=adapter.limits[key];
        result.observedBackend=/\b(llvmpipe|lavapipe)\b/i.test(Object.values(result.adapter).join(' '))?'mesa-lavapipe':'other';
        if(expectedBackend==='mesa-lavapipe'&&result.observedBackend!=='mesa-lavapipe')throw Error('Selected browser adapter is not Mesa lavapipe: '+JSON.stringify(result.adapter));
        for(const feature of result.requiredFeatures)if(!adapter.features.has(feature))throw Error('Unsupported Flow feature: '+feature);
        for(const [key,value]of Object.entries(result.requiredLimits))if(adapter.limits[key]<value)throw Error('Unsupported Flow limit '+key+': requires '+value+', adapter supports '+adapter.limits[key]);
        device=await adapter.requestDevice({requiredFeatures:result.requiredFeatures,requiredLimits:result.requiredLimits});
        result.deviceLimits=Object.fromEntries(Object.keys(result.requiredLimits).map(key=>[key,device.limits[key]]));
        device.addEventListener('uncapturederror',event=>result.errors.push(event.error.message));
        device.pushErrorScope('validation');scoped=true;
        const shader=device.createShaderModule({label:'Flow actual 1024-lane admission',code:`
            @group(0) @binding(0) var<storage,read_write> output:array<u32>;
            var<workgroup> laneValues:array<u32,1024>;
            @compute @workgroup_size(1024) fn main(@builtin(local_invocation_index) lane:u32){
                laneValues[lane]=lane+1u;workgroupBarrier();output[lane]=laneValues[lane^1u];
            }`});
        const compilation=await shader.getCompilationInfo();
        result.compilationMessages=Array.from(compilation.messages,message=>({type:message.type,message:message.message}));
        if(result.compilationMessages.some(message=>message.type==='error'))throw Error('1024-lane WGSL compilation failed');
        const pipeline=await device.createComputePipelineAsync({layout:'auto',compute:{module:shader,entryPoint:'main'}});
        storage=device.createBuffer({size:4096,usage:GPUBufferUsage.STORAGE|GPUBufferUsage.COPY_SRC});
        read=device.createBuffer({size:4096,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ});
        const binding=device.createBindGroup({layout:pipeline.getBindGroupLayout(0),entries:[{binding:0,resource:{buffer:storage}}]});
        const encoder=device.createCommandEncoder();const pass=encoder.beginComputePass();
        pass.setPipeline(pipeline);pass.setBindGroup(0,binding);pass.dispatchWorkgroups(1);pass.end();
        encoder.copyBufferToBuffer(storage,0,read,0,4096);device.queue.submit([encoder.finish()]);
        await device.queue.onSubmittedWorkDone();await read.mapAsync(GPUMapMode.READ);
        const values=new Uint32Array(read.getMappedRange());let mismatches=0,sum=0;
        for(let lane=0;lane<1024;lane++){if(values[lane]!==((lane^1)+1))mismatches++;sum+=values[lane];}
        read.unmap();result.compute={workgroups:1,workgroupSize:1024,checkedLanes:1024,sharedMemoryBarrier:true,mismatches,sum,expectedSum:524800};
        const validation=await device.popErrorScope();scoped=false;
        if(validation)result.errors.push(validation.message);
        if(mismatches||sum!==524800||result.errors.length)throw Error('Actual 1024-lane execution failed: '+JSON.stringify(result.compute)+' '+result.errors.join('\n'));
        result.status='PASS';
    }catch(error){result.status='FAIL';result.error=String(error);}
    finally {
        if(scoped){const validation=await device.popErrorScope();if(validation)result.errors.push(validation.message);}
        if(read?.mapState==='mapped')read.unmap();read?.destroy();storage?.destroy();device?.destroy();
        result.cleanup=device?'DESTROYED':'NOT_CREATED';
        if(result.errors.length)result.status='FAIL';
    }
    return result;
}'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser-executable', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--hardware', action='store_true')
    mode.add_argument('--software-vulkan', action='store_true')
    parser.add_argument('--lavapipe-icd', type=Path)
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/flow-host-capability.json')
    args = parser.parse_args()
    report = {'status': 'RUNNING', 'startedUtc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Actual one-workgroup 1024-lane WebGPU admission; not execution of the Flow graph.',
              'errors': [], 'consoleErrors': []}
    server = None
    try:
        options, metadata = launch_options(args.browser_executable, args.hardware,
                                           args.software_vulkan, args.lavapipe_icd)
        report.update(metadata)
        from playwright.sync_api import sync_playwright
        server = create_server()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**options)
            try:
                report['browserVersion'] = browser.version
                page = browser.new_page()
                page.on('pageerror', lambda error: report['errors'].append(str(error)))
                page.on('console', lambda message: report['consoleErrors'].append(message.text) if message.type == 'error' else None)
                page.goto(f'http://127.0.0.1:{server.server_port}/upstream.lock.json')
                report['capability'] = page.evaluate(PROBE_JS, metadata['requestedBackend'])
                report['status'] = 'PASS' if report['capability']['status'] == 'PASS' and not report['errors'] and not report['consoleErrors'] else 'FAIL'
            finally:
                browser.close()
    except Exception as error:
        report.update(status='FAIL', error=str(error))
    finally:
        if server:
            server.shutdown()
            server.server_close()
        report['finishedUtc'] = datetime.now(timezone.utc).isoformat()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
