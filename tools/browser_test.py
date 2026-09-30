#!/usr/bin/env python3
"""Run REAL PhysX smoke tests in Chromium. Missing binaries never count as a pass.

Every failure, including browser launch and navigation policy failures, produces
machine-readable evidence. This script never changes the browser's URL policy.
"""
from __future__ import annotations
import argparse,json,shutil,sys,threading
from pathlib import Path
from serve import ROOT,create_server
import physx_lab as lab
import evidence

def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile',choices=['baseline','candidate'],required=True)
    p.add_argument('--chromium',default=shutil.which('chromium') or shutil.which('google-chrome'))
    p.add_argument('--headed',action='store_true')
    p.add_argument('--no-sandbox',action='store_true',help='Only for a trusted container without sandbox support')
    a=p.parse_args()
    report={'profile':a.profile,'status':'NOT_RUN','tests':[], 'physicsExecuted':False,
        'engineIntegrationVerified':False,'releaseApproved':False}
    server=None;thread=None
    errors=[]
    try:
        # Explicit preflight avoids confusing an absent SDK with a browser pass.
        source=ROOT/'dist'/a.profile
        manifest=evidence.object_json(source/'build-manifest.json')
        evidence.verify_artifacts(manifest,source,a.profile)
        harness=evidence.source_hashes()
        for name in manifest['artifacts']:
            meta=manifest['artifacts'][name]
            if lab.sha256(source/name)!=meta['sha256'] or (source/name).stat().st_size!=meta['bytes']:
                raise ValueError('Staged artifact hash/size mismatch: '+name)
        from playwright.sync_api import sync_playwright
        server=create_server();thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        with sync_playwright() as pw:
            options={'headless':not a.headed}
            if a.chromium:options['executable_path']=a.chromium
            if a.no_sandbox:options['args']=['--no-sandbox']
            browser=pw.chromium.launch(**options)
            page=browser.new_page(viewport={'width':1280,'height':1100})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/web/index.html',timeout=30000)
            page.wait_for_function('typeof window.runValidation === "function"')
            report=page.evaluate('(profile)=>window.runValidation(profile)',a.profile)
            report['browser']=browser.version
            report['crossOriginIsolated']=page.evaluate('crossOriginIsolated')
            report['artifactHashes']=manifest['artifacts']
            report['testHarnessSha256']=harness
            if harness!=evidence.source_hashes():
                raise ValueError('Test sources changed during execution')
            if errors:report['status']='FAILED'
            (ROOT/'reports').mkdir(exist_ok=True)
            page.screenshot(path=str(ROOT/'reports'/f'{a.profile}-browser.png'),full_page=True)
            browser.close()
    except Exception as exc:
        report.update(status='BLOCKED_OR_FAILED',error=str(exc))
    finally:
        if server:server.shutdown();server.server_close()
        if thread:thread.join(timeout=5)
        report['pageErrors']=errors
        if report.get('status')=='SMOKE_PASSED_NOT_RELEASE_CERTIFIED':
            try:evidence.validate_report(report,manifest,harness,a.profile)
            except (ValueError,KeyError) as exc:report.update(status='FAILED',error=str(exc))
        lab.write_json(ROOT/'reports'/f'{a.profile}-browser.json',report)
    print(json.dumps(report,indent=2))
    return 0 if report['status']=='SMOKE_PASSED_NOT_RELEASE_CERTIFIED' and report.get('physicsExecuted') and not errors else 2

if __name__=='__main__':raise SystemExit(main())
