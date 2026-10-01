# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Validate the isolated source kit on Windows and serialize its WSL build.

The host's existing lock is imported, not redistributed. Windows owns the
authoritative source/tool snapshots; the selected recipe processes and their
supplemental validators execute in WSL. No CI environment is manufactured.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import physx_lab as lab
from browser_compat_endurance import host_lock, power_observation
from flow_source_evidence import shader_inventory, upstream_inputs
from prepare_sources import prepare_flow_sources
from release import inventory, selection

RECIPES = ('compile_wgsl.py', 'compile_solid_wgsl.py',
           'compile_scalar_wgsl.py', 'compile_momentum_wgsl.py')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reference_inputs() -> dict:
    """Bind frozen provenance, including output inventories excluded by release.inventory."""
    folder = ROOT / 'reference'
    result = {path.relative_to(ROOT).as_posix(): {'bytes': path.stat().st_size, 'sha256': lab.sha256(path)}
              for path in sorted(folder.rglob('*')) if path.is_file() and path.suffix != '.pyc'
              and '__pycache__' not in path.parts}
    selected = json.loads((ROOT / 'source-selection.json').read_text(encoding='utf-8'))['flowComponentOrigin']
    for relative, field in (
            ('reference/flow-component-01/dist/flow-component/manifest.json', 'manifestSha256'),
            ('reference/flow-component-01/contracts/integration-handoff-01.json', 'handoffSha256')):
        if result[relative]['sha256'] != selected[field]:
            raise lab.LabError('Frozen Flow provenance manifest changed: ' + relative)
    return result


def compiler_version_matches(name: str, expected: str, stdout: str, stderr: str) -> bool:
    # Slang's real -version writes to stderr; retain both original channels.
    combined = stdout + stderr
    if name == 'slang':
        return combined.strip() == expected
    lines = combined.splitlines()
    return bool(lines and expected in lines[0].split())


def linux_path(path: Path) -> str:
    # Preserve Linux alias names; Windows cannot resolve LX symlink targets.
    path = path.absolute()
    if not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ':':
        raise lab.LabError('An actual Windows drive path is required: ' + str(path))
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def tools_snapshot(emsdk: Path, slangc: Path) -> dict:
    """Bind real compiler entrypoints, Python support and native compiler bins."""
    emscripten = emsdk / 'upstream/emscripten'
    inputs = {emsdk / '.emscripten', emscripten / 'em++', emscripten / 'emcc',
              emscripten / 'emcc.py', emsdk / 'upstream/bin/lld', slangc}
    # Linux tool aliases are LX symlinks which native Windows cannot dereference.
    # Bind their actual regular binaries here and separately record readlink in
    # the locked compiler identity step below.
    inputs.update(p for p in (emsdk / 'upstream/bin').glob('clang-*') if p.is_file())
    inputs.update(emscripten.glob('*.py'))
    inputs.update((emscripten / 'tools').rglob('*.py'))
    inputs.update(p for p in slangc.parent.iterdir() if p.is_file())
    inputs.update(p for p in (slangc.parent.parent / 'lib').rglob('*') if p.is_file())
    for path in inputs:
        if not path.is_file():
            raise lab.LabError('Selected compiler input is missing: ' + str(path))
    expected = json.loads((ROOT / 'reference/flow-component-01/dist/flow-wgsl/manifest.json')
                          .read_text(encoding='utf-8'))['slangSha256']
    if lab.sha256(slangc) != expected:
        raise lab.LabError('Slang compiler bytes differ from the frozen component pin')
    return {str(path.resolve()): {'bytes': path.stat().st_size, 'sha256': lab.sha256(path)}
            for path in sorted(inputs)}


def wsl_invocation(argv: list[str], emsdk: Path, distro: str,
                   rust_toolchain: Path | None = None) -> list[str]:
    env = {'EMSDK': linux_path(emsdk), 'EM_CONFIG': linux_path(emsdk / '.emscripten'),
           'EMSDK_PYTHON': '/usr/bin/python3', 'PYTHON': '/usr/bin/python3'}
    script = 'set -euo pipefail; '
    script += ' '.join('export ' + key + '=' + shlex.quote(value) + ';' for key, value in env.items())
    search = '/usr/bin:/bin:' + linux_path(emsdk / 'upstream/emscripten')
    if rust_toolchain is not None:
        search = linux_path(rust_toolchain / 'bin') + ':' + search
    script += ' export PATH=' + shlex.quote(search) + '; '
    script += 'cd ' + shlex.quote(linux_path(ROOT)) + '; exec ' + shlex.join(argv)
    return ['wsl.exe', '-d', distro, '--', 'bash', '-c', script]


def rust_toolchain_snapshot(toolchain: Path, emsdk: Path, distro: str) -> dict:
    """Read the existing compiler/sysroot bytes in WSL; never invoke rustup/install."""
    if not toolchain.is_dir() or not (toolchain / 'bin/rustc').is_file():
        raise lab.LabError('Choose an actual existing Rust toolchain with bin/rustc')
    probe = r'''import hashlib,json,os,pathlib,shutil,subprocess,sys
root=pathlib.Path(sys.argv[1]).resolve(strict=True); expected=sys.argv[2]; target='wasm32-unknown-emscripten'
compiler=(root/'bin/rustc').resolve(strict=True)
if not compiler.is_file() or not compiler.is_relative_to(root):raise RuntimeError('Compiler leaves its selected sysroot')
if pathlib.Path(shutil.which('rustc') or '').resolve()!=compiler:raise RuntimeError('PATH selects a different Rust compiler')
def run(*args):
 p=subprocess.run([str(compiler),*args],capture_output=True,text=True,check=True,timeout=30);return p.stdout.strip()
version=run('-Vv'); fields=dict(line.split(': ',1) for line in version.splitlines()[1:] if ': ' in line)
if fields.get('release')!=expected or fields.get('host')!='x86_64-unknown-linux-gnu':raise RuntimeError('Different Rust release/host')
if pathlib.Path(run('--print','sysroot')).resolve()!=root:raise RuntimeError('Compiler uses a different sysroot')
library=pathlib.Path(run('--print','target-libdir','--target',target)).resolve(strict=True)
if not library.is_dir() or not library.is_relative_to(root) or not any(library.glob('libcore-*.rlib')):raise RuntimeError('Installed Rust Emscripten core target missing')
paths={root/'bin/rustc'}
paths.update(p for p in (root/'lib').iterdir() if p.is_file())
for name in (fields['host'],target):
 folder=root/'lib/rustlib'/name
 if not folder.is_dir():raise RuntimeError('Installed compiler/target library tree missing: '+name)
 paths.update(p for p in folder.rglob('*') if p.is_file())
files={}
for path in sorted(paths):
 actual=path.resolve(strict=True)
 if not actual.is_file() or not actual.is_relative_to(root):raise RuntimeError('Compiler dependency leaves selected sysroot: '+str(path))
 with actual.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
 files[path.relative_to(root).as_posix()]={'bytes':actual.stat().st_size,'sha256':digest,'resolvedPath':str(actual)}
print(json.dumps({'schema':'physx-pe.existing-rust-toolchain/v1','status':'PINNED_COMPILER_AND_INSTALLED_TARGET_ADMITTED','sysroot':str(root),'compiler':str(compiler),'version':version,'release':fields['release'],'host':fields['host'],'target':target,'targetLibdir':str(library),'files':files,'installersInvoked':False,'nativeCodeCompiled':False}))'''
    invocation = wsl_invocation(['/usr/bin/python3', '-c', probe, linux_path(toolchain), lab.LOCK['rust']],
                                emsdk, distro, toolchain)
    result = subprocess.run(invocation, cwd=ROOT, capture_output=True, text=True,
                            encoding='utf-8', timeout=300, check=False)
    if result.returncode:
        raise lab.LabError('Existing Rust toolchain admission failed: ' + result.stdout + result.stderr)
    admitted = json.loads(result.stdout)
    if (admitted.get('release') != lab.LOCK['rust']
            or admitted.get('target') != 'wasm32-unknown-emscripten'
            or admitted.get('status') != 'PINNED_COMPILER_AND_INSTALLED_TARGET_ADMITTED'
            or not admitted.get('files') or admitted.get('installersInvoked') is not False):
        raise lab.LabError('Existing Rust compiler receipt is incomplete')
    return admitted | {'invocation': invocation, 'exitCode': result.returncode,
                       'stderr': result.stderr}


def run_record(argv: list[str], receipt: dict, destination: Path, log: Path) -> dict:
    row = {'argv': argv, 'startedUtc': now(), 'status': 'RUNNING'}
    receipt['steps'].append(row)
    lab.write_json(destination, receipt)
    started = time.perf_counter()
    print('Executing ' + str(len(receipt['steps'])) + ': ' + argv[0], flush=True)
    with log.open('a', encoding='utf-8') as stream:
        stream.write('\nCOMMAND ' + json.dumps(argv) + '\n')
        stream.flush()
        result = subprocess.run(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                timeout=7200, check=False)
    row.update(exitCode=result.returncode, elapsedSeconds=time.perf_counter() - started,
               finishedUtc=now(), status='PASS' if result.returncode == 0 else 'FAIL')
    lab.write_json(destination, receipt)
    if result.returncode:
        raise lab.LabError('Selected command failed; original output is retained in ' + str(log))
    return row


def native_aliases(emsdk: Path, distro: str, captured: dict) -> dict:
    aliases = {}
    for name in ('clang++', 'wasm-ld'):
        alias = linux_path(emsdk / 'upstream/bin' / name)
        invocation = wsl_invocation(['readlink', '-f', alias], emsdk, distro)
        result = subprocess.run(invocation, cwd=ROOT, capture_output=True, text=True,
                                encoding='utf-8', timeout=60, check=False)
        target = result.stdout.strip()
        expected_prefix = linux_path(emsdk / 'upstream/bin') + '/'
        if result.returncode or not target.startswith(expected_prefix):
            raise lab.LabError('Actual native compiler alias leaves the selected tool tree: ' + name)
        actual = emsdk / 'upstream/bin' / target[len(expected_prefix):]
        if str(actual.resolve()) not in captured:
            raise lab.LabError('Native compiler alias target was not captured on Windows: ' + name)
        aliases[name] = {
            'invocation': invocation, 'target': target, 'exitCode': result.returncode,
            'stdout': result.stdout, 'stderr': result.stderr,
            'actualBinary': captured[str(actual.resolve())]}
    return aliases


def build(emsdk: Path, slangc: Path, distro: str, receipt: dict, destination: Path, log: Path) -> None:
    before_tools = tools_snapshot(emsdk, slangc)
    receipt['compilerInputsBefore'] = before_tools
    receipt['nativeCompilerAliases'] = native_aliases(emsdk, distro, before_tools)
    for name, argv, version in (
            ('emscripten', ['em++', '--version'], lab.LOCK['emscripten']),
            ('slang', [linux_path(slangc), '-version'], '2025.6.1')):
        invocation = wsl_invocation(argv, emsdk, distro)
        result = subprocess.run(invocation, cwd=ROOT, capture_output=True, text=True,
                                encoding='utf-8', timeout=60, check=False)
        receipt.setdefault('compilerIdentities', {})[name] = {
            'argv': argv, 'invocation': invocation, 'exitCode': result.returncode,
            'stdout': result.stdout, 'stderr': result.stderr}
        lab.write_json(destination, receipt)
        matched = compiler_version_matches(name, version, result.stdout, result.stderr)
        if result.returncode or not matched:
            raise lab.LabError('Actual compiler identity differs from its pin: ' + name)
    prepare_flow_sources()
    for name in RECIPES:
        argv = ['/usr/bin/python3', 'addons/flow/' + name, '--slangc', linux_path(slangc)]
        run_record(wsl_invocation(argv, emsdk, distro), receipt, destination, log)
    receipt['shaderInventory'] = shader_inventory(ROOT)
    component_report = 'reports/' + destination.stem + '-component.json'
    if (ROOT / component_report).exists():
        raise lab.LabError('The component receipt already exists; preserve it')
    argv = ['/usr/bin/python3', 'addons/flow/build_component.py', '--report', component_report]
    run_record(wsl_invocation(argv, emsdk, distro), receipt, destination, log)
    component = json.loads((ROOT / component_report).read_text(encoding='utf-8'))
    if (component.get('status') != 'COMPILED_NOT_RUNTIME_VERIFIED'
            or component.get('sourceHashesBefore') != receipt['sourceHashesBefore']
            or component.get('sourceHashesAfter') != receipt['sourceHashesBefore']
            or component.get('shaderInventory') != receipt['shaderInventory']):
        raise lab.LabError('Fresh native component does not bind this Windows source/shader selection')
    receipt['component'] = component
    receipt['componentReport'] = {'path': component_report, 'bytes': (ROOT / component_report).stat().st_size,
                                   'sha256': lab.sha256(ROOT / component_report)}
    receipt['compilerInputsAfter'] = tools_snapshot(emsdk, slangc)
    if receipt['compilerInputsAfter'] != before_tools:
        raise lab.LabError('Compiler bytes changed during the source build')
    receipt['nativeCompilerAliasesAfter'] = native_aliases(emsdk, distro, receipt['compilerInputsAfter'])
    if receipt['nativeCompilerAliasesAfter'] != receipt['nativeCompilerAliases']:
        raise lab.LabError('Native compiler aliases changed during the source build')
    receipt['shaderCompilerExecutions'] = [row['command'] for name in (
        'manifest.json', 'addons/solid/manifest.json', 'addons/scalar/manifest.json', 'addons/momentum/manifest.json')
        for row in json.loads((ROOT / 'dist/flow-wgsl' / name).read_text(encoding='utf-8'))['shaders']]
    if len(receipt['shaderCompilerExecutions']) != 124:
        raise lab.LabError('Incomplete actual Slang argv evidence')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('typecheck', 'flow-build', 'full-build'))
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--lock-workspace', type=Path, required=True)
    parser.add_argument('--emsdk', type=Path)
    parser.add_argument('--slangc', type=Path)
    parser.add_argument('--rust-toolchain', type=Path,
                        help='Existing Linux Rust sysroot on a Windows drive; required for full-build')
    parser.add_argument('--wsl-distro', default='Ubuntu-24.04')
    parser.add_argument('--chromium', type=Path)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Use native Windows Python; WSL fcntl is not this host lock')
    if args.phase in ('flow-build', 'full-build') and (args.emsdk is None or args.slangc is None):
        parser.error('Flow compilation requires the actual existing Emscripten and Slang paths')
    if args.phase == 'full-build' and args.rust_toolchain is None:
        parser.error('Full compilation requires the explicit existing pinned --rust-toolchain')
    destination = args.report.resolve()
    if not destination.is_relative_to((ROOT / 'reports').resolve()) or destination.exists():
        parser.error('Choose a fresh contained report path; previous receipts are preserved')
    log = destination.with_suffix('.log')
    if log.exists():
        parser.error('The log already exists; preserve it')
    selection()
    before = inventory(ROOT)
    references_before = reference_inputs()
    upstream_inputs(ROOT)
    lock, lock_metadata = host_lock(args.lock_workspace)
    receipt = {'schema': 'physx-pe.source-kit-windows-phase/v1', 'phase': args.phase,
               'status': 'WAITING_FOR_SHARED_LOCK', 'startedUtc': now(), 'steps': [],
               'sourceRoot': str(ROOT), 'sourceHashesBefore': before,
               'referenceInputsBefore': references_before,
               'sharedPerformanceLock': lock_metadata, 'powerAtStart': power_observation(),
               'authoritativeValidationHost': 'native-windows-python',
               'compilerRecipeHost': args.wsl_distro if args.phase in ('flow-build', 'full-build') else None,
               'runtimeExecuted': False, 'gpuExecuted': False, 'releaseAdmitted': False,
               'scope': 'Bounded prospective source/type/build proof only; no runtime, installation or release admission.'}
    lab.write_json(destination, receipt)
    try:
        with lock:
            receipt['lockAcquiredUtc'] = now()
            if inventory(ROOT) != before or reference_inputs() != references_before:
                raise lab.LabError('Source kit changed while waiting for the actual Windows shared lock')
            selection()
            if args.phase == 'typecheck':
                command = [sys.executable, str(ROOT / 'tools/typecheck_browser.py')]
                if args.chromium:
                    command.extend(['--chromium', str(args.chromium)])
                run_record(command, receipt, destination, log)
                semantic_path = ROOT / 'reports/addon-types-browser.json'
                receipt['semantic'] = json.loads(semantic_path.read_text(encoding='utf-8'))
                receipt['semanticReportSha256'] = lab.sha256(semantic_path)
                if receipt['semantic'].get('status') != 'TYPESCRIPT_DECLARATIONS_PASSED':
                    raise lab.LabError('The actual TypeScript consumer did not pass')
            elif args.phase == 'flow-build':
                build(args.emsdk.resolve(), args.slangc.resolve(), args.wsl_distro,
                      receipt, destination, log)
            else:
                from flow_source_evidence import require_final_selection
                from native_components import selected_source_inputs
                from release import source_revision
                require_final_selection(ROOT)
                selected_source_inputs(ROOT)
                # Both Windows and WSL independently require the same clean
                # committed complete source tree; no source identity bypass.
                receipt['sourceRevisionBefore'] = source_revision(before, ROOT)
                receipt['compilerInputsBefore'] = tools_snapshot(args.emsdk.resolve(), args.slangc.resolve())
                receipt['nativeCompilerAliases'] = native_aliases(args.emsdk.resolve(), args.wsl_distro, receipt['compilerInputsBefore'])
                rust = args.rust_toolchain.resolve()
                receipt['rustCompilerInputsBefore'] = rust_toolchain_snapshot(rust, args.emsdk.resolve(), args.wsl_distro)
                lab.write_json(destination, receipt)
                argv = ['/usr/bin/python3', 'release.py', 'build', '--slangc', linux_path(args.slangc.resolve())]
                run_record(wsl_invocation(argv, args.emsdk.resolve(), args.wsl_distro, rust), receipt, destination, log)
                receipt['fullSourceBuild'] = json.loads((ROOT / 'reports/release-build.json').read_text())
                if (receipt['fullSourceBuild'].get('status') != 'COMPILED_NOT_RUNTIME_VERIFIED'
                        or receipt['fullSourceBuild'].get('sourceHashes') != before
                        or receipt['fullSourceBuild'].get('sourceHashesAfter') != before
                        or receipt['fullSourceBuild'].get('sourceRevision') != receipt['sourceRevisionBefore']):
                    raise lab.LabError('Fresh full build does not bind this Windows source revision')
                receipt['compilerInputsAfter'] = tools_snapshot(args.emsdk.resolve(), args.slangc.resolve())
                if receipt['compilerInputsAfter'] != receipt['compilerInputsBefore']:
                    raise lab.LabError('Compiler inputs changed during full source compilation')
                receipt['nativeCompilerAliasesAfter'] = native_aliases(args.emsdk.resolve(), args.wsl_distro, receipt['compilerInputsAfter'])
                if receipt['nativeCompilerAliasesAfter'] != receipt['nativeCompilerAliases']:
                    raise lab.LabError('Native compiler aliases changed during full source compilation')
                receipt['rustCompilerInputsAfter'] = rust_toolchain_snapshot(rust, args.emsdk.resolve(), args.wsl_distro)
                if receipt['rustCompilerInputsAfter'] != receipt['rustCompilerInputsBefore']:
                    raise lab.LabError('Existing Rust compiler/target bytes changed during full source compilation')
                if source_revision(inventory(ROOT), ROOT) != receipt['sourceRevisionBefore']:
                    raise lab.LabError('Committed source revision changed during full source compilation')
            receipt['sourceHashesAfter'] = inventory(ROOT)
            receipt['referenceInputsAfter'] = reference_inputs()
            if receipt['sourceHashesAfter'] != before or receipt['referenceInputsAfter'] != references_before:
                raise lab.LabError('Source kit changed during the selected phase')
            selection()
            receipt['status'] = ('SEMANTIC_TYPES_PASSED' if args.phase == 'typecheck'
                                 else 'FRESH_FLOW_SOURCE_COMPILED_NOT_RUNTIME_VERIFIED' if args.phase == 'flow-build'
                                 else 'FULL_SOURCE_COMPILED_NOT_RUNTIME_VERIFIED')
    except Exception as error:
        receipt.update(status='FAIL', error=str(error), sourceHashesAfter=inventory(ROOT))
    finally:
        receipt.update(finishedUtc=now(), powerAtEnd=power_observation())
        if log.exists():
            receipt['logSha256'] = lab.sha256(log)
        lab.write_json(destination, receipt)
    print(json.dumps({key: receipt[key] for key in ('status', 'phase', 'sourceRoot', 'releaseAdmitted')}
                     | {'report': str(destination), 'error': receipt.get('error')}, indent=2))
    return 0 if receipt['status'] != 'FAIL' else 2


if __name__ == '__main__':
    raise SystemExit(main())
