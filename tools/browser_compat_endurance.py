# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Separate native browser/endurance evidence, serialized on the host test lock.

CPU runs use the current frozen 37-case harness and a private endurance Worker.
GPU runs use a verified delivered ZIP snapshot plus unchanged existing Flow tests.
The shared host lock helper is imported, never copied into this MIT distribution.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import importlib.util
from importlib.metadata import version
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import uuid
import zipfile
import zlib
from datetime import datetime, timezone, timedelta
from pathlib import Path, PurePosixPath

import evidence
from serve import ROOT, create_server

ENDURANCE_CASES = ('Repeated native scenes, actors, bulk contexts and Blast ownership',
                   'Fixed physical-time replay remains finite and repeatable',
                   'Bounded endurance native teardown')
ENDURANCE_SOURCES = tuple(evidence.HARNESS) + ('web/endurance-regressions.mjs',
    'tools/browser_compat_endurance.py', 'release.py', 'tests/test_endurance_admission.py')
ZERO_ADDONS = {'blastFamilies': 0, 'blastAuthoringResults': 0, 'flowHosts': 0}
HISTORICAL_DELIVERED_VERSION = '5.11.0-alpha.2'
DELIVERED_TEST_HELPERS = ('tools/flow_gpu_probe.py', 'tools/serve.py', 'addons/flow/host_browser_test.py',
    'addons/flow/wgsl_browser_test.py', 'addons/flow/velocity_gather_oracle.mjs',
    'addons/flow/advection_smoke.mjs', 'addons/flow/mesh_scan_smoke.mjs',
    'addons/flow/pr_flow_host.cpp')


def validate_configuration(cycles: int, physical_seconds: int, release_phase: bool) -> None:
    evidence.require(type(cycles) is int and 64 <= cycles <= 4096, 'Endurance cycles must be 64–4096')
    evidence.require(type(physical_seconds) is int and 60 <= physical_seconds <= 3600, 'Endurance physical seconds must be 60–3600')
    if release_phase:
        evidence.require(cycles >= 256 and physical_seconds >= 600, 'Release endurance requires at least 256 cycles and 600 physical seconds')


def github_ci_isolation(environment: dict) -> dict:
    observed = {name: environment.get(name) for name in ('GITHUB_ACTIONS', 'CI', 'RUNNER_ENVIRONMENT', 'GITHUB_RUN_ID')}
    evidence.require(observed['GITHUB_ACTIONS'] == 'true' and observed['CI'] == 'true'
        and observed['RUNNER_ENVIRONMENT'] == 'github-hosted',
        'CI isolation requires actual GitHub-hosted Actions, GITHUB_ACTIONS=true and CI=true')
    return {'mode': 'github-hosted-ci-isolation', 'observedEnvironment': observed,
            'sharedLocalPerformanceClaim': False, 'environmentOverridden': False,
            'scope': 'Explicit isolated GitHub-hosted job; no shared local machine reference/performance claim'}


def execution_window(workspace: Path | None, ci_isolated: bool):
    evidence.require((workspace is not None) != ci_isolated, 'Select exactly one shared host lock or explicit CI isolation')
    if workspace is not None:
        lock, metadata = host_lock(workspace)
        return lock, {'mode': 'shared-host-lock', 'sharedPerformanceLock': metadata,
                      'environmentOverridden': False, 'sharedLocalPerformanceClaim': False}
    return nullcontext(), github_ci_isolation(os.environ)


def browser_selection(playwright, args) -> tuple[dict, dict]:
    import playwright as package
    metadata = {'playwrightVersion': version('playwright')}
    if args.browser is None:
        evidence.require(args.browser_name in (None, 'chromium'), 'Default Playwright browser is Chromium')
        executable = Path(playwright.chromium.executable_path)
        pin_path = Path(package.__file__).parent / 'driver/package/browsers.json'
        pin = next(row for row in evidence.object_json(pin_path)['browsers'] if row['name'] == 'chromium')
        metadata.update(browserName='chromium', browserDistribution='playwright-pinned-chromium',
                        browserPin={'revision': pin['revision'], 'browserVersion': pin['browserVersion'],
                                    'manifestSha256': sha(pin_path)})
    else:
        executable = args.browser
        detected = {'msedge.exe': 'edge', 'msedge': 'edge', 'chrome.exe': 'chrome',
                    'google-chrome': 'chrome', 'google-chrome-stable': 'chrome'}.get(executable.name.lower(), 'chromium')
        evidence.require(args.browser_name in (None, detected), 'Requested browser name does not match selected executable')
        metadata.update(browserName=detected, browserDistribution='explicit-executable')
    evidence.require(executable.is_file(), 'Selected browser executable is unavailable: ' + str(executable))
    metadata.update(browserExecutable=str(executable.resolve()), browserExecutableSha256=sha(executable))
    return {'headless': True, 'executable_path': str(executable), 'args': ['--disable-gpu']}, metadata


def validate_endurance(report: dict, manifest: dict, sources: dict, cycles: int = 256,
                       physical_seconds: int = 600, not_before=None) -> dict:
    """Fail-closed receipt consistency, never a signature or full leak certificate."""
    require = evidence.require
    validate_configuration(cycles, physical_seconds, report.get('releasePhase') is True)
    require(report.get('status') == 'CPU_BROWSER_AND_BOUNDED_ENDURANCE_PASSED', 'CPU endurance did not pass')
    require(report.get('releaseApproved') is False and report.get('pageErrors') == [], 'Endurance cannot approve releases or contain page errors')
    require(report.get('cpuBrowserArgs') == ['--disable-gpu'] and report.get('browserClosed') is True, 'CPU browser isolation/cleanup was not recorded')
    require(report.get('artifactHashes') == manifest['artifacts'], 'Endurance native pair changed')
    require(report.get('sourceSha256Before') == sources == report.get('sourceSha256After'), 'Endurance sources changed')
    require(report.get('requestedConfiguration') == {'cycles': cycles, 'physicalSeconds': physical_seconds}, 'Endurance configuration changed')
    isolation = report.get('executionIsolation', {})
    if isolation.get('mode') == 'shared-host-lock':
        shared = report.get('sharedPerformanceLock', {})
        require(type(shared) is dict and isolation.get('sharedPerformanceLock') == shared
            and isinstance(shared.get('sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', shared['sha256']) is not None
            and shared.get('environmentOverridden') is False, 'Shared host lock evidence missing')
    elif isolation.get('mode') == 'github-hosted-ci-isolation':
        require(isolation == github_ci_isolation(isolation.get('observedEnvironment', {}))
            and report.get('sharedPerformanceLock') is None, 'CI isolation evidence changed')
    else:
        raise evidence.EvidenceError('Explicit endurance isolation evidence missing')
    evidence.validate_report(report.get('functional'), manifest,
        {name: sources[name] for name in evidence.HARNESS}, 'candidate', not_before=not_before)
    rows = report.get('endurance', {})
    require(rows.get('status') == 'BOUNDED_CPU_ENDURANCE_PASSED' and rows.get('physicsExecuted') is True, 'No bounded native simulation proof')
    require(rows.get('releaseApproved') is False and rows.get('engineIntegrationVerified') is False, 'Endurance scope was overstated')
    require(rows.get('artifactHashes') == manifest['artifacts'] and rows.get('stderr') == [], 'Endurance artifact/SDK diagnostics mismatch')
    require(rows.get('driverWorkerCleanup') == {'terminationInvoked': True, 'blobUrlRevoked': True}, 'Endurance Worker cleanup missing')
    started = evidence.instant(rows.get('startedUtc'), 'endurance started')
    finished = evidence.instant(rows.get('finishedUtc'), 'endurance finished')
    require(started <= finished and started >= evidence.instant(manifest['staged_utc'], 'staged build'), 'Endurance timestamp is stale or reversed')
    require(finished <= datetime.now(timezone.utc) + timedelta(minutes=5), 'Endurance timestamp is in the future')
    if not_before is not None:
        require(started >= not_before, 'Endurance predates this verification invocation')
    tests = rows.get('tests')
    require(type(tests) is list and tuple(row.get('name') for row in tests) == ENDURANCE_CASES, 'Endurance cases missing, duplicated or unexpected')
    for row in tests:
        require(row.get('status') == 'PASS' and type(row.get('ms')) in (int, float)
                and math.isfinite(row['ms']) and row['ms'] >= 0, 'Endurance case did not actually pass')
    life, replay, cleanup = (row['detail'] for row in tests)
    for name, expected in {'cycles': cycles, 'warmupCycles': 32, 'scenesCreatedAndReleased': cycles + 32,
        'dynamicActorsCreatedAndReleased': (cycles + 32) * 16, 'bulkContextsCreatedAndReleased': (cycles + 32) * 2,
        'blastFractureSmokeRuns': cycles + 32, 'maximumActiveActors': 17,
        'allocationProbeSamples': cycles, 'tailSamples': (cycles + 1) // 2}.items():
        require(type(life.get(name)) is int and life[name] == expected, 'Incomplete lifecycle coverage: ' + name)
    require(life.get('activeActorsAfterEveryClose') == [0] and life.get('retiredBulkStatus') == [-1], 'Retired native ownership is still active')
    require(life.get('liveBefore') == ZERO_ADDONS == life.get('liveAfter'), 'Native addons retained ownership')
    require(all(type(value) is int for field in ('liveBefore', 'liveAfter') for value in life[field].values()), 'Native addon counters must be integers')
    heaps = life.get('wasmHeapBytesTail')
    require(type(heaps) is list and len(heaps) == 1 and type(heaps[0]) is int and heaps[0] > 0, 'WASM memory did not reach bounded high-water')
    allocations = life.get('allocationReuse')
    require(type(allocations) is list and [p.get('bytes') for p in allocations] == [16, 256, 4096, 65536, 1048576], 'Allocation probes changed')
    for allocation in allocations:
        addresses = allocation.get('addresses')
        require(type(addresses) is list and 1 <= len(addresses) <= 4 and len(set(addresses)) == len(addresses)
                and all(type(p) is int and p > 0 for p in addresses), 'Native probes failed bounded address reuse')
    expected_input = {'seed': 1337, 'bodies': 8, 'dt': 1 / 120, 'steps': physical_seconds * 120, 'sampleEvery': 30,
        'initialLayout': '1.5 m four-column grid', 'impulseEverySteps': 120,
        'impulses': [[.4, 2.5, .2], [-.4, 2.5, -.2]], 'impulseBody': 'floor(step/120) mod 8', 'impulseChoice': 'floor(step/120) mod 2'}
    expected_sha = hashlib.sha256(json.dumps(expected_input, separators=(',', ':')).encode()).hexdigest()
    require(replay.get('input') == expected_input and replay.get('inputSha256') == expected_sha, 'Replay physical inputs changed')
    for name, expected in {'physicalSecondsPerRun': physical_seconds, 'stepsPerRun': physical_seconds * 120,
        'sampleCountPerRun': physical_seconds * 4, 'sampledComponents': physical_seconds * 4 * 8 * 7,
        'trajectoryBytes': physical_seconds * 4 * 8 * 7 * 4}.items():
        require(type(replay.get(name)) is int and replay[name] == expected, 'Replay duration/coverage changed: ' + name)
    require(type(replay.get('differentBytes')) is int and replay['differentBytes'] == 0
        and type(replay.get('maxPoseDelta')) in (int, float) and replay['maxPoseDelta'] == 0
        and replay.get('remainingActors') == [0, 0], 'Same-input native replay diverged or retained actors')
    require(isinstance(replay.get('trajectorySha256'), str) and re.fullmatch(r'[0-9a-f]{64}', replay['trajectorySha256']) is not None, 'Replay trajectory identity missing')
    error = replay.get('maxQuaternionError')
    require(type(error) in (int, float) and math.isfinite(error) and 0 <= error < .005, 'Replay quaternion drift is invalid')
    require(replay.get('allocationAfterReplay', {}).get('heapBytes') == heaps[0], 'WASM high-water changed during bounded replay')
    require(cleanup.get('ownedScenes') == 0 and cleanup.get('addonLiveCounters') == ZERO_ADDONS
        and cleanup.get('sdkReleased') is True and cleanup.get('sdkStderr') == 0, 'Native endurance teardown missing')
    require(type(cleanup['ownedScenes']) is int and type(cleanup['sdkStderr']) is int
        and all(type(value) is int for value in cleanup['addonLiveCounters'].values()), 'Native cleanup counts must be integers')
    return {'status': 'BOUNDED_ENDURANCE_ACCEPTED_NOT_RELEASE_CERTIFIED', 'testsAccepted': 3,
            'cycles': cycles, 'physicalSecondsPerReplay': physical_seconds, 'physicsExecuted': True,
            'releaseApproved': False, 'fullAllocatorLeakProof': False,
            'stillUnproven': ['long wall-clock/unbounded endurance', 'full native allocator accounting',
                             'cross-platform/device determinism', 'Android/Firefox/Safari hardware']}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def host_lock(workspace: Path):
    helper = workspace / 'tests/storage/performance_run_lock.py'
    spec = importlib.util.spec_from_file_location('physx_endurance_host_lock', helper)
    if spec is None or spec.loader is None:
        raise ValueError('Existing host performance lock cannot be loaded')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.performance_run_lock(workspace), {
        'helper': str(helper.resolve()), 'sha256': sha(helper),
        'helperLicense': 'LicenseRef-ParticleRealms-Alpha; host-only validation dependency, not redistributed',
        'effectiveWorkspace': str(Path(os.environ.get('WEBGPU_OS_PERFORMANCE_LOCK_ROOT') or workspace).resolve()),
        'environmentOverridden': False,
    }


def power_observation():
    if os.name != 'nt':
        return {'available': False, 'environmentOverridden': False}
    import ctypes
    class Power(ctypes.Structure):
        _fields_ = [('ac', ctypes.c_ubyte), ('flags', ctypes.c_ubyte), ('percent', ctypes.c_ubyte),
                    ('reserved', ctypes.c_ubyte), ('life', ctypes.c_uint32), ('fullLife', ctypes.c_uint32)]
    state = Power()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(state)):
        return {'available': False, 'environmentOverridden': False}
    return {'available': True, 'acLineStatus': state.ac, 'batteryPercent': state.percent,
            'environmentOverridden': False}


def cpu_run(args, report):
    from playwright.sync_api import sync_playwright
    manifest = evidence.object_json(ROOT / 'dist/candidate/build-manifest.json')
    evidence.verify_artifacts(manifest, ROOT / 'dist/candidate', 'candidate')
    before = evidence.source_hashes(ROOT, ENDURANCE_SOURCES)
    report['requestedConfiguration'] = {'cycles': args.cycles, 'physicalSeconds': args.physical_seconds}
    server = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    errors = []
    try:
        with sync_playwright() as playwright:
            options, metadata = browser_selection(playwright, args)
            report.update(metadata)
            browser = playwright.chromium.launch(**options)
            try:
                if 'browserPin' in metadata:
                    evidence.require(browser.version == metadata['browserPin']['browserVersion'], 'Launched Chromium differs from its installed package pin')
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda message: print(message.text, flush=True) if message.text.startswith('[endurance]') else None)
                page.goto(f'http://127.0.0.1:{server.server_port}/web/index.html')
                page.wait_for_function('typeof window.runValidation === "function"')
                functional = page.evaluate('() => window.runValidation("candidate")')
                functional.update(pageErrors=list(errors), artifactHashes=manifest['artifacts'], testHarnessSha256=evidence.source_hashes())
                report['functional'] = functional
                report['functionalAdmission'] = evidence.validate_report(functional, manifest, evidence.source_hashes(), 'candidate')
                report['endurance'] = page.evaluate('''config => new Promise((resolve,reject) => {
                    const source = `import {runEndurance} from ${JSON.stringify(location.origin + '/web/endurance-regressions.mjs')};
                        self.onmessage=async({data})=>{self.postMessage({done:await runEndurance(data,row=>self.postMessage({progress:row}))});};`;
                    const url=URL.createObjectURL(new Blob([source],{type:'text/javascript'}));
                    const worker=new Worker(url,{type:'module'});let done=false;
                    const timer=setTimeout(()=>finish(null,Error('Bounded endurance Worker timed out')),240000);
                    function finish(result,error){if(done)return;done=true;clearTimeout(timer);worker.terminate();URL.revokeObjectURL(url);
                        if(result)result.driverWorkerCleanup={terminationInvoked:true,blobUrlRevoked:true};error?reject(error):resolve(result);}
                    worker.onmessage=({data})=>{if(data.progress)console.log('[endurance] '+JSON.stringify(data.progress));else if(data.done)finish(data.done);};
                    worker.onerror=event=>finish(null,Error(event.message));worker.postMessage(config);
                })''', {'cycles': args.cycles, 'physicalSeconds': args.physical_seconds})
                report.update(browserVersion=browser.version, pageErrors=errors, cpuBrowserArgs=['--disable-gpu'])
                if errors or report['endurance']['status'] != 'BOUNDED_CPU_ENDURANCE_PASSED':
                    raise ValueError('CPU native endurance failed: ' + str(report['endurance'].get('error', errors)))
                report['artifactHashes'] = manifest['artifacts']
                report['sourceSha256Before'] = before
                report['sourceSha256After'] = evidence.source_hashes(ROOT, ENDURANCE_SOURCES)
                if report['sourceSha256After'] != before:
                    raise ValueError('CPU harness sources changed during execution')
                evidence.verify_artifacts(manifest, ROOT / 'dist/candidate', 'candidate')
                report['status'] = 'CPU_BROWSER_AND_BOUNDED_ENDURANCE_PASSED'
            finally:
                browser.close()
                report['browserClosed'] = True
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def delivered_snapshot(args, report):
    expected_version = getattr(args, 'expected_version', HISTORICAL_DELIVERED_VERSION)
    evidence.require(isinstance(expected_version, str)
        and re.fullmatch(r'5\.11\.0-alpha\.[1-9][0-9]*', expected_version) is not None,
        'Invalid expected delivered release version')
    expected_name = 'physx-pe-' + expected_version + '-runtime.zip'
    evidence.require(args.runtime_zip.name == expected_name, 'Delivered archive name differs from expected release version')
    proof = evidence.object_json(args.release_proof)
    evidence.require(proof.get('schema') == 'physx-pe.anonymous-download-check/v1'
        and proof.get('status') == 'PASS' and proof.get('private') is False
        and proof.get('repository') == 'https://github.com/BTSpaniel/Physx',
        'Anonymous public download receipt did not pass')
    assets = proof.get('assets')
    evidence.require(isinstance(assets, list) and all(isinstance(item, dict) for item in assets),
        'Anonymous download asset inventory is invalid')
    matches = [item for item in assets if item.get('name') == expected_name]
    evidence.require(len(matches) == 1, 'Delivered ZIP needs exactly one anonymous receipt asset')
    asset = matches[0]
    expected_url = 'https://github.com/BTSpaniel/Physx/releases/download/v' + expected_version + '/' + expected_name
    evidence.require(asset.get('status') == 'PASS' and asset.get('authentication') == 'none'
        and asset.get('url') == expected_url, 'Anonymous asset release tag or authentication differs')
    revision = proof.get('sourceRevision')
    evidence.require(isinstance(revision, dict) and set(revision) == {'commit', 'tree', 'inventorySha256'}
        and all(isinstance(revision[key], str) and re.fullmatch(pattern, revision[key]) is not None
                for key, pattern in (('commit', r'[0-9a-f]{40}'), ('tree', r'[0-9a-f]{40}'),
                                     ('inventorySha256', r'[0-9a-f]{64}'))),
        'Anonymous download source revision is invalid')
    evidence.require(type(asset.get('bytes')) is int and asset['bytes'] > 0
        and args.runtime_zip.stat().st_size == asset['bytes']
        and asset.get('digest') == 'sha256:' + sha(args.runtime_zip),
        'Delivered ZIP differs from anonymous-download receipt')
    directory = ROOT / 'work' / ('browser-compat-' + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    archive_inputs = {}
    with zipfile.ZipFile(args.runtime_zip) as archive:
        names = archive.namelist()
        evidence.require(len(names) == len(set(names)), 'Duplicate archive paths')
        manifest = json.loads(archive.read('runtime-manifest.json'))
        evidence.require(manifest.get('schema') == 'physx-pe.package-manifest/v1'
            and manifest.get('sdkVersion') == '5.11.0' and manifest.get('version') == expected_version
            and manifest.get('sourceRevision') == revision
            and set(names) == set(manifest['files']) | {'runtime-manifest.json'},
            'Unexpected delivered runtime version, source revision or inventory')
        verification = json.loads(archive.read('reports/verification.json'))
        evidence.require(verification.get('version') == expected_version
            and verification.get('sourceRevision') == revision
            and verification.get('sourceInventorySha256') == revision['inventorySha256']
            and verification.get('status') == 'BUILD_AND_BROWSER_SMOKE_PASSED_ALPHA',
            'Delivered verification version or source revision differs')
        for name in names:
            path = PurePosixPath(name)
            evidence.require(not path.is_absolute() and '\\' not in name and all(part not in ('', '.', '..') and ':' not in part for part in path.parts), 'Unsafe ZIP path: ' + name)
            info = archive.getinfo(name)
            evidence.require((info.external_attr >> 16) & 0o170000 != 0o120000, 'ZIP symlink is not allowed')
            target = (directory / name).resolve()
            evidence.require(target.is_relative_to(directory.resolve()), 'ZIP target escapes test snapshot')
            content = archive.read(name)
            meta = {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
            if name != 'runtime-manifest.json':
                evidence.require(meta == manifest['files'][name], 'Runtime manifest mismatch: ' + name)
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
            archive_inputs[name] = meta
    native_source = 'addons/flow/pr_flow_host.cpp'
    native_expected = evidence.object_json(directory / 'dist/candidate/build-manifest.json')['bridge_sources'][native_source]
    evidence.require(sha(ROOT / native_source) == native_expected, 'Native provenance source differs from delivered build manifest')
    test_inputs = {}
    for name in DELIVERED_TEST_HELPERS:
        target = directory / name
        if target.exists():
            evidence.require(sha(target) == sha(ROOT / name), 'Current helper differs from delivered bytes: ' + name)
        else:
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT / name, target)
        test_inputs[name] = {'bytes': target.stat().st_size, 'sha256': sha(target)}
    # Edge requests a favicon after the probe's JSON navigation. Supply a real
    # test-only transparent ICO instead of suppressing console/network errors.
    favicon = directory / 'favicon.ico'
    evidence.require(not favicon.exists(), 'Delivered archive already owns favicon.ico')
    def png_chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    png = b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
    png += png_chunk(b'IDAT', zlib.compress(b'\0\0\0\0\0')) + png_chunk(b'IEND', b'')
    favicon.write_bytes(struct.pack('<HHHBBBBHHII', 0, 1, 1, 1, 1, 0, 0, 1, 32, len(png), 22) + png)
    test_only = {'favicon.ico': {'bytes': favicon.stat().st_size, 'sha256': sha(favicon),
                                'purpose': 'Generated transparent test-only ICO; not part of delivered runtime'}}
    (directory / 'reports').mkdir(exist_ok=True)
    report.update(expectedDeliveredVersion=expected_version, deliveredSourceRevision=revision, snapshotRoot=str(directory), deliveredZip={'path': str(args.runtime_zip), 'bytes': asset['bytes'], 'sha256': asset['digest'].removeprefix('sha256:')},
                  anonymousDownloadProof={'path': str(args.release_proof), 'sha256': sha(args.release_proof)}, archiveFiles=archive_inputs, testHelpers=test_inputs, testOnlyAssets=test_only)
    report['nativeProvenanceInput'] = {'path': native_source, 'sha256': native_expected,
                                      'purpose': 'Read-only helper hash input, matched to delivered build; not rebuilt or executed as source'}
    for name in ('physx-pe.mjs', 'physx-pe.wasm'):
        evidence.require(sha(directory / 'dist/candidate' / name) == sha(ROOT / 'dist/candidate' / name), 'GPU delivery pair differs from the staged expected release bytes')
    return directory


def flow_run(args, report):
    directory = delivered_snapshot(args, report)
    if args.mode == 'flow-probe':
        command = [sys.executable, str(directory / 'tools/flow_gpu_probe.py'), '--hardware', '--browser-executable', str(args.browser), '--report', str(directory / 'reports/edge-probe.json')]
        result = directory / 'reports/edge-probe.json'
    elif args.mode == 'flow-host':
        command = [sys.executable, str(directory / 'addons/flow/host_browser_test.py'), '--unified', '--hardware', '--browser-executable', str(args.browser)]
        result = directory / 'reports/flow-host-browser.json'
    else:
        command = [sys.executable, str(directory / 'addons/flow/wgsl_browser_test.py'), '--hardware', '--browser-executable', str(args.browser), '--report', str(directory / 'reports/edge-wgsl.json'), '--advection', '--mesh-scan']
        if args.mode == 'flow-shipped':
            command.append('--modules-only')
        result = directory / 'reports/edge-wgsl.json'
    log = args.report.with_suffix('.log')
    with log.open('w', encoding='utf-8') as stream:
        completed = subprocess.run(command, cwd=directory, stdout=stream, stderr=subprocess.STDOUT, timeout=300, check=False)
    report.update(command=command, exitCode=completed.returncode, result=evidence.object_json(result), log={'path': str(log), 'sha256': sha(log)})
    for name, meta in report['archiveFiles'].items():
        evidence.require(sha(directory / name) == meta['sha256'], 'Delivered runtime bytes changed during GPU check: ' + name)
    for name, meta in report['testHelpers'].items():
        evidence.require(sha(directory / name) == meta['sha256'] and sha(ROOT / name) == meta['sha256'], 'Existing Flow test helper changed: ' + name)
    for name, meta in report['testOnlyAssets'].items():
        evidence.require(sha(directory / name) == meta['sha256'], 'Test-only asset changed during GPU check: ' + name)
    if completed.returncode != 0:
        raise ValueError('Existing native Flow check failed: ' + str(report['result'].get('error', report['result']['status'])))
    report['status'] = 'DELIVERED_EDGE_FLOW_CHECK_PASSED'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path, help='Optional explicit executable; CPU default is the installed Playwright-pinned Chromium')
    parser.add_argument('--browser-name', choices=['chrome', 'edge', 'chromium'], help='Optional checked executable name; default is detected')
    parser.add_argument('--mode', choices=['cpu', 'flow-probe', 'flow-host', 'flow-wgsl', 'flow-shipped'], default='cpu')
    isolation = parser.add_mutually_exclusive_group(required=True)
    isolation.add_argument('--lock-workspace', type=Path, help='Existing shared host lock, imported without redistribution')
    isolation.add_argument('--ci-isolated', action='store_true', help='Explicit GitHub-hosted isolation; actual GITHUB_ACTIONS/CI must verify it')
    parser.add_argument('--release-phase', action='store_true', help='Require release minimum256 measured lifecycles and600 physicalseconds per replay')
    parser.add_argument('--cycles', type=int, default=256)
    parser.add_argument('--physical-seconds', type=int, default=600)
    parser.add_argument('--runtime-zip', type=Path)
    parser.add_argument('--release-proof', type=Path)
    parser.add_argument('--expected-version', default=HISTORICAL_DELIVERED_VERSION,
                        help='Expected delivered alpha version; defaults to historical alpha.2, pass the current release version explicitly for a new ZIP')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error('Report already exists; choose a unique path to preserve historical evidence')
    if args.release_phase and args.mode != 'cpu':
        parser.error('Release endurance is a separate CPU-only phase')
    if args.mode != 'cpu' and args.browser is None:
        parser.error('Delivered GPU compatibility requires an explicit installed executable')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {'status': 'RUNNING', 'startedUtc': datetime.now(timezone.utc).isoformat(), 'browserName': args.browser_name,
              'browserExecutable': str(args.browser) if args.browser else None,
              'browserExecutableSha256': sha(args.browser) if args.browser else None, 'mode': args.mode,
              'driverSha256': sha(Path(__file__)), 'powerAtStart': power_observation(), 'releaseApproved': False,
              'releasePhase': args.release_phase,
              'scope': 'Bounded real execution on this installed browser/device; unavailable Android/Firefox/Safari are not covered'}
    started = time.monotonic()
    try:
        validate_configuration(args.cycles, args.physical_seconds, args.release_phase)
        if args.browser is not None:
            _, metadata = browser_selection(None, args)
            report.update(metadata)
        lock, metadata = execution_window(args.lock_workspace, args.ci_isolated)
        report['executionIsolation'] = metadata
        report['sharedPerformanceLock'] = metadata.get('sharedPerformanceLock')
        print('Entering ' + metadata['mode'] + ': ' + args.mode + ' / ' + (args.browser_name or 'detected-browser'), flush=True)
        with lock:
            report['lockAcquiredUtc'] = datetime.now(timezone.utc).isoformat()
            if args.mode == 'cpu':
                cpu_run(args, report)
                manifest = evidence.object_json(ROOT / 'dist/candidate/build-manifest.json')
                report['enduranceAdmission'] = validate_endurance(report, manifest,
                    evidence.source_hashes(ROOT, ENDURANCE_SOURCES), args.cycles, args.physical_seconds,
                    not_before=evidence.instant(report['startedUtc'], 'run started'))
            else:
                if not args.runtime_zip or not args.release_proof:
                    raise ValueError('GPU checks require the exact delivered ZIP and its anonymous-download proof')
                flow_run(args, report)
        report['lockReleasedUtc'] = datetime.now(timezone.utc).isoformat()
    except Exception as error:
        report.update(status='FAILED', error=str(error))
    finally:
        report.update(finishedUtc=datetime.now(timezone.utc).isoformat(), elapsedSeconds=time.monotonic() - started,
                      powerAtEnd=power_observation())
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(report['status'] + ': ' + str(args.report), flush=True)
    if report['status'] == 'FAILED':
        print(report['error'], flush=True)
    return 0 if report['status'] != 'FAILED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
