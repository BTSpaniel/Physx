# SPDX-License-Identifier: MIT
"""Verify ordered native Flow GPU uploads and optional exact field differential."""
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

    def __init__(self, *args, baseline, candidate, **kwargs):
        self.baseline = baseline
        self.candidate = candidate
        super().__init__(*args, **kwargs)

    def translate_path(self, path):
        if path.split('?')[0] == '/baseline-flow.mjs':
            return str(self.baseline)
        if self.candidate and path.split('?')[0] == '/addons/flow/flow_host_webgpu.mjs':
            return str(self.candidate)
        return super().translate_path(path)

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
    parser.add_argument('--primitives-only', action='store_true')
    parser.add_argument('--repo', type=Path, default=Path('C:/Coding/game'))
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    args.baseline = args.baseline.resolve(strict=True)
    if args.candidate:
        args.candidate = args.candidate.resolve(strict=True)
    sys.path.insert(0, str(args.repo / 'tests/storage'))
    from performance_run_lock import performance_run_lock
    report = {'status': 'RUNNING', 'pageErrors': [], 'consoleErrors': [],
              'unified': args.unified, 'scope': 'primitives' if args.primitives_only else 'primitives-and-native-field-differential'}
    artifact = 'dist/candidate/physx-pe' if args.unified else 'dist/flow-host/flow-host'
    paths = [ROOT / f'{artifact}.mjs', ROOT / f'{artifact}.wasm', ROOT / 'addons/flow/pr_flow_host.cpp',
             ROOT / 'addons/flow/flow_host_webgpu.mjs', ROOT / 'addons/flow/upload_staging_oracle.mjs',
             Path(__file__), args.baseline, *sorted((ROOT / 'dist/flow-wgsl').rglob('*'))]
    if args.candidate:
        paths.append(args.candidate)
        report['candidatePath'] = str(args.candidate)
    paths = [path for path in paths if path.is_file()]
    hashes = lambda: {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(ROOT), baseline=args.baseline, candidate=args.candidate))
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
                page.evaluate(r"""({artifact,differential}) => {
                    globalThis.uploadResult = { status: 'RUNNING' };
                    (async () => {
                        const {default:init} = await import('/'+artifact+'.mjs');
                        const {verifyUploadStaging} = await import('/addons/flow/upload_staging_oracle.mjs');
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
                            const result=await verifyUploadStaging(module,device,{differential});
                            if(gpuErrors.length) throw Error(gpuErrors.join('\n'));
                            globalThis.uploadResult={status:'PASS',result,gpuErrors,adapter:adapterInfo};
                        } catch(error) {
                            globalThis.uploadResult={status:'FAIL',error:String(error),stack:error.stack,gpuErrors,adapter:adapterInfo};
                        } finally {device.destroy();}
                    })().catch(error=>globalThis.uploadResult={status:'FAIL',error:String(error),stack:error.stack});
                }""", {'artifact': artifact, 'differential': not args.primitives_only})
                page.wait_for_function("globalThis.uploadResult.status !== 'RUNNING'", timeout=240000)
                outcome = page.evaluate('globalThis.uploadResult')
                report.update(outcome)
                report['sourceHashesAfter'] = hashes()
                assert outcome['status'] == 'PASS', outcome
                assert not report['pageErrors'] and not report['consoleErrors'], report
                assert report['sourceHashes'] == report['sourceHashesAfter'], 'Sources changed during native acceptance'
                report['artifactHashes'] = {path.name: report['sourceHashes'][str(path)] for path in paths if str(path).startswith(str(ROOT / artifact))}
                report.update(status='PASS', browser=browser.version)
            finally:
                browser.close()
    except Exception as error:
        report.update(status='FAIL', error=str(error))
    finally:
        server.shutdown()
        server.server_close()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if not key.startswith('sourceHashes')}, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
