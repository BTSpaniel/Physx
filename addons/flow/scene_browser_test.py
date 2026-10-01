# SPDX-License-Identifier: MIT
"""Verify actual upstream Flow sphere/box scenes on native Chrome WebGPU."""
import argparse
import hashlib
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, '.mjs': 'text/javascript', '.wasm': 'application/wasm'}

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
        else:
            super().do_GET()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unified', action='store_true')
    parser.add_argument('--repo', type=Path, default=Path('C:/Coding/game'))
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo / 'tests/storage'))
    from performance_run_lock import performance_run_lock
    report = {'status': 'RUNNING', 'pageErrors': [], 'consoleErrors': [], 'unified': args.unified}
    artifact = 'dist/candidate/physx-pe' if args.unified else 'dist/flow-host/flow-host'
    paths = [f'{artifact}.mjs', f'{artifact}.wasm', 'addons/flow/pr_flow_host.cpp',
             'addons/flow/flow_host_webgpu.mjs', 'addons/flow/scene_oracle.mjs',
             'addons/flow/scene_browser_test.py', 'dist/flow-wgsl/manifest.json']
    hashes = lambda: {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with performance_run_lock(args.repo), sync_playwright() as p:
            report['sourceHashes'] = hashes()
            browser = p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
            try:
                page = browser.new_page()
                page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
                page.on('console', lambda message: report['consoleErrors'].append(message.text) if message.type == 'error' else None)
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.evaluate('''artifact => {
                    globalThis.sceneResult = { status: 'RUNNING' };
                    (async () => {
                        const {default:init} = await import('/'+artifact+'.mjs');
                        const {verifyFlowScene} = await import('/addons/flow/scene_oracle.mjs');
                        const module = await init();
                        const adapter = await navigator.gpu.requestAdapter({powerPreference:'high-performance'});
                        const isFallbackAdapter=adapter?.info?.isFallbackAdapter ?? adapter?.isFallbackAdapter;
                        if(!adapter || isFallbackAdapter !== false) throw Error('A confirmed non-fallback native WebGPU adapter is required');
                        const adapterInfo={vendor:adapter.info.vendor,architecture:adapter.info.architecture,
                            device:adapter.info.device,description:adapter.info.description,isFallbackAdapter};
                        const device = await adapter.requestDevice({requiredFeatures:['float32-filterable'],
                            requiredLimits:{maxComputeInvocationsPerWorkgroup:1024,maxComputeWorkgroupSizeX:1024}});
                        const gpuErrors=[];device.addEventListener('uncapturederror',event=>gpuErrors.push(event.error.message));
                        try {
                            const result=await verifyFlowScene(module,device,'/dist/flow-wgsl');
                            if(gpuErrors.length) throw Error(gpuErrors.join('\\n'));
                            globalThis.sceneResult={status:'PASS',result,gpuErrors,adapter:adapterInfo};
                        } finally {device.destroy();}
                    })().catch(error=>globalThis.sceneResult={status:'FAIL',error:String(error),stack:error.stack});
                }''', artifact)
                page.wait_for_function("globalThis.sceneResult.status !== 'RUNNING'", timeout=180000)
                outcome = page.evaluate('globalThis.sceneResult')
                report['result'] = outcome.get('result', outcome)
                assert outcome['status'] == 'PASS', outcome
                report['gpuErrors'] = outcome['gpuErrors']
                report['adapter'] = outcome['adapter']
                assert not report['pageErrors'] and not report['consoleErrors'], report
                assert report['sourceHashes'] == hashes(), 'Sources changed during native acceptance'
                report['flowSceneAbi'] = report['result']['flowSceneAbi']
                report['artifactHashes'] = {Path(path).name: digest for path, digest in report['sourceHashes'].items() if path.startswith(artifact)}
                report['bridgeSha256'] = report['sourceHashes']['addons/flow/flow_host_webgpu.mjs']
                report['hostSourceSha256'] = report['sourceHashes']['addons/flow/pr_flow_host.cpp']
                report.update(status='PASS', browser=browser.version)
            finally:
                browser.close()
    except Exception as error:
        report.update(status='FAIL', error=str(error))
    finally:
        server.shutdown()
        server.server_close()
        path = ROOT / 'reports/flow-scene-browser.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
