#!/usr/bin/env python3
"""Run the compiled Rust/C++ ABI probe in a real browser; contains NO physics.

Optional development dependency: Python Playwright. The engine does not need it.
The test uses normal loopback HTTP and never disables browser URL/access policy.
"""
from __future__ import annotations
import argparse, json, shutil, sys, threading
from pathlib import Path
from serve import ROOT, create_server
import physx_lab as lab


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium', default=shutil.which('chromium') or shutil.which('google-chrome'))
    parser.add_argument('--no-sandbox', action='store_true')
    args=parser.parse_args()
    report={'scope':'real Rust/C++ WebAssembly ABI probe only', 'physicsExecuted':False,
            'status':'NOT_RUN', 'checks':0}
    browser=None; server=None; thread=None
    try:
        source=ROOT/'dist/abi'
        manifest=json.loads((source/'build-manifest.json').read_text())
        for name in ('abi-probe.mjs','abi-probe.wasm'):
            meta=manifest['artifacts'][name]
            if lab.sha256(source/name)!=meta['sha256'] or (source/name).stat().st_size!=meta['bytes']:
                raise ValueError('ABI artifact hash/size mismatch: '+name)
        from playwright.sync_api import sync_playwright
        server=create_server();thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        with sync_playwright() as pw:
            opts={'headless':True}
            if args.chromium:opts['executable_path']=args.chromium
            if args.no_sandbox:opts['args']=['--no-sandbox']
            browser=pw.chromium.launch(**opts)
            page=browser.new_page(); errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}/web/index.html',timeout=30000)
            result=page.evaluate('''async() => {
              const root=new URL('../dist/abi/', location.href);
              const manifest=await(await fetch(new URL('build-manifest.json',root),{cache:'no-store'})).json();
              const {digest,instantiateVerified}=await import('./suite.mjs');
              const data={};
              for (const name of ['abi-probe.mjs','abi-probe.wasm']) {
                const response=await fetch(new URL(name,root),{cache:'no-store'});
                if (!response.ok) throw new Error('ABI artifact fetch failed');
                const bytes=new Uint8Array(await response.arrayBuffer());
                const meta=manifest.artifacts[name];
                if (bytes.length!==meta.bytes || await digest(bytes)!==meta.sha256)
                  throw new Error('ABI artifact hash/size mismatch');
                data[name]=bytes;
              }
              const stderr=[];
              const m=await instantiateVerified({base:root,loaderBytes:data['abi-probe.mjs'],
                wasmBinary:data['abi-probe.wasm']},{printErr:(...args)=>stderr.push(args.join(' '))});
              const failure=m._pr_abi_selftest(),checks=m._pr_abi_checks();
              return {failure,checks,stderr,seed:m._pr_abi_seed(),step:m._pr_abi_step()};
            }''')
            report.update(result)
            report.update(browser=browser.version,pageErrors=errors,artifactHashes=manifest['artifacts'])
            report['status']='ABI_PASSED_NOT_PHYSICS' if result['failure']==0 and result['checks']>0 and not errors and not result['stderr'] else 'FAILED'
            browser.close();browser=None
    except Exception as exc:
        report.update(status='BLOCKED_OR_FAILED',error=str(exc))
    finally:
        if server:server.shutdown();server.server_close()
        if thread:thread.join(timeout=5)
        lab.write_json(ROOT/'reports/abi-browser.json',report)
    print(json.dumps(report,indent=2))
    return 0 if report['status']=='ABI_PASSED_NOT_PHYSICS' else 2

if __name__=='__main__':raise SystemExit(main())
