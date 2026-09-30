# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Verify a newly extracted runtime archive through its shipped browser routes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import threading
import zipfile
from pathlib import Path

import physx_lab as lab
from serve import create_server


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    report = {'status': 'RUNNING', 'scope': 'Extracted runtime archive inventory and actual PhysX browser regression suite',
              'pageErrors': [], 'archiveSha256': lab.sha256(args.archive)}
    server = thread = None
    try:
        with tempfile.TemporaryDirectory(prefix='physx-pe-archive-') as temp:
            target = Path(temp)
            with zipfile.ZipFile(args.archive) as bundle:
                names = bundle.namelist()
                if len(names) != len(set(names)) or any(
                        name.startswith('/') or '\\' in name or '..' in Path(name).parts
                        or not (target / name).resolve().is_relative_to(target.resolve()) for name in names):
                    raise ValueError('Invalid archive path inventory')
                bundle.extractall(target)
            manifest = json.loads((target / 'runtime-manifest.json').read_text())
            if set(manifest['files']) | {'runtime-manifest.json'} != set(names):
                raise ValueError('Archive files differ from the matched manifest inventory')
            for name, expected in manifest['files'].items():
                data = (target / name).read_bytes()
                if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
                    raise ValueError('Extracted archive bytes differ: ' + name)
            from playwright.sync_api import sync_playwright
            server = create_server(root=target)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/web/index.html')
                    page.wait_for_function('typeof window.runValidation === "function"')
                    result = page.evaluate('window.runValidation("candidate")')
                    if result.get('status') != 'SMOKE_PASSED_NOT_RELEASE_CERTIFIED' or not result.get('physicsExecuted'):
                        raise ValueError('Extracted browser validation failed: ' + json.dumps(result))
                    if report['pageErrors'] or len(result.get('tests', [])) != 23:
                        raise ValueError('Extracted browser checks are incomplete or errored')
                    page.goto(f'http://127.0.0.1:{server.server_port}/web/quickstart.html')
                    page.wait_for_function('window.quickstartState?.status === "FAILED" || window.quickstartState?.time >= 0.2')
                    falling = page.evaluate('window.quickstartState')
                    if falling.get('runtimeVersion') != '5.11.0' or not 0.25 < falling.get('y', 0) < 3:
                        raise ValueError('Extracted quick start did not execute native free fall: ' + json.dumps(falling))
                    page.click('#pause')
                    page.wait_for_function('["PAUSED", "FAILED"].includes(window.quickstartState?.status)')
                    paused = page.evaluate('window.quickstartState')
                    page.wait_for_timeout(200)
                    if paused['status'] != 'PAUSED' or page.evaluate('window.quickstartState.time') != paused['time']:
                        raise ValueError('Quick-start pause did not hold the native simulation')
                    page.click('#pause')
                    page.wait_for_function('window.quickstartState?.status === "FAILED" || window.quickstartState?.time >= 6', timeout=15000)
                    resting = page.evaluate('window.quickstartState')
                    if resting['status'] != 'RUNNING' or not 0.23 < resting.get('y', 0) < 0.32 or abs(resting.get('velocityY', 1)) > 0.1:
                        raise ValueError('Quick-start sphere did not settle on its native floor: ' + json.dumps(resting))
                    screenshot = lab.ROOT / 'reports/quickstart.png'
                    page.screenshot(path=str(screenshot), full_page=True)
                    page.click('#reset')
                    page.wait_for_function('(generation) => window.quickstartState?.status === "FAILED" || (window.quickstartState?.generation > generation && window.quickstartState?.time >= 0.05)', arg=resting['generation'])
                    restarted = page.evaluate('window.quickstartState')
                    if restarted['status'] != 'RUNNING' or restarted['y'] < 2.5:
                        raise ValueError('Quick-start reset did not create a fresh scene')
                    page.click('#stop')
                    page.wait_for_function('["CLOSED", "FAILED"].includes(window.quickstartState?.status)')
                    closed = page.evaluate('window.quickstartState')
                    if closed['status'] != 'CLOSED' or closed.get('ownedWrappers') != 0 or report['pageErrors']:
                        raise ValueError('Quick-start scene teardown failed: ' + json.dumps(closed))
                    report.update(status='PASS', browser=browser.version,
                                  inventoryFiles=len(names), tests=result['tests'],
                                  quickstart={'status': 'PASS', 'falling': falling, 'paused': paused,
                                              'resting': resting, 'restarted': restarted, 'closed': closed,
                                              'screenshotSha256': lab.sha256(screenshot)})
                finally:
                    browser.close()
    except Exception as exc:
        report.update(status='FAIL', error=str(exc))
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=5)
        lab.write_json(lab.ROOT / 'reports/package-smoke.json', report)
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
