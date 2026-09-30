# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Build and verify the standalone PhysX PE alpha; never install or publish it."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tools'))
import physx_lab as lab
import evidence
from prepare_sources import prepare
from generate_physx_types import generate_types

VERSION = '5.11.0-alpha.2'


def inventory(root: Path | None = None) -> dict[str, str]:
    root = ROOT if root is None else root
    ignored = {'.git', 'work', 'dist', 'reports', '__pycache__', '.venv'}
    captured = {}
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(name for name in folders if name not in ignored)
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix != '.pyc':
                captured[path.relative_to(root).as_posix()] = lab.sha256(path)
    return dict(sorted(captured.items()))


def source_revision(source_hashes: dict[str, str], root: Path | None = None) -> dict[str, str]:
    """Bind the actual source bytes to a clean committed Git tree, without filters."""
    root = ROOT if root is None else root
    commit = lab.git(root, 'rev-parse', 'HEAD')
    tree = lab.git(root, 'rev-parse', 'HEAD^{tree}')
    if not all(re.fullmatch(r'[0-9a-f]{40}', value) for value in (commit, tree)):
        raise lab.LabError('A committed source revision is required')
    if lab.git(root, 'status', '--porcelain', '--untracked-files=all'):
        raise lab.LabError('Release source/index must be clean and committed')
    rows = lab.git(root, 'ls-tree', '-rz', '--full-tree', 'HEAD').split('\0')
    committed = {}
    for row in filter(None, rows):
        header, name = row.split('\t', 1)
        mode, kind, object_id = header.split()
        if kind != 'blob' or mode not in ('100644', '100755'):
            raise lab.LabError('Unsupported source-tree entry: ' + name)
        committed[name] = object_id
    if set(committed) != set(source_hashes):
        raise lab.LabError('Full source inventory differs from the committed Git tree')
    result = subprocess.run(['git', '-C', str(root), 'cat-file', '--batch'],
                            input=''.join(value + '\n' for value in committed.values()).encode('ascii'),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    blobs = io.BytesIO(result.stdout)
    for name, object_id in committed.items():
        header = blobs.readline().decode('ascii').strip().split()
        if len(header) != 3 or header[:2] != [object_id, 'blob']:
            raise lab.LabError('Committed source object could not be read: ' + name)
        content = blobs.read(int(header[2]))
        if blobs.read(1) != b'\n' or hashlib.sha256(content).hexdigest() != source_hashes[name]:
            raise lab.LabError('Source bytes differ from their committed object: ' + name)
    if blobs.read() or inventory(root) != source_hashes:
        raise lab.LabError('Source inventory changed while binding its revision')
    return {'commit': commit, 'tree': tree, 'inventorySha256': hashlib.sha256(
        json.dumps(source_hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def selection() -> dict:
    selected = json.loads((ROOT / 'source-selection.json').read_text(encoding='utf-8'))
    for relative, expected in selected['stableBridgeSources'].items():
        if lab.sha256(ROOT / relative) != expected:
            raise lab.LabError('Stable source selection changed: ' + relative)
    return selected


def shader_inventory() -> dict[str, dict]:
    root = ROOT / 'dist/flow-wgsl'
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status') != 'FLOW_WGSL_CORPUS_COMPILED' or manifest.get('shaderCount') != 97:
        raise lab.LabError('Pinned Flow shader corpus is incomplete')
    rows = manifest.get('shaders')
    if not isinstance(rows, list) or len(rows) != 97:
        raise lab.LabError('Pinned Flow shader corpus must contain exactly 97 rows')
    wgsl_names = [row.get('wgsl') for row in rows]
    reflection_names = [row.get('reflection') for row in rows]
    if (any(not isinstance(name, str) for name in wgsl_names + reflection_names) or
            len(set(wgsl_names)) != 97 or len(set(reflection_names)) != 97 or
            set(wgsl_names) & set(reflection_names) or
            'manifest.json' in set(wgsl_names + reflection_names)):
        raise lab.LabError('Flow WGSL/reflection paths must each be 97 unique files')
    expected = {'manifest.json'}
    for row in rows:
        if row.get('status') != 'PASS':
            raise lab.LabError('A generated Flow shader failed compilation')
        for name, digest in ((row['wgsl'], row['wgslSha256']),
                             (row['reflection'], row['reflectionSha256'])):
            path = root / name
            if not path.resolve().is_relative_to(root.resolve()) or lab.sha256(path) != digest:
                raise lab.LabError('Generated Flow shader/sidecar changed: ' + name)
            expected.add(name)
    actual = {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()}
    if len(expected) != 195 or actual != expected:
        raise lab.LabError('Generated Flow shader directory differs from its exact inventory')
    return {name: {'bytes': (root / name).stat().st_size, 'sha256': lab.sha256(root / name)}
            for name in sorted(actual)}


def command(argv: list[str], timeout: int = 7200) -> None:
    lab.run([sys.executable, *argv], timeout=timeout)


def build() -> None:
    selection()
    before = inventory()
    revision = source_revision(before)
    prepared = prepare()
    command(['addons/flow/compile_wgsl.py'])
    shader = json.loads((ROOT / 'dist/flow-wgsl/manifest.json').read_text())
    if shader.get('status') != 'FLOW_WGSL_CORPUS_COMPILED' or shader.get('shaderCount') != 97:
        raise lab.LabError('The complete pinned Flow shader corpus did not compile')
    command(['build.py', 'wasm', '--profile', 'candidate'])
    idl = ROOT / 'work/candidate/PhysX/physx/source/webidlbindings/src/wasm/PhysXWasm.idl'
    declarations = generate_types(idl.read_text(encoding='utf-8'))
    if declarations != (ROOT / 'types/physx-pe.d.ts').read_bytes():
        raise lab.LabError('Generated declarations differ from the reviewed IDL reference')
    (ROOT / 'dist/candidate/physx-pe.d.ts').write_bytes(declarations)
    after = inventory()
    if before != after:
        raise lab.LabError('Release source files changed during compilation')
    if source_revision(after) != revision:
        raise lab.LabError('Committed source revision changed during compilation')
    manifest = json.loads((ROOT / 'dist/candidate/build-manifest.json').read_text())
    evidence.verify_artifacts(manifest, ROOT / 'dist/candidate', 'candidate')
    lab.write_json(ROOT / 'reports/release-build.json', {
        'schema': 'physx-pe.release-build/v1', 'version': VERSION,
        'status': 'COMPILED_NOT_RUNTIME_VERIFIED', 'sourceHashes': before,
        'sourceRevision': revision,
        'sourceHashesAfter': after, 'upstreamCommit': prepared['upstreamCommit'],
        'overlaySha256': prepared['overlaySha256'], 'artifacts': manifest['artifacts'],
        'flowManifestSha256': lab.sha256(ROOT / 'dist/flow-wgsl/manifest.json'),
        'flowShaderInventory': shader_inventory(),
        'preparedSourceReceiptSha256': lab.sha256(ROOT / 'work/source-preparation.json')})


def verify(browser: str | None, hardware: bool, software_vulkan: bool = False,
           flow_browser_engine: str = 'chromium') -> None:
    selection()
    built = json.loads((ROOT / 'reports/release-build.json').read_text())
    before = inventory()
    revision = source_revision(before)
    if built.get('status') != 'COMPILED_NOT_RUNTIME_VERIFIED' or built.get('sourceHashes') != before:
        raise lab.LabError('Build does not belong to the current source inventory')
    if built.get('sourceRevision') != revision:
        raise lab.LabError('Build does not belong to the current committed source revision')
    manifest = json.loads((ROOT / 'dist/candidate/build-manifest.json').read_text())
    evidence.verify_artifacts(manifest, ROOT / 'dist/candidate', 'candidate')
    if built.get('artifacts') != manifest['artifacts']:
        raise lab.LabError('Matched release artifact pair changed after build')
    shaders_before = shader_inventory()
    if built.get('flowShaderInventory') != shaders_before:
        raise lab.LabError('Flow shader inventory changed after build')
    extra = ['--chromium', browser] if browser else []
    gpu = ['--browser-executable', browser] if browser else []
    if hardware:
        gpu += ['--hardware']
    host_backend = ['--software-vulkan'] if software_vulkan else []
    host_backend += ['--browser-engine', flow_browser_engine]
    jobs = [
        ('Rust units, Python FFI and C ABI layout', ['build.py', 'test'], 'rust-python-tests.json'),
        ('Native C++/Rust ABI', ['build.py', 'abi', '--target', 'native'], 'abi-build.json'),
        ('Browser C++/Rust ABI compile', ['build.py', 'abi', '--target', 'wasm'], 'abi-build.json'),
        ('Browser C++/Rust ABI', ['tools/abi_browser_test.py', *extra], 'abi-browser.json'),
        ('PhysX rigid-body browser regressions', ['tools/browser_test.py', '--profile', 'candidate', *extra], 'candidate-browser.json'),
        ('Blast split and unified WASM/WebGPU transfer', ['addons/flow/browser_test.py', *gpu], 'unified-browser.json'),
        ('Flow WGSL modules and executed advection/mesh scan', ['addons/flow/wgsl_browser_test.py', *gpu, '--modules-only', '--advection', '--mesh-scan', '--report', 'reports/flow-wgsl-modules.json'], 'flow-wgsl-modules.json'),
        ('Native Flow graph and WebGPU ownership', ['addons/flow/host_browser_test.py', '--unified', *gpu, *host_backend], 'flow-host-browser.json'),
    ]
    report = {'schema': 'physx-pe.release-verification/v1', 'version': VERSION,
              'status': 'RUNNING', 'startedUtc': datetime.now(timezone.utc).isoformat(),
              'sourceHashesBefore': before, 'artifacts': manifest['artifacts'], 'steps': [],
              'sourceRevision': revision,
              'shaderHashesBefore': shaders_before,
              'gpuMode': 'hardware' if hardware else 'WebGPU-backend-unreported' if flow_browser_engine == 'firefox' else 'software-WebGPU',
              'requestedGpuMode': 'hardware' if hardware else 'software-WebGPU',
              'flowBrowserEngine': flow_browser_engine,
              'scope': 'Build and functional browser smoke only. No full feature parity, engine promotion, device matrix or realtime claim.'}
    destination = ROOT / 'reports/release-verification.json'
    try:
        for index, (name, argv, output) in enumerate(jobs, 1):
            row = {'name': name, 'command': argv, 'status': 'RUNNING'}
            report['steps'].append(row)
            lab.write_json(destination, report)
            command(argv, timeout=10800)
            produced = ROOT / 'reports' / output
            phase = ROOT / 'reports/phases' / f'{index:02d}-{output}'
            phase.parent.mkdir(parents=True, exist_ok=True)
            produced_bytes = produced.read_bytes()
            phase.write_bytes(produced_bytes)
            if phase.read_bytes() != produced_bytes:
                raise lab.LabError('Phase receipt changed while preserving it')
            raw = json.loads(produced_bytes)
            row.update(status='PASS', reportFile=phase.relative_to(ROOT).as_posix(), reportSha256=lab.sha256(phase),
                       resultStatus=raw.get('status'), checks=raw.get('checks'),
                       testCount=len(raw.get('tests', [])) if isinstance(raw.get('tests'), list) else None)
            lab.write_json(destination, report)
        evidence.check_profile('candidate', not_before=evidence.instant(report['startedUtc'], 'release started'))
        unified = json.loads((ROOT / 'reports/unified-browser.json').read_text())
        flow = json.loads((ROOT / 'reports/flow-host-browser.json').read_text())
        if unified.get('artifactHashes') != manifest['artifacts'] or flow.get('artifactHashes') != manifest['artifacts']:
            raise lab.LabError('Unified browser evidence belongs to a different artifact pair')
        report['sourceHashesAfter'] = inventory()
        report['shaderHashesAfter'] = shader_inventory()
        if report['sourceHashesAfter'] != before:
            raise lab.LabError('Source inventory changed during verification')
        if source_revision(report['sourceHashesAfter']) != revision:
            raise lab.LabError('Committed source revision changed during verification')
        if report['shaderHashesAfter'] != shaders_before:
            raise lab.LabError('Generated shaders changed during verification')
        report['status'] = 'BUILD_AND_BROWSER_SMOKE_PASSED_ALPHA'
    except Exception as exc:
        report.update(status='FAIL', error=str(exc))
        raise
    finally:
        report['finishedUtc'] = datetime.now(timezone.utc).isoformat()
        lab.write_json(destination, report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'verify', 'package', 'all'])
    parser.add_argument('--browser', help='Optional Chrome/Chromium executable')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--hardware', action='store_true', help='Use the actual browser hardware WebGPU adapter')
    mode.add_argument('--software-vulkan', action='store_true', help='Use Mesa lavapipe for the Flow host 1024-lane software test')
    parser.add_argument('--flow-browser-engine', choices=['chromium', 'firefox'], default='chromium',
                        help='Browser engine for the unchanged native Flow graph; other browser phases use Chromium')
    args = parser.parse_args()
    if args.flow_browser_engine == 'firefox' and not args.software_vulkan:
        parser.error('--flow-browser-engine firefox requires --software-vulkan')
    if args.flow_browser_engine != 'chromium' and args.browser:
        parser.error('--browser selects Chromium for the other phases; use default pinned Firefox for the Flow phase')
    try:
        if args.command in ('build', 'all'):
            build()
        if args.command in ('verify', 'all'):
            verify(args.browser, args.hardware, args.software_vulkan, args.flow_browser_engine)
        if args.command in ('package', 'all'):
            from package_release import package
            package()
    except (OSError, ValueError, lab.LabError) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
