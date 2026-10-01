# SPDX-License-Identifier: MIT
"""Verify native upstream Flow velocity-only collisions on a real Chrome WebGPU adapter."""
import argparse
import hashlib
import json
import sys
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from scene_browser_test import Handler, ROOT


def file_hashes(paths):
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unified', action='store_true')
    parser.add_argument('--repo', type=Path, default=Path('C:/Coding/game'))
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo / 'tests/storage'))
    from performance_run_lock import performance_run_lock
    artifact = 'dist/candidate/physx-pe' if args.unified else 'dist/flow-host/flow-host'
    paths = [f'{artifact}.mjs', f'{artifact}.wasm', 'addons/flow/pr_flow_host.cpp',
             'addons/flow/flow_host_webgpu.mjs', 'addons/flow/collision_oracle.mjs',
             'addons/flow/collision_browser_test.py', 'addons/flow/scene_browser_test.py',
             'dist/flow-wgsl/manifest.json']
    manifest = json.loads((ROOT / 'dist/flow-wgsl/manifest.json').read_text())
    corpus_paths = sorted({f'dist/flow-wgsl/{item[key]}' for item in manifest['shaders'] for key in ('wgsl',)} |
                          {f"dist/flow-wgsl/{item['wgsl'].replace('.wgsl', '.reflection.json')}" for item in manifest['shaders']})
    report = {'status': 'RUNNING', 'unified': args.unified, 'flowCollisionAbi': 1,
              'pageErrors': [], 'consoleErrors': [], 'gpuErrors': []}
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with performance_run_lock(args.repo), sync_playwright() as playwright:
            report['sourceHashes'] = file_hashes(paths)
            report['corpusHashes'] = file_hashes(corpus_paths)
            report['artifactHashes'] = {Path(path).name: digest for path, digest in report['sourceHashes'].items() if path.startswith(artifact)}
            report['bridgeSha256'] = report['sourceHashes']['addons/flow/flow_host_webgpu.mjs']
            report['hostSourceSha256'] = report['sourceHashes']['addons/flow/pr_flow_host.cpp']
            browser = playwright.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
            try:
                report['browser'] = browser.version
                page = browser.new_page()
                page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
                page.on('console', lambda message: report['consoleErrors'].append(message.text) if message.type == 'error' else None)
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.evaluate('''artifact => {
                    const state=globalThis.collisionResult={status:'RUNNING',gpuErrors:[],adapter:null};
                    (async()=>{
                        const {default:init}=await import('/'+artifact+'.mjs');
                        const {verifyFlowCollision}=await import('/addons/flow/collision_oracle.mjs');
                        const module=await init();
                        const adapter=await navigator.gpu.requestAdapter({powerPreference:'high-performance'});
                        const isFallbackAdapter=adapter?.info?.isFallbackAdapter??adapter?.isFallbackAdapter;
                        if(!adapter||isFallbackAdapter!==false)throw Error('A confirmed non-fallback native WebGPU adapter is required');
                        state.adapter={vendor:adapter.info.vendor,architecture:adapter.info.architecture,
                            device:adapter.info.device,description:adapter.info.description,isFallbackAdapter};
                        const device=await adapter.requestDevice({requiredFeatures:['float32-filterable'],
                            requiredLimits:{maxComputeInvocationsPerWorkgroup:1024,maxComputeWorkgroupSizeX:1024}});
                        state.deviceLimits={maxBufferSize:device.limits.maxBufferSize,
                            maxStorageBufferBindingSize:device.limits.maxStorageBufferBindingSize,
                            maxStorageBuffersPerShaderStage:device.limits.maxStorageBuffersPerShaderStage,
                            maxComputeWorkgroupsPerDimension:device.limits.maxComputeWorkgroupsPerDimension};
                        device.addEventListener('uncapturederror',event=>state.gpuErrors.push(event.error.message));
                        try{
                            state.result=await verifyFlowCollision(module,device,'/dist/flow-wgsl');
                            await device.queue.onSubmittedWorkDone();
                            if(state.gpuErrors.length)throw Error(state.gpuErrors.join('\\n'));
                            state.status='PASS';
                        }finally{device.destroy();}
                    })().catch(error=>Object.assign(state,{status:'FAIL',error:String(error),stack:error.stack,
                        partial:globalThis.flowCollisionProgress}));
                }''', artifact)
                page.wait_for_function("globalThis.collisionResult.status !== 'RUNNING'", timeout=180000)
                outcome = page.evaluate('globalThis.collisionResult')
                report.update({key: outcome[key] for key in ('result', 'partial', 'gpuErrors', 'adapter', 'deviceLimits') if key in outcome})
                report['sourceHashesAfter'] = file_hashes(paths)
                report['corpusHashesAfter'] = file_hashes(corpus_paths)
                assert outcome['status'] == 'PASS', outcome
                assert not report['pageErrors'] and not report['consoleErrors'] and not report['gpuErrors'], report
                assert report['sourceHashes'] == report['sourceHashesAfter'], 'Sources changed during collision acceptance'
                assert report['corpusHashes'] == report['corpusHashesAfter'], 'Shader corpus changed during collision acceptance'
                assert report['result']['flowCollisionAbi'] == 1 and report['result']['cleanup'] == 'PASS', report['result']
                report['status'] = 'PASS'
            finally:
                browser.close()
    except Exception as error:
        report.update(status='FAIL', error=str(error))
    finally:
        server.shutdown()
        server.server_close()
        output = ROOT / 'reports/flow-collision-browser.json'
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report.get(key) for key in ('status', 'unified', 'adapter', 'result', 'error', 'pageErrors', 'consoleErrors', 'gpuErrors')}, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
