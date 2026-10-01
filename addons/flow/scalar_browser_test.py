# SPDX-License-Identifier: MIT
"""Actual native Flow scalar-source admission, receipts and atlas-integral proof."""
import argparse
import json
import sys
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright
from scene_browser_test import ROOT
from collision_browser_test import file_hashes
from solid_browser_test import ProofHandler, SOURCES as SOLID_SOURCES

SCALAR_FILES = tuple('addons/flow/' + name for name in (
    'scalar_sources_types.h', 'scalar_sources_impl.h', 'PrScalarCommon.hlsli',
    'PrScalarAdmitCS.hlsl', 'PrScalarCountCS.hlsl', 'PrScalarApplyCS.hlsl',
    'PrScalarReceiptCS.hlsl', 'compile_scalar_wgsl.py', 'scalar_oracle.mjs',
    'scalar_browser_test.py', 'flow_scalar_sources.mjs'))
SOURCES = tuple(dict.fromkeys((*SOLID_SOURCES, *SCALAR_FILES)))
ADDON_SOURCES = SOURCES
NATIVE_SOURCES = tuple('addons/flow/' + name for name in (
    'pr_flow_host.cpp', 'solid_geometry.h', 'solid_operators.h',
    'scalar_sources_types.h', 'scalar_sources_impl.h'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unified', action='store_true')
    parser.add_argument('--repo', type=Path, default=Path('C:/Coding/game'))
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/flow-scalar-browser.json')
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo / 'tests/storage'))
    from performance_run_lock import performance_run_lock
    artifact = 'dist/candidate/physx-pe' if args.unified else 'dist/flow-host/flow-host'
    manifests = ('dist/flow-wgsl/manifest.json', 'dist/flow-wgsl/addons/solid/manifest.json',
                 'dist/flow-wgsl/addons/scalar/manifest.json')
    corpus_files = list(manifests)
    for manifest in manifests:
        for row in json.loads((ROOT / manifest).read_text())['shaders']:
            corpus_files.extend(('dist/flow-wgsl/' + row['wgsl'], 'dist/flow-wgsl/' + row['reflection']))
    files = tuple(dict.fromkeys((*SOURCES, artifact + '.mjs', artifact + '.wasm', *corpus_files)))
    report = {'status': 'RUNNING', 'unified': args.unified, 'quick': False, 'flowScalarSourceAbi': 1,
              'flowScalarShaderModules': 4, 'flowScalarSourceMode': 'finite-additive-with-receipts',
              'startedUtc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Actual nonfallback GPU native scalar admission and integrated atlas change; no presentation or FPS claim',
              'pageErrors': [], 'consoleErrors': [], 'gpuErrors': [], 'requestFailures': [], 'httpErrors': []}
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(ProofHandler, directory=str(ROOT)))
    server.served_hashes = {}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with performance_run_lock(args.repo), sync_playwright() as playwright:
            report['sourceHashes'] = file_hashes(files)
            report['bridgeSources'] = file_hashes(NATIVE_SOURCES)
            report['artifactHashes'] = {Path(path).name: value for path, value in report['sourceHashes'].items()
                                        if path.startswith(artifact)}
            report['bridgeSha256'] = report['sourceHashes']['addons/flow/flow_host_webgpu.mjs']
            report['hostSourceSha256'] = report['sourceHashes']['addons/flow/pr_flow_host.cpp']
            report['corpusSha256'] = report['sourceHashes'][manifests[0]]
            report['addonSourceHashes'] = file_hashes(ADDON_SOURCES)
            report['scalarManifestSha256'] = report['sourceHashes'][manifests[2]]
            if args.unified:
                build_path = 'dist/candidate/build-manifest.json'
                build = json.loads((ROOT / build_path).read_text())
                report['nativeBuildManifestSha256'] = file_hashes((build_path,))[build_path]
                for source, digest in report['bridgeSources'].items():
                    assert build['bridge_sources'].get(source) == digest, 'Native build source mismatch: ' + source
            browser = playwright.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
            try:
                report['browser'] = browser.version
                page = browser.new_page()
                page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
                page.on('console', lambda message: report['consoleErrors'].append(message.text) if message.type == 'error' else None)
                page.on('requestfailed', lambda request: report['requestFailures'].append({'url': request.url, 'failure': request.failure}))
                page.on('response', lambda response: report['httpErrors'].append({'url': response.url, 'status': response.status}) if response.status >= 400 else None)
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.evaluate(r'''options => {
                    const state=globalThis.scalarResult={status:'RUNNING',gpuErrors:[]};
                    (async()=>{
                        const {default:init}=await import('/'+options.artifact+'.mjs');
                        const {verifyScalarSources}=await import('/addons/flow/scalar_oracle.mjs');
                        const module=await init(),adapter=await navigator.gpu.requestAdapter({powerPreference:'high-performance'});
                        const isFallbackAdapter=adapter?.info?.isFallbackAdapter??adapter?.isFallbackAdapter;
                        if(!adapter||isFallbackAdapter!==false)throw Error('Native nonfallback WebGPU adapter required');
                        state.adapter={vendor:adapter.info.vendor,architecture:adapter.info.architecture,
                            device:adapter.info.device,description:adapter.info.description,isFallbackAdapter};
                        state.nativeContextsBefore=module._pr_flow_host_live();
                        const device=await adapter.requestDevice({requiredFeatures:['float32-filterable'],requiredLimits:{
                            maxComputeInvocationsPerWorkgroup:1024,maxComputeWorkgroupSizeX:1024,maxStorageBuffersPerShaderStage:11}});
                        device.addEventListener('uncapturederror',event=>state.gpuErrors.push(event.error.message));
                        try{
                            state.result=await verifyScalarSources(module,device,'/dist/flow-wgsl');
                            state.cleanup=state.result.cleanup;
                            await device.queue.onSubmittedWorkDone();
                            if(state.result.status!=='PASS')throw Error(state.result.error||'Scalar oracle failed');
                            if(state.gpuErrors.length)throw Error(state.gpuErrors.join('\n'));
                            state.status='PASS';
                        }finally{state.nativeContextsAfter=module._pr_flow_host_live();device.destroy();}
                    })().catch(error=>Object.assign(state,{status:'FAIL',error:String(error),stack:error.stack,
                        partial:globalThis.flowScalarProgress}));
                }''', {'artifact': artifact})
                page.wait_for_function("globalThis.scalarResult.status!=='RUNNING'", timeout=300000)
                report.update(page.evaluate('globalThis.scalarResult'))
                report['sourceHashesAfter'] = file_hashes(files)
                report['addonSourceHashesAfter'] = file_hashes(ADDON_SOURCES)
                report['bridgeSourcesAfter'] = file_hashes(NATIVE_SOURCES)
                report['servedSourceHashes'] = dict(server.served_hashes)
                report['servedSourceHashesAfter'] = {'/' + path: digest for path, digest in file_hashes(
                    tuple(path[1:] for path in report['servedSourceHashes'])).items()}
                assert report['status'] == 'PASS', report.get('error')
                assert report['sourceHashes'] == report['sourceHashesAfter'], 'Source changed during scalar proof'
                assert report['servedSourceHashes'] == report['servedSourceHashesAfter'], 'Served bytes changed during scalar proof'
                required = (artifact + '.mjs', artifact + '.wasm', 'addons/flow/flow_host_webgpu.mjs',
                            'addons/flow/flow_solid_boundary.mjs', 'addons/flow/flow_scalar_sources.mjs', 'addons/flow/scalar_oracle.mjs', *corpus_files)
                for path in required:
                    assert '/' + path in report['servedSourceHashes'], 'Runtime dependency was not actually served: ' + path
                assert report['nativeContextsAfter'] == report['nativeContextsBefore'], 'Native scalar contexts leaked'
                assert not any(report[key] for key in ('pageErrors', 'consoleErrors', 'gpuErrors', 'requestFailures', 'httpErrors')), report
            finally:
                browser.close()
    except Exception as error:
        report.update(status='FAIL', error=str(error))
    finally:
        report.setdefault('servedSourceHashes', dict(server.served_hashes))
        report['finishedUtc'] = datetime.now(timezone.utc).isoformat()
        server.shutdown()
        server.server_close()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report.get(key) for key in ('status', 'unified', 'result', 'partial', 'error', 'gpuErrors')}, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
