# SPDX-License-Identifier: MIT
"""Native boundary proof; no software/fallback adapter and no installed edits."""
import argparse
import hashlib
import json
import sys
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote
from playwright.sync_api import sync_playwright
from scene_browser_test import Handler, ROOT
from collision_browser_test import file_hashes
from component_evidence import validate_component, validate_unified, sha

SOURCES = tuple('addons/flow/'+name for name in (
    'pr_flow_host.cpp','build_host.py','generate_host_headers.py','flow_host_webgpu.mjs',
    'solid_geometry.h','solid_operators.h','PrSolidBoundary.hlsli','flow_solid_boundary.mjs',
    'PressureDivergenceCS.body.hlsli','PressureJacobiCS.body.hlsli','PressureSubtractCS.body.hlsli',
    'compile_solid_wgsl.py','solid_oracle.mjs','solid_browser_test.py','velocity_gather_oracle.mjs','flow_scalar_sources.mjs'))
NATIVE_SOURCES=tuple('addons/flow/'+name for name in ('pr_flow_host.cpp','solid_geometry.h','solid_operators.h'))
ADDON_SOURCES=tuple(path for path in SOURCES if path not in NATIVE_SOURCES)


class ProofHandler(Handler):
    # Thousands of shader fetches across isolated native contexts must reuse
    # connections instead of exhausting the Windows localhost ephemeral ports.
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        request_path=unquote(urlsplit(self.path).path)
        if request_path in getattr(self.server,'runtime_aliases',{}):
            path=self.server.runtime_aliases[request_path];data=path.read_bytes()
            self.server.served_runtime_hashes[request_path]=hashlib.sha256(data).hexdigest()
            self.send_response(200);self.send_header('Content-Type',self.guess_type(str(path)))
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        if request_path.startswith('/engine/') and getattr(self.server,'engine_root',None):
            path=(self.server.engine_root/request_path.lstrip('/')).resolve()
            if not path.is_relative_to(self.server.engine_root) or not path.is_file():
                self.send_error(404);return
            data=path.read_bytes();self.server.served_engine_hashes[request_path]=hashlib.sha256(data).hexdigest()
            self.send_response(200);self.send_header('Content-Type',self.guess_type(str(path)))
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        path=Path(self.translate_path(self.path)).resolve()
        if path.is_file() and path.is_relative_to(ROOT):
            data=path.read_bytes();relative=path.relative_to(ROOT).as_posix()
            self.server.served_hashes['/'+relative]=hashlib.sha256(data).hexdigest()
            self.send_response(200);self.send_header('Content-Type',self.guess_type(str(path)))
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        else:super().do_GET()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unified',action='store_true');parser.add_argument('--quick',action='store_true')
    parser.add_argument('--unified-runtime',type=Path,help='Explicit final physx-pe.mjs; requires a matched Flow component link manifest')
    parser.add_argument('--momentum',action='store_true',help='Run finite gas/rigid terminal exchange proof')
    parser.add_argument('--rebase',action='store_true',help='Run the native coordinate-rebase proof instead of solid cases')
    parser.add_argument('--engine-rebase',action='store_true',help='Also verify the prepared PhysX/Flow/FloatingOrigin transaction')
    parser.add_argument('--repo',type=Path,default=Path('C:/Coding/game'))
    parser.add_argument('--report',type=Path,default=ROOT/'reports/flow-solid-browser.json')
    args=parser.parse_args();sys.path.insert(0,str(args.repo/'tests/storage'))
    if args.unified_runtime:args.unified=True
    if args.momentum and args.rebase:parser.error('Choose momentum or rebase')
    if args.engine_rebase and not args.rebase:parser.error('--engine-rebase requires --rebase')
    from performance_run_lock import performance_run_lock
    artifact='dist/candidate/physx-pe' if args.unified else 'dist/flow-host/flow-host'
    addon='dist/flow-wgsl/addons/solid/manifest.json';corpus='dist/flow-wgsl/manifest.json'
    corpus_files=[addon,corpus]
    scalar='dist/flow-wgsl/addons/scalar/manifest.json'
    corpus_files.append(scalar)
    manifests=[addon,corpus,scalar]
    momentum='dist/flow-wgsl/addons/momentum/manifest.json'
    if (ROOT/momentum).exists():corpus_files.append(momentum);manifests.append(momentum)
    for manifest in manifests:
        for row in json.loads((ROOT/manifest).read_text())['shaders']:
            corpus_files.extend(['dist/flow-wgsl/'+row['wgsl'],'dist/flow-wgsl/'+row['reflection']])
    explicit_component=args.unified and (args.rebase or args.momentum or args.unified_runtime is not None)
    unified_loader=(args.unified_runtime or ROOT/'dist/candidate/physx-pe.mjs').resolve()
    files=(*SOURCES,*(() if explicit_component else (artifact+'.mjs',artifact+'.wasm')),*corpus_files)
    build_manifest_path=ROOT/Path(artifact).parent/'build-manifest.json'
    build_manifest=None
    component=None
    if explicit_component:
        component=validate_component(ROOT)
        files=tuple(dict.fromkeys((*files,'addons/flow/component_evidence.py','addons/flow/rebase_oracle.mjs','addons/flow/scalar_oracle.mjs',
            'dist/flow-component/manifest.json',component['hostBuildManifest']['path'],component['object']['path'],*component['inputs'])))
    elif args.rebase or args.momentum:
        build_manifest=json.loads(build_manifest_path.read_text())
        assert build_manifest['schema']=='flow-host-build-v1' and build_manifest['flowRebaseAbi']==1
        files=tuple(dict.fromkeys((*files,'addons/flow/rebase_oracle.mjs','addons/flow/scalar_oracle.mjs',build_manifest_path.relative_to(ROOT).as_posix(),*build_manifest['sourceHashes'])))
    if args.momentum:files=(*files,'addons/flow/momentum_oracle.mjs')
    if args.engine_rebase:files=(*files,'addons/flow/rebase_engine_oracle.mjs')
    report={'status':'RUNNING','unified':args.unified,'quick':args.quick,'flowSolidBoundaryAbi':1,'pageErrors':[],'consoleErrors':[],'gpuErrors':[],'requestFailures':[],'httpErrors':[]}
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(ProofHandler,directory=str(ROOT)))
    server.served_hashes={}
    server.runtime_aliases={}
    if explicit_component:
        for prefix in ('/dist/candidate/','/engine/sim/physics/'):
            for suffix in ('.mjs','.wasm'):server.runtime_aliases[prefix+'physx-pe'+suffix]=unified_loader.with_suffix(suffix)
    server.served_runtime_hashes={}
    server.engine_root=args.repo.resolve() if args.engine_rebase or args.momentum else None
    server.served_engine_hashes={}
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with performance_run_lock(args.repo),sync_playwright() as p:
            if explicit_component:
                report['unifiedAdmission']=validate_unified(ROOT,unified_loader)
            if build_manifest is not None:
                assert file_hashes(tuple(build_manifest['sourceHashes']))==build_manifest['sourceHashes'],'Flow rebase build inputs changed'
                for name,row in build_manifest['artifacts'].items():
                    path=build_manifest_path.parent/name
                    assert path.stat().st_size==row['size'] and hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256'],'Flow rebase native artifact changed'
            report['sourceHashes']=file_hashes(files);report['bridgeSources']=file_hashes(NATIVE_SOURCES)
            if args.engine_rebase or args.momentum:
                report['engineSourceHashes']={str(path.relative_to(args.repo)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in [args.repo/'engine/sim/FlowPhysXCollision.js',args.repo/'engine/sim/FlowMomentumExchange.js',args.repo/'engine/world/FloatingOrigin.js',args.repo/'engine/world/HierarchicalCoords.js']}
            report['artifactHashes']={Path(path).name:value for path,value in report['sourceHashes'].items() if path.startswith(artifact)}
            if explicit_component:report['artifactHashes']={name:row['sha256'] for name,row in report['unifiedAdmission']['artifacts'].items() if name in ('physx-pe.mjs','physx-pe.wasm')}
            report['bridgeSha256']=report['sourceHashes']['addons/flow/flow_host_webgpu.mjs'];report['hostSourceSha256']=report['sourceHashes']['addons/flow/pr_flow_host.cpp']
            report['corpusSha256']=report['sourceHashes'][corpus];report['addonSourceHashes']=file_hashes(ADDON_SOURCES)
            if args.unified:
                path=unified_loader.with_name('build-manifest.json') if explicit_component else ROOT/'dist/candidate/build-manifest.json'
                manifest=json.loads(path.read_text())
                report['nativeBuildManifestSha256']=sha(path)
                for source,digest in report['bridgeSources'].items():
                    assert manifest['bridge_sources'][source]==digest,'Native build source mismatch: '+source
            browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
            try:
                report['browser']=browser.version;page=browser.new_page()
                page.on('pageerror',lambda e:report['pageErrors'].append(str(e)))
                page.on('console',lambda m:report['consoleErrors'].append(m.text) if m.type=='error' else None)
                page.on('requestfailed',lambda request:report['requestFailures'].append({'url':request.url,'failure':request.failure}))
                page.on('response',lambda response:report['httpErrors'].append({'url':response.url,'status':response.status}) if response.status>=400 else None)
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.evaluate(r'''options=>{
                    const state=globalThis.solidResult={status:'RUNNING',gpuErrors:[]};
                    (async()=>{
                        const init=options.sharedEngine?null:(await import('/'+options.artifact+'.mjs')).default;
                        const {verifyFlowSolids}=await import('/addons/flow/solid_oracle.mjs');
                        const verify=options.momentum?(await import('/addons/flow/momentum_oracle.mjs')).verifyFlowMomentum:options.rebase?(await import('/addons/flow/rebase_oracle.mjs')).verifyFlowRebase:verifyFlowSolids;
                        const module=options.sharedEngine?await (await import('/engine/sim/physics/PhysXModule.js')).ensurePhysXModule():await init();
                        if(options.sharedEngine)globalThis.expectedUnifiedFlowModule=module;
                        state.sharedEngineModule=options.sharedEngine;
                        const adapter=await navigator.gpu.requestAdapter({powerPreference:'high-performance'});
                        const isFallbackAdapter=adapter?.info?.isFallbackAdapter??adapter?.isFallbackAdapter;
                        if(!adapter||isFallbackAdapter!==false)throw Error('Native nonfallback WebGPU adapter required');
                        state.adapter={vendor:adapter.info.vendor,architecture:adapter.info.architecture,device:adapter.info.device,description:adapter.info.description,isFallbackAdapter};
                        const device=await adapter.requestDevice({requiredFeatures:['float32-filterable'],requiredLimits:{maxComputeInvocationsPerWorkgroup:1024,maxComputeWorkgroupSizeX:1024,maxStorageBuffersPerShaderStage:11}});
                        device.addEventListener('uncapturederror',e=>state.gpuErrors.push(e.error.message));
                        try{state.result=await verify(module,device,'/dist/flow-wgsl',{quick:options.quick});
                            if(options.engineRebase)state.engineResult=await (await import('/addons/flow/rebase_engine_oracle.mjs')).verifyEngineRebase(module,device,'/dist/flow-wgsl');
                            await device.queue.onSubmittedWorkDone();
                            if(state.gpuErrors.length)throw Error(state.gpuErrors.join('\n'));state.status='PASS';
                        }finally{device.destroy();}
                    })().catch(e=>Object.assign(state,{status:'FAIL',error:String(e),stack:e.stack,partial:options.momentum?globalThis.flowMomentumProgress:options.rebase?globalThis.flowRebaseProgress:globalThis.flowSolidProgress}));
                }''',{'artifact':artifact,'quick':args.quick,'rebase':args.rebase,'engineRebase':args.engine_rebase,'momentum':args.momentum,
                       'sharedEngine':explicit_component and (args.engine_rebase or args.momentum)})
                page.wait_for_function("globalThis.solidResult.status!=='RUNNING'",timeout=300000)
                outcome=page.evaluate('globalThis.solidResult');report.update(outcome)
                report['bridgeSourcesAfter']=file_hashes(NATIVE_SOURCES);report['sourceHashesAfter']=file_hashes(files)
                report['addonSourceHashesAfter']=file_hashes(ADDON_SOURCES);report['servedSourceHashes']=dict(server.served_hashes)
                if explicit_component:
                    report['unifiedAdmissionAfter']=validate_unified(ROOT,unified_loader)
                    assert report['unifiedAdmission']==report['unifiedAdmissionAfter'],'Final unified/component bytes changed'
                    report['servedRuntimeHashes']=dict(server.served_runtime_hashes)
                    report['servedRuntimeHashesAfter']={name:sha(server.runtime_aliases[name]) for name in server.served_runtime_hashes}
                    assert report['servedRuntimeHashes']==report['servedRuntimeHashesAfter'],'Served unified runtime changed'
                    prefix='/engine/' if args.engine_rebase or args.momentum else '/dist/'
                    for name,path in server.runtime_aliases.items():
                        if name.startswith(prefix):
                            assert report['servedRuntimeHashes'].get(name)==sha(path),'Final pair was not executed on both native sides: '+name
                report['servedSourceHashesAfter']={'/'+name:digest for name,digest in file_hashes(tuple(path[1:] for path in report['servedSourceHashes'])).items()}
                if args.engine_rebase or args.momentum:
                    report['engineSourceHashesAfter']={name:hashlib.sha256((args.repo/name).read_bytes()).hexdigest() for name in report['engineSourceHashes']}
                    report['servedEngineHashes']=dict(server.served_engine_hashes)
                    report['servedEngineHashesAfter']={name:hashlib.sha256((args.repo/name.lstrip('/')).read_bytes()).hexdigest() for name in server.served_engine_hashes}
                    assert report['engineSourceHashes']==report['engineSourceHashesAfter'],'Engine coordinator source changed'
                    assert report['servedEngineHashes']==report['servedEngineHashesAfter'],'Served engine bytes changed'
                assert report['status']=='PASS',report.get('error')
                assert report['sourceHashes']==report['sourceHashesAfter'],'Source changed during native proof'
                assert report['servedSourceHashes']==report['servedSourceHashesAfter'],'Served bytes changed during native proof'
                for required in [artifact+'.mjs',artifact+'.wasm','addons/flow/flow_host_webgpu.mjs','addons/flow/flow_solid_boundary.mjs','addons/flow/flow_scalar_sources.mjs',*corpus_files]:
                    if explicit_component and (args.engine_rebase or args.momentum) and required.startswith(artifact):required='engine/sim/physics/'+Path(required).name
                    assert '/'+required in report['servedSourceHashes'] or '/'+required in server.served_runtime_hashes,'Required runtime file was not served: '+required
                assert not report['pageErrors'] and not report['consoleErrors'] and not report['gpuErrors'],report
                assert not report['requestFailures'] and not report['httpErrors'],report
            finally:browser.close()
    except Exception as error:report.update(status='FAIL',error=str(error))
    finally:
        server.shutdown();server.server_close();args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:report.get(key) for key in ('status','unified','quick','result','partial','error','gpuErrors')},indent=2,ensure_ascii=True))
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
