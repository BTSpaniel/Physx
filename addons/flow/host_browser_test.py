# SPDX-License-Identifier: MIT
"""Execute the actual NVIDIA Flow host graph on Chrome WebGPU."""
import json
import argparse
import hashlib
import threading
import sys
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from flow_gpu_probe import launch_options, launch_test_browser, PROBE_JS
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--unified', action='store_true')
parser.add_argument('--browser-executable', type=Path)
parser.add_argument('--browser-engine', choices=['chromium', 'firefox'], default='chromium')
mode = parser.add_mutually_exclusive_group()
mode.add_argument('--hardware', action='store_true')
mode.add_argument('--software-vulkan', action='store_true')
parser.add_argument('--lavapipe-icd', type=Path)
args = parser.parse_args()
report = {'status': 'RUNNING', 'errors': [], 'consoleErrors': []}


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, '.mjs': 'text/javascript', '.wasm': 'application/wasm'}
    def log_message(self, *_):
        pass
    def do_GET(self):
        if self.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()


server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(ROOT)))
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with sync_playwright() as p:
        options, metadata = launch_options(args.browser_executable, args.hardware, args.software_vulkan, args.lavapipe_icd, args.browser_engine)
        report.update(metadata)
        with launch_test_browser(p, args.browser_engine, options, report) as (browser, pages):
            report['browserVersion'] = browser.version
            page = pages.new_page()
            page.on('pageerror', lambda e: report['errors'].append(str(e)))
            page.on('console', lambda m: report['consoleErrors'].append(m.text) if m.type == 'error' else None)
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            report['capability'] = page.evaluate(PROBE_JS, {
                'expectedBackend': metadata['requestedBackend']})
            report['backendIdentityVerified'] = report['capability'].get('backendIdentityVerified', False)
            if report['capability']['status'] != 'PASS':
                raise RuntimeError('Required Flow capability failed: ' + json.dumps(report['capability']))
            report['result'] = page.evaluate(r'''async unified => {
                const {default: init}=await import(unified?'/dist/candidate/physx-pe.mjs':'/dist/flow-host/flow-host.mjs');
                const {FlowHostWebGpu}=await import('/addons/flow/flow_host_webgpu.mjs');
                const module=await init();
                const adapter=await navigator.gpu.requestAdapter({powerPreference:'high-performance'});
                if(!adapter)throw Error('No GPU adapter');
                const device=await adapter.requestDevice({requiredFeatures:['float32-filterable'],requiredLimits:{maxComputeInvocationsPerWorkgroup:1024,maxComputeWorkgroupSizeX:1024}});
                const errors=[];device.addEventListener('uncapturederror',e=>errors.push(e.error.message));
                let host;
                try {
                    host=await FlowHostWebGpu.create(module,device,'/dist/flow-wgsl');
                    for(let i=0;i<120;i++)await host.step(1/60);
                    const probes=new Float32Array([0,0,0,0,.2,0,0,.5,0,0,1,0,1000,1000,1000]);
                    const velocity=await host.sampleVelocity(probes);
                    if(!velocity.every(Number.isFinite)||!velocity.some(n=>Math.abs(n)>.001)||velocity.slice(-3).some(n=>n!==0))throw Error('World velocity gather failed '+velocity);
                    const {verifyVelocityGather}=await import('/addons/flow/velocity_gather_oracle.mjs');
                    const gatherOracle=await verifyVelocityGather(host);
                    const texture=host.output?.density;
                    if(!texture)throw Error('No density output');
                    const [w,h,d]=texture.size, pitch=Math.ceil(w*16/256)*256;
                    const read=device.createBuffer({size:pitch*h*d,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ});
                    try {
                        const encoder=device.createCommandEncoder();
                        encoder.copyTextureToBuffer({texture:texture.texture},{buffer:read,bytesPerRow:pitch,rowsPerImage:h},[w,h,d]);
                        device.queue.submit([encoder.finish()]);await read.mapAsync(GPUMapMode.READ);
                        const values=new Float32Array(read.getMappedRange());let sum=0,max=0;
                        for(const n of values){if(!Number.isFinite(n))throw Error('Nonfinite Flow voxel');sum+=Math.abs(n);max=Math.max(max,Math.abs(n));}
                        read.unmap();
                        if(!(sum>0))throw Error('Flow emitted no density');
                        if(errors.length)throw Error(errors.join('\n'));
                        const emitted={stats:structuredClone(host.stats),density:{sum,max,dimensions:texture.size},worldVelocity:Array.from(velocity),gatherOracle};
                        for(const name of ['PressureDivergenceCS','PressureJacobiCS','PressureSubtractCS','AdvectionDensity2CS','Vorticity2CS','SummaryCS'])
                            if(!Object.entries(host.stats.passes).some(([key,count])=>key.includes(name)&&count>0))throw Error('Missing coupled pass '+name);
                        host.setEmitter({enabled:false});
                        for(let i=0;i<24;i++)await host.step(1/60);
                        const other=await FlowHostWebGpu.create(module,device,'/dist/flow-wgsl',{maxBlocks:16});
                        try {
                            other.setEmitter({enabled:false});other.setControls({pressure:false,combustion:false,vorticity:0});
                            await other.step();
                            if(other.stats.activeBlocks!==0||host.stats.frames!==144)throw Error('Flow contexts are not isolated');
                            if(module._pr_flow_host_live()!==2)throw Error('Native Flow ownership differs');
                        }finally{await other.dispose();}
                        await host.dispose();
                        if(module._pr_flow_host_live()!==0||host.resources.size||host.stats.allocatedBytes!==0)throw Error('Flow resources leaked');
                        const probe=device.createBuffer({size:4,usage:GPUBufferUsage.COPY_DST});probe.destroy();
                        return {...emitted,cleanup:'PASS',isolatedContexts:'PASS',emitterStoppedFrames:24};
                    }finally{read.destroy();}
                }catch(error){throw Error(String(error)+' stats='+JSON.stringify(host?.stats));}
                finally {await host?.dispose();device.destroy();}
            }''', args.unified)
            report['unified'] = args.unified
            report['bridgeSha256'] = hashlib.sha256((ROOT / 'addons/flow/flow_host_webgpu.mjs').read_bytes()).hexdigest()
            report['hostSourceSha256'] = hashlib.sha256((ROOT / 'addons/flow/pr_flow_host.cpp').read_bytes()).hexdigest()
            manifest = json.loads((ROOT / 'dist/candidate/build-manifest.json').read_text()) if args.unified else {}
            report['artifactHashes'] = manifest.get('artifacts', {})
            for name, expected in report['artifactHashes'].items():
                artifact = ROOT / 'dist/candidate' / name
                if artifact.stat().st_size != expected['bytes'] or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected['sha256']:
                    raise RuntimeError('Matched Flow artifact changed: ' + name)
            report['gpuMode'] = metadata['gpuMode']
            report['status'] = 'PASS' if not report['errors'] and not report['consoleErrors'] else 'FAIL'
except Exception as e:
    report.update(status='FAIL', error=str(e))
finally:
    server.shutdown()
    server.server_close()
    (ROOT / 'reports/flow-host-browser.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report, indent=2))
raise SystemExit(0 if report['status'] == 'PASS' else 1)
