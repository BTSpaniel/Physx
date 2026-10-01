# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Build and verify the standalone PhysX PE alpha; never install or publish it."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys
import uuid
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
    ignored = {'.git', '__pycache__', '.venv'}
    captured = {}
    for directory, folders, files in os.walk(root):
        # Frozen provenance under reference/{dist,work} is source-controlled
        # evidence, while only this kit's own top-level outputs are generated.
        outputs = {'work', 'dist', 'reports'} if Path(directory) == root else set()
        folders[:] = sorted(name for name in folders if name not in ignored | outputs)
        for name in sorted(files):
            path = Path(directory) / name
            if name not in ignored and path.suffix != '.pyc':
                captured[path.relative_to(root).as_posix()] = lab.sha256(path)
    return dict(sorted(captured.items()))


def windows_worktree_git_environment(root: Path) -> dict[str, str] | None:
    """Translate an existing Windows gitfile for scoped real Git calls in WSL.

    The repository metadata stays untouched. Git still resolves its actual
    common directory, and source_revision verifies every committed raw blob.
    Ordinary Git directories and relative/Unix gitfiles need no override.
    """
    metadata = root / '.git'
    if not metadata.is_file():
        return None
    match = re.fullmatch(r'gitdir:\s+([A-Za-z]):[/\\](.+)',
                         metadata.read_text(encoding='utf-8').strip())
    if match is None:
        return None
    actual = '/mnt/' + match[1].lower() + '/' + match[2].replace('\\', '/')
    return dict(os.environ, GIT_DIR=actual, GIT_WORK_TREE=str(root.resolve()))


def source_revision(source_hashes: dict[str, str], root: Path | None = None) -> dict[str, str]:
    """Bind the actual source bytes to a clean committed Git tree, without filters."""
    root = ROOT if root is None else root
    git_environment = (windows_worktree_git_environment(root)
                       if os.name != 'nt' and 'microsoft' in platform.release().lower() else None)
    commit = lab.git(root, 'rev-parse', 'HEAD', env=git_environment)
    tree = lab.git(root, 'rev-parse', 'HEAD^{tree}', env=git_environment)
    if not all(re.fullmatch(r'[0-9a-f]{40}', value) for value in (commit, tree)):
        raise lab.LabError('A committed source revision is required')
    if lab.git(root, 'status', '--porcelain', '--untracked-files=all', env=git_environment):
        raise lab.LabError('Release source/index must be clean and committed')
    rows = lab.git(root, 'ls-tree', '-rz', '--full-tree', 'HEAD', env=git_environment).split('\0')
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
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, env=git_environment)
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
    from flow_source_evidence import shader_inventory as admitted_flow_inventory
    return admitted_flow_inventory(ROOT)


def command(argv: list[str], timeout: int = 7200) -> None:
    lab.run([sys.executable, *argv], timeout=timeout)


def build(slangc: Path | None = None) -> None:
    from flow_source_evidence import require_final_selection
    require_final_selection(ROOT)
    selection()
    before = inventory()
    revision = source_revision(before)
    prepared = prepare()
    compiler_args = ['--slangc', str(slangc)] if slangc else []
    for recipe in ('compile_wgsl.py', 'compile_solid_wgsl.py', 'compile_scalar_wgsl.py', 'compile_momentum_wgsl.py'):
        command(['addons/flow/' + recipe, *compiler_args])
    shader_inventory()
    command(['build.py', 'wasm', '--profile', 'candidate'])
    idl = ROOT / 'work/candidate/PhysX/physx/source/webidlbindings/src/wasm/PhysXWasm.idl'
    declarations = generate_types(idl.read_text(encoding='utf-8'), ROOT)
    if declarations != (ROOT / 'types/physx-pe.d.ts').read_bytes():
        raise lab.LabError('Generated declarations differ from the reviewed IDL reference')
    (ROOT / 'dist/candidate/physx-pe.d.ts').write_bytes(declarations)
    (ROOT / 'dist/candidate/physx-pe.d.mts').write_bytes(declarations)
    command(['tools/generate_addon_types.py', '--check'])
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


def build_flow_component(slangc: Path | None, lock_workspace: Path | None, ci_isolated: bool) -> None:
    """Run the complete source build in the existing declared execution window."""
    from prepare_sources import prepare_flow_sources
    from browser_compat_endurance import execution_window
    if lock_workspace is not None and os.name != 'nt' and 'microsoft' in platform.release().lower():
        raise lab.LabError('Direct WSL locking is not the Windows shared host lock; use a Windows parent execution window')
    selection()
    before = inventory()
    window, isolation = execution_window(lock_workspace, ci_isolated)
    with window:
        if inventory() != before:
            raise lab.LabError('Source kit changed while queued for its compile window')
        prepare_flow_sources()
        compiler_args = ['--slangc', str(slangc)] if slangc else []
        for recipe in ('compile_wgsl.py', 'compile_solid_wgsl.py', 'compile_scalar_wgsl.py', 'compile_momentum_wgsl.py'):
            command(['addons/flow/' + recipe, *compiler_args], timeout=7200)
        shader_inventory()
        name = 'flow-source-build-' + uuid.uuid4().hex + '.json'
        command(['addons/flow/build_component.py', '--report', 'reports/' + name], timeout=7200)
        result = json.loads((ROOT / 'reports' / name).read_text(encoding='utf-8'))
        if inventory() != before:
            raise lab.LabError('Source kit changed during the complete Flow build')
        result['executionIsolation'] = isolation
        lab.write_json(ROOT / 'reports' / name, result)


def verify(browser: str | None, hardware: bool, software_vulkan: bool = False,
           flow_browser_engine: str = 'chromium', lock_workspace: Path | None = None,
           ci_isolated: bool = False) -> None:
    from browser_compat_endurance import execution_window, validate_endurance, ENDURANCE_SOURCES
    from flow_source_evidence import require_final_selection
    require_final_selection(ROOT)
    # This new CPU phase needs explicit isolation. The Alpha host lock is only
    # imported when supplied; GitHub uses verified isolated runner metadata.
    _, isolation_metadata = execution_window(lock_workspace, ci_isolated)
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
    endurance_output = 'endurance-browser-' + uuid.uuid4().hex + '.json'
    endurance_isolation = ['--lock-workspace', str(lock_workspace)] if lock_workspace is not None else ['--ci-isolated']
    endurance_browser = ['--browser', browser] if browser else []
    jobs = [
        ('Rust units, Python FFI and C ABI layout', ['build.py', 'test'], 'rust-python-tests.json'),
        ('Native C++/Rust ABI', ['build.py', 'abi', '--target', 'native'], 'abi-build.json'),
        ('Browser C++/Rust ABI compile', ['build.py', 'abi', '--target', 'wasm'], 'abi-build.json'),
        ('Browser C++/Rust ABI', ['tools/abi_browser_test.py', *extra], 'abi-browser.json'),
        ('TypeScript addon and SDK consumer semantics', ['tools/typecheck_browser.py', *extra], 'addon-types-browser.json'),
        ('PhysX behavioral browser regressions', ['tools/browser_test.py', '--profile', 'candidate', *extra], 'candidate-browser.json'),
        ('Bounded native CPU endurance and identical-input replay', ['tools/browser_compat_endurance.py',
            '--release-phase', *endurance_browser, *endurance_isolation, '--report', 'reports/' + endurance_output], endurance_output),
        ('Blast split and unified WASM/WebGPU transfer', ['addons/flow/browser_test.py', *gpu], 'unified-browser.json'),
        ('Complete 124 Flow WGSL modules and executed advection/mesh scan', ['addons/flow/wgsl_browser_test.py', *gpu, '--modules-only', '--include-addons', '--advection', '--mesh-scan', '--report', 'reports/flow-wgsl-modules.json'], 'flow-wgsl-modules.json'),
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
              'executionIsolation': isolation_metadata,
              'scope': 'Build and functional browser smoke only. No full feature parity, engine promotion, device matrix or realtime claim.'}
    destination = ROOT / 'reports/release-verification.json'
    try:
        for index, (name, argv, output) in enumerate(jobs, 1):
            row = {'name': name, 'command': argv, 'status': 'RUNNING'}
            report['steps'].append(row)
            lab.write_json(destination, report)
            if output == endurance_output:
                # The new child acquires its own window and records it. Holding
                # the same file lock in the parent would deadlock this process.
                command(argv, timeout=10800)
            else:
                window, _ = execution_window(lock_workspace, ci_isolated)
                with window:
                    command(argv, timeout=10800)
            produced = ROOT / 'reports' / output
            phase = ROOT / 'reports/phases' / f'{index:02d}-{output}'
            phase.parent.mkdir(parents=True, exist_ok=True)
            produced_bytes = produced.read_bytes()
            raw = json.loads(produced_bytes)
            if output == endurance_output:
                validate_endurance(raw, manifest, evidence.source_hashes(ROOT, ENDURANCE_SOURCES),
                    not_before=evidence.instant(report['startedUtc'], 'release started'))
            phase.write_bytes(produced_bytes)
            if phase.read_bytes() != produced_bytes:
                raise lab.LabError('Phase receipt changed while preserving it')
            row.update(status='PASS', reportFile=phase.relative_to(ROOT).as_posix(), reportSha256=lab.sha256(phase),
                       resultStatus=raw.get('status'), checks=raw.get('enduranceAdmission', raw.get('checks')),
                       testCount=3 if output == endurance_output else len(raw.get('tests', [])) if isinstance(raw.get('tests'), list) else None)
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
    parser.add_argument('command', choices=['build', 'verify', 'package', 'all', 'build-flow-component'])
    parser.add_argument('--slangc', type=Path, help='Explicit existing pinned Slang 2025.6.1 executable for the isolated Flow source build')
    parser.add_argument('--browser', help='Optional Chrome/Chromium executable')
    isolation = parser.add_mutually_exclusive_group()
    isolation.add_argument('--lock-workspace', type=Path, help='Existing shared host lock for the bounded CPU endurance phase')
    isolation.add_argument('--ci-isolated', action='store_true', help='Explicit isolated GitHub-hosted Actions job; actual environment is checked')
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
        if args.command == 'build-flow-component':
            build_flow_component(args.slangc, args.lock_workspace, args.ci_isolated)
        if args.command in ('build', 'all'):
            build(args.slangc)
        if args.command in ('verify', 'all'):
            verify(args.browser, args.hardware, args.software_vulkan, args.flow_browser_engine,
                   args.lock_workspace, args.ci_isolated)
        if args.command in ('package', 'all'):
            from flow_source_evidence import require_final_selection
            require_final_selection(ROOT)
            from package_release import package
            package()
    except (OSError, ValueError, lab.LabError) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
