#!/usr/bin/env python3
"""Run the real game SPH solver against candidate WASM and shared-device Flow."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from serve import Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-root', type=Path, required=True)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--frames', type=int, default=60)
    parser.add_argument('--playground', action='store_true', help='Also capture the real visual demo')
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/sph-playground-port.json')
    args = parser.parse_args()
    game = args.game_root.resolve()
    if not (game / 'tests/playground/src/demos/sphFluid/simulation.js').is_file():
        parser.error('Game root does not contain the SPH playground')
    if not 1 <= args.frames <= 600:
        parser.error('Frames must be in 1..600')
    source_hashes = {}

    class MountedHandler(Handler):
        def log_message(self, *_args):
            pass

        def translate_path(self, path):
            url = unquote(urlsplit(path).path)
            if url.startswith('/port/'):
                resolved = (ROOT / url.removeprefix('/port/')).resolve()
                if not resolved.is_relative_to(ROOT) or any(
                    part.startswith('.') for part in resolved.relative_to(ROOT).parts
                ) or resolved.relative_to(ROOT).parts[0] not in ('addons', 'dist'):
                    return str(ROOT / '__forbidden__')
                return str(resolved)
            return super().translate_path(path)

        def send_head(self):
            path = Path(self.translate_path(self.path))
            if path.is_file() and path.suffix in ('.js', '.mjs', '.wasm', '.wgsl', '.json'):
                source_hashes[urlsplit(self.path).path] = hashlib.sha256(path.read_bytes()).hexdigest()
            return super().send_head()

        def do_GET(self):
            if urlsplit(self.path).path == '/__sph_port__':
                body = b'<!doctype html><title>SPH candidate integration</title>'
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(MountedHandler, directory=str(game)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {'started': datetime.now(timezone.utc).isoformat(), 'status': 'RUNNING',
              'gameRoot': str(game), 'browserFlags': [], 'progress': []}
    args.report.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    def console(message):
        if message.text.startswith('SPH port '):
            row = json.loads(message.text.removeprefix('SPH port '))
            report['progress'].append(row)
            print(message.text, flush=True)
            save()

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(args.browser), headless=True)
            try:
                report['browser'] = browser.version
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.on('console', console)
                origin = f'http://127.0.0.1:{server.server_port}'
                page.goto(origin + '/__sph_port__')
                report['integration'] = page.evaluate('''async frames => {
                    const factory = (await import('/port/dist/candidate/physx-pe.mjs')).default;
                    const module = await factory({locateFile: name => '/port/dist/candidate/' + name});
                    const {runSphPlaygroundSmoke} = await import('/port/addons/flow/sph_playground_smoke.mjs');
                    return runSphPlaygroundSmoke(module, {frames});
                }''', args.frames)
                if args.playground:
                    visual = {'pageErrors': [], 'consoleErrors': []}
                    report['playground'] = visual
                    page.on('pageerror', lambda error: visual['pageErrors'].append(str(error)))
                    page.on('console', lambda msg: visual['consoleErrors'].append(msg.text)
                            if msg.type == 'error' else None)
                    page.goto(origin + '/tests/playground/index.html?demo=sphfluid', wait_until='domcontentloaded')
                    try:
                        page.wait_for_function("document.querySelector('#stat-particles')?.textContent.includes('GPU-contained')", timeout=60000)
                    except Exception:
                        visual['failureText'] = page.locator('body').inner_text()
                        page.screenshot(path=str(args.report.with_suffix('.failed.png')))
                        raise
                    visual['initialText'] = page.locator('#stat-particles').text_content()
                    page.get_by_role('button', name='Vent shell', exact=True).click()
                    page.get_by_role('button', name='Seal shell', exact=True).wait_for(timeout=10000)
                    page.wait_for_timeout(5000)
                    visual['ventText'] = page.locator('#stat-particles').text_content()
                    contained = re.search(r'([\d,]+) GPU-contained', visual['ventText'])
                    if not visual['ventText'].startswith('65,536 simulated') or not contained \
                            or not 0 < int(contained[1].replace(',', '')) < 65536:
                        raise RuntimeError('Venting did not move contained particles into persistent runoff')
                    page.screenshot(path=str(args.report.with_suffix('.vent.png')))
                    page.get_by_role('button', name='R · Reset', exact=True).click()
                    page.get_by_role('button', name='Vent shell', exact=True).wait_for(timeout=10000)
                    page.get_by_role('button', name='Start inlet', exact=True).wait_for(timeout=10000)
                    page.wait_for_timeout(3000)
                    visual['resetText'] = page.locator('#stat-particles').text_content()
                    if not visual['resetText'].startswith('65,536 simulated') \
                            or '65,536 GPU-contained' not in visual['resetText']:
                        raise RuntimeError('Reset did not restore the default particle population')
                    visual['controlsPassed'] = ['vent opens', 'vent drains without deleting particles',
                        'reset seals', 'reset holds inlet', 'reset restores contained population']
                    screenshot = args.report.with_suffix('.png')
                    page.screenshot(path=str(screenshot))
                    visual['screenshot'] = str(screenshot)
                    report['playground'] = visual
                    if visual['pageErrors'] or visual['consoleErrors']:
                        raise RuntimeError('Visual playground reported browser errors; see receipt')
                report['status'] = 'SPH_PLAYGROUND_PORT_INTEGRATION_PASSED'
            finally:
                browser.close()
    except Exception as error:
        report.update(status='FAILED', error=str(error))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        report['servedSourceHashes'] = dict(sorted(source_hashes.items()))
        report['finished'] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({'status': report['status'], 'report': str(args.report), 'error': report.get('error')}))
    return 0 if report['status'] == 'SPH_PLAYGROUND_PORT_INTEGRATION_PASSED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
