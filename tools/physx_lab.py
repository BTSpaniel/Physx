#!/usr/bin/env python3
"""Pinned PhysX WASM migration workbench, Python 3.11+, no npm dependency.

All writes are confined to this kit's work/, dist/ and reports/ directories.
A successful build is NOT a physics-test pass or an engine-integration pass.
The candidate merge is intentionally fail-closed: no automatic ours/theirs resolution.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / 'upstream.lock.json').read_text())
WORK = ROOT / 'work'
REPORTS = ROOT / 'reports'
WASM_MAGIC = b'\x00asm\x01\x00\x00\x00'

class LabError(RuntimeError):
    pass

def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)

def sha256(path: Path) -> str:
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def sdk_version(text: str) -> str:
    values = []
    for name in ('MAJOR', 'MINOR', 'BUGFIX'):
        m = re.search(r'^\s*#\s*define\s+PX_PHYSICS_VERSION_' + name + r'\s+(\d+)\b', text, re.M)
        if not m or int(m[1]) > 255:
            raise LabError('Cannot parse a valid PhysX version header')
        values.append(m[1])
    return '.'.join(values)

def verify_wasm(path: Path) -> None:
    if not path.is_file():
        raise LabError(f'Missing WASM: {path}')
    with path.open('rb') as f:
        magic = f.read(8)
    if magic != WASM_MAGIC or path.stat().st_size <= 8:
        raise LabError(f'Not a nonempty WebAssembly v1 binary: {path}')

def run(args: list[str | Path], cwd: Path = ROOT, *, timeout: int = 3600,
        check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    args = [str(v) for v in args]
    print('+', subprocess.list2cmdline(args), flush=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        p = subprocess.run(args, cwd=cwd, text=True, encoding='utf-8', errors='replace',
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        with (REPORTS / 'commands.log').open('a', encoding='utf-8') as log:
            log.write(f'\n$ {subprocess.list2cmdline(args)}\n{type(e).__name__}: {e}\n')
        raise LabError(f'Command could not run: {e}') from e
    print(p.stdout[-18000:], end='', flush=True)
    with (REPORTS / 'commands.log').open('a', encoding='utf-8') as log:
        log.write(f'\n$ {subprocess.list2cmdline(args)}\nCWD: {cwd}\n{p.stdout}\n'
                  f'EXIT: {p.returncode}; elapsed seconds: {time.time()-started:.3f}\n')
    if check and p.returncode:
        raise LabError(f'Command failed ({p.returncode}); full output: reports/commands.log')
    return p

def git(repo: Path, *args: str, check: bool = True) -> str:
    return run(['git', '-C', repo, *args], check=check).stdout.strip()

def require(programs: list[str]) -> None:
    missing = [p for p in programs if not shutil.which(p)]
    if missing:
        raise LabError('Missing tools: ' + ', '.join(missing) + '. See README.md.')

def doctor() -> dict:
    tools = {x: shutil.which(x) for x in ('git', 'bash', 'cmake', 'make', 'emcc', 'em++')}
    data = {'tools': tools, 'python': sys.version, 'platform': sys.platform,
            'pinned_emscripten': LOCK['emscripten'],
            'emsdk_env': os.environ.get('EMSDK'), 'physx_compiled': False}
    missing = [x for x in ('git', 'bash', 'cmake', 'make', 'emcc', 'em++') if not tools[x]]
    data['status'] = 'BLOCKED' if missing or not data['emsdk_env'] else 'TOOLS_FOUND_NOT_BUILT'
    data['missing'] = missing
    if tools['emcc']:
        data['emcc_version_output'] = run(['emcc', '--version'], timeout=30).stdout
    write_json(REPORTS / 'doctor.json', data)
    print(json.dumps(data, indent=2))
    return data

def fetch() -> None:
    require(['git'])
    base = WORK / 'baseline'
    WORK.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    if not base.exists():
        run(['git', 'clone', '--branch', LOCK['binding_tag'], '--no-recurse-submodules',
             LOCK['binding_repo'], base], timeout=600, env=env)
    if not (base / '.git').exists():
        raise LabError('Baseline folder exists but is not the expected checkout; nothing was deleted.')
    head = git(base, 'rev-parse', 'HEAD')
    if not head.startswith(LOCK['binding_commit_prefix']):
        raise LabError('Binding tag/commit mismatch; refusing to use an unexpected checkout.')
    # Read the actual gitlink rather than assuming the submodule follows a branch.
    tree = git(base, 'ls-tree', 'HEAD', 'PhysX')
    if LOCK['port_commit'] not in tree:
        raise LabError('PhysX submodule pin mismatch; upstream tag contents differ from research.')
    sdk_repo = base / 'PhysX'
    if not (sdk_repo / '.git').exists():
        # The upstream .gitmodules uses SSH. Override only local Git configuration.
        git(base, 'config', 'submodule.PhysX.url', LOCK['port_repo'])
        run(['git', '-C', base, 'submodule', 'update', '--init', 'PhysX'],
            timeout=1800, env=env)
    if git(sdk_repo, 'rev-parse', 'HEAD') != LOCK['port_commit']:
        raise LabError('Existing baseline PhysX checkout does not match its pin. Not resetting it.')
    version = sdk_version((sdk_repo / 'physx/include/foundation/PxPhysicsVersion.h').read_text())
    if version != LOCK['baseline_sdk']:
        raise LabError(f'Unexpected baseline SDK {version}')
    write_json(REPORTS / 'baseline-source.json', {
        'binding_commit': head, 'physx_commit': LOCK['port_commit'], 'sdk_version': version,
        'compiled': False, 'physics_tested': False})


def merge_candidate(repo: Path, target: str) -> list[str]:
    """Merge without committing, preserving conflicts for review; tested with local Git fixtures."""
    run(['git', '-C', repo, 'merge-base', 'HEAD', target])
    result = run(['git', '-C', repo, '-c', 'user.name=PhysX Lab', '-c',
                  'user.email=physx-lab@localhost', 'merge', '--no-commit', '--no-ff', target], check=False)
    conflicts = git(repo, 'diff', '--name-only', '--diff-filter=U').splitlines()
    if result.returncode and not conflicts:
        raise LabError('Merge failed for a reason other than file conflicts; inspect commands.log')
    return [p for p in conflicts if p]


def prepare_upgrade() -> None:
    base = WORK / 'baseline' / 'PhysX'
    target = WORK / 'candidate' / 'PhysX'
    if not (base / '.git').exists():
        raise LabError('Run fetch first.')
    if target.exists():
        raise LabError('Candidate already exists; resolve its conflicts and use build. Not overwriting it.')
    remote = 'pr-upstream'
    remotes = git(base, 'remote').splitlines()
    if remote not in remotes:
        git(base, 'remote', 'add', remote, LOCK['upstream_repo'])
    elif git(base, 'remote', 'get-url', remote) != LOCK['upstream_repo']:
        raise LabError('Unexpected pr-upstream URL')
    ref = 'refs/pr-lab/candidate'
    run(['git', '-C', base, 'fetch', '--no-tags', remote,
         f"refs/tags/{LOCK['upstream_tag']}:{ref}"], timeout=1800,
        env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
    resolved = git(base, 'rev-parse', ref + '^{commit}')
    if not resolved.startswith(LOCK['upstream_commit_prefix']):
        raise LabError('Candidate tag moved or differs from researched commit; refusing to proceed.')
    target.parent.mkdir(parents=True, exist_ok=True)
    git(base, 'worktree', 'add', '--detach', str(target), LOCK['port_commit'])
    conflicts = merge_candidate(target, resolved)
    state = {'upstream_tag': LOCK['upstream_tag'], 'upstream_commit': resolved,
             'port_commit': LOCK['port_commit'], 'conflicts': conflicts,
             'status': 'MERGE_CONFLICTS' if conflicts else 'MERGED_NOT_COMPILED',
             'physics_tested': False}
    write_json(REPORTS / 'candidate-source.json', state)
    if conflicts:
        raise LabError('Resolve merge conflicts in work/candidate/PhysX; paths in reports/candidate-source.json. '
                       'No conflicts were silently discarded.')
    actual = sdk_version((target / 'physx/include/foundation/PxPhysicsVersion.h').read_text())
    if actual != LOCK['candidate_sdk']:
        raise LabError(f'Candidate sources identify as {actual}, not {LOCK["candidate_sdk"]}')


def patch_bulk(cmake_text: str, cpp_path: str) -> str:
    """Add C exports to the SAME WASM module as WebIDL. Never mix object pointers across modules."""
    if '# PR_RUST_BULK_ADDON_V2' in cmake_text:
        raise LabError('Rust addon already enabled; use build.py wasm instead of the old C++ addon.')
    marker = '# PR_BULK_ADDON_V1'
    if marker in cmake_text:
        return cmake_text
    anchor = 'COMMAND em++ glue.o ${PHYSX_LIBS} ${EMCC_WASM_ARGS} -o physx-js-webidl.mjs'
    if cmake_text.count(anchor) != 1:
        raise LabError('Upstream link-command layout changed; refusing an ambiguous CMake patch.')
    dependency = 'DEPENDS physx-js-bindings ${PHYSX_TARGETS}'
    if cmake_text.count(dependency) != 1:
        raise LabError('Upstream dependency layout changed; refusing the CMake patch.')
    # Compile the extension separately with the same release/glue flags, then
    # link it INTO the WebIDL module. Do not change whole-program link optimization.
    header_path = str(Path(cpp_path).with_name('pose_cache.hpp')).replace('\\', '/')
    addon = ('\nADD_CUSTOM_COMMAND(\n  OUTPUT pr_bulk.o\n  COMMAND em++ "' + cpp_path +
             '" ${EMCC_GLUE_ARGS} -std=c++17 -msimd128 -fno-rtti -fno-exceptions -o pr_bulk.o\n' +
             '  DEPENDS "' + cpp_path + '" "' + header_path + '"\n  VERBATIM\n)\n')
    replacement = 'COMMAND em++ glue.o pr_bulk.o ${PHYSX_LIBS} ${EMCC_WASM_ARGS} -o physx-js-webidl.mjs'
    return marker + '\n' + cmake_text.replace(anchor, replacement).replace(
        dependency, dependency + ' pr_bulk.o') + addon


def enable_bulk(repo: Path) -> None:
    dest = repo / 'physx/source/webidlbindings/pr_bulk'
    dest.mkdir(parents=True, exist_ok=True)
    for name in ('pr_bulk.cpp', 'pose_cache.hpp'):
        shutil.copy2(ROOT / 'bridge' / name, dest / name)
    cmake = repo / 'physx/source/compiler/cmake/emscripten/PhysXWasmBindings.cmake'
    cmake.write_text(patch_bulk(cmake.read_text(), (dest / 'pr_bulk.cpp').as_posix()))


def stage(profile: str, source: Path, *, compiled: bool, bulk: bool = False) -> None:
    if profile not in ('baseline', 'candidate'):
        raise LabError('Unknown staging profile')
    stem = 'physx-pe' if profile == 'candidate' else 'physx-js-webidl'
    wasm = source / (stem + '.wasm')
    loader = source / (stem + '.mjs')
    verify_wasm(wasm)
    if not loader.is_file() or loader.stat().st_size < 128:
        raise LabError('Missing or empty matched ES-module loader')
    # Never accept HTML error pages downloaded under a .mjs name.
    if loader.read_text(errors='replace').lstrip().startswith(('<html', '<!DOCTYPE', '<!doctype')):
        raise LabError('The supposed JavaScript loader is an HTML page')
    sdk_repo = WORK / profile / 'PhysX'
    version = sdk_version((sdk_repo / 'physx/include/foundation/PxPhysicsVersion.h').read_text())
    expected = LOCK['baseline_sdk'] if profile == 'baseline' else LOCK['candidate_sdk']
    if version != expected:
        raise LabError(f'Cannot stage SDK {version} under {profile}; expected {expected}')
    dist = ROOT / 'dist'
    dist.mkdir(parents=True, exist_ok=True)
    dest = dist / profile
    tmp = Path(tempfile.mkdtemp(prefix=f'.{profile}-', dir=dist))
    backup = dist / ('.' + profile + '-previous')
    if backup.exists():
        shutil.rmtree(tmp)
        raise LabError(f'Unresolved prior staging backup at {backup}; inspect it before retrying.')
    record = {'profile': profile, 'expected_runtime_version': expected, 'source_version': version,
              'staged_utc': datetime.now(timezone.utc).isoformat(),
              'locally_compiled': compiled, 'bulk_addon_requested': bulk,
              'runtime_verified': False, 'engine_integration_verified': False,
              'artifacts': {name: {'bytes': (source/name).stat().st_size, 'sha256': sha256(source/name)}
                            for name in (wasm.name, loader.name)}}
    if profile == 'baseline':
        record['matches_documented_original_wasm'] = sha256(wasm) == LOCK['baseline_wasm_sha256']
        record['hash_note'] = 'Rebuilt binaries need not be byte-identical to the upstream prebuilt.'
    try:
        for name in record['artifacts']:
            shutil.copy2(source / name, tmp / name)
            if sha256(tmp/name) != record['artifacts'][name]['sha256']:
                raise LabError('Source artifacts changed during staging; refusing the pair.')
        notices = tmp / 'notices'; notices.mkdir()
        notice_candidates = {
            'bindings-LICENSE.txt': WORK / 'baseline/LICENSE',
            'bindings-NOTICE.md': WORK / 'baseline/NOTICE.md',
            'PhysX-LICENSE.md': sdk_repo / 'LICENSE.md',
            'PhysX-LICENSE.txt': sdk_repo / 'LICENSE.txt',
            'PhysX-sdk-LICENSE.md': sdk_repo / 'physx/LICENSE.md',
            'PhysX-sdk-LICENSE.txt': sdk_repo / 'physx/LICENSE.txt',
        }
        copied=[]
        for name, src in notice_candidates.items():
            if src.is_file():
                shutil.copy2(src, notices / name); copied.append(name)
        record['copied_notices'] = copied
        record['license_note'] = 'Retain complete upstream notices. Check dependency notices before redistribution.'
        write_json(tmp / 'build-manifest.json', record)
        # Validate fully first; never publish one half of a matched loader/WASM pair.
        if dest.exists(): dest.rename(backup)
        try: tmp.rename(dest)
        except OSError:
            if backup.exists(): backup.rename(dest)
            raise
        if backup.exists(): shutil.rmtree(backup)
    finally:
        if tmp.exists(): shutil.rmtree(tmp)
    write_json(REPORTS / (profile + '-build.json'), record)
    print(f'Staged {profile}; runtime and engine integration remain UNVERIFIED.')


def build(profile: str, *, bulk: bool = False) -> None:
    require(['bash', 'cmake', 'make', 'emcc', 'em++'])
    if not os.environ.get('EMSDK'):
        raise LabError('Activate emsdk_env first. The upstream CMake file requires EMSDK.')
    emver = run(['emcc', '--version'], timeout=30).stdout
    if not re.search(r'\b' + re.escape(LOCK['emscripten']) + r'\b', emver):
        raise LabError(f"This controlled comparison pins Emscripten {LOCK['emscripten']}; activate that version.")
    repo = WORK / profile / 'PhysX'
    if not repo.exists():
        raise LabError('Sources are missing; use fetch / prepare-upgrade first.')
    conflicts = git(repo, 'diff', '--name-only', '--diff-filter=U')
    if conflicts:
        raise LabError('Unresolved merge conflicts; build refused:\n' + conflicts)
    actual = sdk_version((repo / 'physx/include/foundation/PxPhysicsVersion.h').read_text())
    expected = LOCK['baseline_sdk'] if profile == 'baseline' else LOCK['candidate_sdk']
    if actual != expected:
        raise LabError(f'Header version mismatch: {actual} != {expected}')
    addon_cmake = repo / 'physx/source/compiler/cmake/emscripten/PhysXWasmBindings.cmake'
    if addon_cmake.is_file() and '# PR_RUST_BULK_ADDON_V2' in addon_cmake.read_text():
        raise LabError('This checkout uses the Rust addon; build with python build.py wasm.')
    if not bulk and addon_cmake.is_file() and '# PR_BULK_ADDON_V1' in addon_cmake.read_text():
        raise LabError('This checkout already contains the addon patch. Use --bulk or a clean vanilla checkout; refusing to mislabel the build.')
    if bulk:
        enable_bulk(repo)
    write_json(REPORTS / (profile + '-build-inputs.json'), {
        'head': git(repo, 'rev-parse', 'HEAD'), 'status': git(repo, 'status', '--porcelain'),
        'emscripten_output': emver, 'compiler_flags_note': 'Port release preset; single-thread SIMD',
        'bulk_source_sha256': {n: sha256(ROOT / 'bridge' / n) for n in ('pr_bulk.cpp', 'pose_cache.hpp')} if bulk else {},
        'compiled': False, 'physics_tested': False})
    sdk = repo / 'physx'
    # Use the port's own project generator and preset, not the native ovphysx build scripts.
    run(['bash', './generate_projects.sh', 'emscripten'], cwd=sdk, timeout=1800)
    out = sdk / 'compiler/emscripten-release'
    if not (out / 'CMakeCache.txt').is_file():
        raise LabError('Project generator did not produce the expected Emscripten release build directory.')
    run(['cmake', '--build', out, '--parallel', str(min(os.cpu_count() or 2, 8))], timeout=7200)
    stage(profile, out / 'sdk_source_bin', compiled=True, bulk=bulk)
    # Preserve the actual local source delta, including any reviewed conflict resolutions.
    patch = run(['git', '-C', repo, 'diff', '--binary', 'HEAD']).stdout
    (REPORTS / (profile + '-working-tree.patch')).write_text(patch)


def audit(engine: Path) -> dict:
    if not engine.is_dir():
        raise LabError('Engine source directory does not exist')
    hits = []
    hazards = ('PxSoftBody', 'PxFEMSoftBody', 'PxParticleCloth', 'getSolverResidual',
               'eENABLE_SOLVER_RESIDUAL_REPORTING', 'PxDefaultMemoryOutputStream',
               'PxDefaultMemoryInputData', 'getLength', 'pushBack')
    excluded = {'.git', 'node_modules', 'work', '__pycache__'}
    binaries = []
    for parent, dirs, names in os.walk(engine):
        dirs[:] = [d for d in dirs if d not in excluded]
        for name in names:
            path = Path(parent) / name
            if name == 'physx-js-webidl.wasm':
                binaries.append({'path': str(path), 'sha256': sha256(path), 'bytes': path.stat().st_size})
            if path.suffix not in {'.js', '.mjs', '.cpp', '.h', '.idl'} or path.stat().st_size > 12_000_000:
                continue
            for i, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
                terms = [term for term in hazards if term in line]
                if terms:
                    hits.append({'path': str(path.relative_to(engine)), 'line': i, 'terms': terms,
                                 'excerpt': line[:320]})
    data = {'warning': 'Lexical review candidates, NOT proven incompatibilities; dynamic code can be missed.',
            'engine_root': str(engine.resolve()), 'binaries': binaries, 'review_hits': hits}
    write_json(REPORTS / 'engine-audit.json', data)
    print(f'{len(binaries)} binary candidates; {len(hits)} source-review candidates. See engine-audit.json.')
    return data


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest='command', required=True)
    subs.add_parser('doctor')
    subs.add_parser('fetch')
    subs.add_parser('prepare-upgrade')
    b = subs.add_parser('build'); b.add_argument('--profile', choices=['baseline', 'candidate'], required=True)
    b.add_argument('--bulk', action='store_true', help='Add the experimental C pose-buffer extension in the same WASM')
    s = subs.add_parser('stage-prebuilt'); s.add_argument('--source', type=Path, required=True,
        help='Directory containing the matched original v2.7.3 .mjs and .wasm files')
    a = subs.add_parser('audit'); a.add_argument('--engine', type=Path, required=True)
    args = p.parse_args()
    try:
        if args.command == 'doctor':
            return 2 if doctor()['status'] == 'BLOCKED' else 0
        if args.command == 'fetch': fetch()
        elif args.command == 'prepare-upgrade': prepare_upgrade()
        elif args.command == 'build': build(args.profile, bulk=args.bulk)
        elif args.command == 'stage-prebuilt':
            if sha256(args.source / 'physx-js-webidl.wasm') != LOCK['baseline_wasm_sha256']:
                raise LabError('The prebuilt WASM does not match the documented v2.7.3 baseline.')
            stage('baseline', args.source.resolve(), compiled=False)
        elif args.command == 'audit': audit(args.engine)
        return 0
    except (LabError, OSError, ValueError) as e:
        record = {'command': args.command, 'status': 'BLOCKED_OR_FAILED', 'error': str(e),
                  'physx_compiled_and_verified': False}
        write_json(REPORTS / 'last-failure.json', record)
        print('ERROR:', e, file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
