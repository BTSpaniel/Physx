#!/usr/bin/env python3
"""Rust + Python extension of the PhysX WASM workbench (Python 3.11+).

This builds a Rust batching layer, NOT a translation of NVIDIA's solver.
The SDK migration must still compile and pass the browser/engine test gates.
No shell=True, npm, third-party Python dependencies, or automatic installers.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tools'))
import physx_lab as lab
import evidence as evidence_tools
from addons.blast.prepare_stress_sources import prepare as prepare_stress_sources
from addons.blast.prepare_authoring_sources import authoring_layout, prepare as prepare_authoring_sources

RUST = ROOT / 'rust/src/lib.rs'
TARGET = 'wasm32-unknown-emscripten'
MARKER = '# PR_RUST_BULK_ADDON_V2'
BLAST_MARKER = '# PR_BLAST_CORE_ADDON_V1'
REPORTS = ROOT / 'reports'


def record(name: str, value: Any) -> None:
    lab.write_json(REPORTS / name, value)


def command(args: list[str | Path], *, env: dict[str, str] | None = None,
            check: bool = True, timeout: int = 3600) -> subprocess.CompletedProcess:
    return lab.run(args, cwd=ROOT, env=env, check=check, timeout=timeout)


def doctor() -> dict[str, Any]:
    tools = {name: shutil.which(name) for name in
             ('python3', 'rustc', 'cargo', 'rustup', 'git', 'bash', 'cmake', 'make', 'emcc', 'em++')}
    data: dict[str, Any] = {'utc': datetime.now(timezone.utc).isoformat(), 'python': sys.version,
        'tools': tools, 'target': TARGET, 'emscripten_pin': lab.LOCK['emscripten'],
        'source_target': lab.LOCK['candidate_sdk'], 'compiled': False, 'physics_tested': False}
    if tools['rustc']:
        data['rustc'] = command(['rustc', '-Vv'], check=False, timeout=30).stdout
        targetdir = command(['rustc', '--print', 'target-libdir', '--target', TARGET],
                            check=False, timeout=30)
        d = Path(targetdir.stdout.strip())
        data['rust_target_core_installed'] = targetdir.returncode == 0 and d.is_dir() and any(d.glob('libcore-*.rlib'))
    else:
        data['rust_target_core_installed'] = False
    if tools['emcc']:
        data['emcc'] = command(['emcc', '--version'], check=False, timeout=30).stdout
    data['native_rust_missing'] = [n for n in ('rustc',) if not tools[n]]
    data['wasm_missing'] = [n for n in ('rustc', 'git', 'bash', 'cmake', 'make', 'emcc', 'em++') if not tools[n]]
    if not data['rust_target_core_installed']:
        data['wasm_missing'].append('Rust Emscripten core target')
    if not os.environ.get('EMSDK'):
        data['wasm_missing'].append('activated EMSDK')
    data['status'] = 'BLOCKED' if data['wasm_missing'] else 'TOOLS_PRESENT_NOT_COMPILED'
    record('rust-python-doctor.json', data)
    print(json.dumps(data, indent=2))
    return data


def native_filename(platform: str = sys.platform) -> str:
    if platform == 'win32':
        return 'pr_pose_core.dll'
    if platform == 'darwin':
        return 'libpr_pose_core.dylib'
    return 'libpr_pose_core.so'


def rust_args(kind: str, output: Path) -> list[str | Path]:
    if kind not in ('test', 'native', 'wasm'):
        raise lab.LabError('Unknown Rust build kind')
    args: list[str | Path] = ['rustc', '--edition=2021', '--crate-name=pr_pose_core', RUST, '-Dwarnings']
    if kind == 'test':
        return args + ['--test', '-o', output]
    args += ['-Copt-level=3', '-Cpanic=abort', '-Clto=off', '-Ccodegen-units=1']
    if kind == 'native':
        args += ['--crate-type=cdylib']
    else:
        args += ['--crate-type=staticlib', '--target', TARGET, '-Ctarget-feature=+simd128']
    return args + ['-o', output]


def compile_native() -> Path:
    lab.require(['rustc'])
    out = ROOT / 'work/rust-native'
    out.mkdir(parents=True, exist_ok=True)
    lib = out / native_filename()
    command(rust_args('native', lib))
    if not lib.is_file():
        raise lab.LabError('rustc exited without producing the native library')
    record('rust-native-build.json', {'compiled': True, 'physics_solver_included': False,
        'library': str(lib.relative_to(ROOT)), 'sha256': lab.sha256(lib),
        'rust_source_sha256': lab.sha256(RUST), 'ffi_tested': False})
    return lib


def run_ffi_tests(lib: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, PR_RUST_LIB=str(lib.resolve()))
    return command([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                    '-p', 'test_rust_ffi*.py', '-v'], env=env, check=False)


def cmake_literal(path: Path | str) -> str:
    text = str(path).replace('\\', '/')
    if any(c in text for c in ('"', '\n', '\r', ';', '$')):
        raise lab.LabError('Unsupported special character in CMake build path')
    return '"' + text + '"'


def patch_rust(cmake_text: str, cpp: Path | str, header: Path | str, archive: Path | str) -> str:
    # Never combine the old C++ cache addon and the Rust cache addon.
    if '# PR_BULK_ADDON_V1' in cmake_text:
        raise lab.LabError('Old C++ addon already enabled. Use a fresh source worktree for the Rust build.')
    cppq, headerq, archiveq = map(cmake_literal, (cpp, header, archive))
    # WebIDL glue and the addon must use the same ABI/exception settings.
    flags = '-std=c++17 -msimd128 -fno-rtti -fno-exceptions'
    wrapper = 'COMMAND em++ ${PHYSXWASM_GLUE_WRAPPER} ${EMCC_GLUE_ARGS}'
    old_wrapper, new_wrapper = wrapper + ' -o glue.o', wrapper + ' ' + flags + ' -o glue.o'
    if cmake_text.count(old_wrapper) + cmake_text.count(new_wrapper) != 1:
        raise lab.LabError('Expected one WebIDL glue compile command; refusing ambiguous ABI flags')
    cmake_text = cmake_text.replace(old_wrapper, new_wrapper)
    anchor = 'COMMAND em++ glue.o ${PHYSX_LIBS} ${EMCC_WASM_ARGS} -o physx-js-webidl.mjs'
    legacy_link = f'COMMAND em++ glue.o pr_bulk_rust.o {archiveq} ${{PHYSX_LIBS}} ${{EMCC_WASM_ARGS}} -o physx-js-webidl.mjs'
    # Prefer this Emscripten's libc/compiler runtime over Rust's bundled weak
    # libm/quad-float builtins, which were compiled with a different EH ABI.
    link = legacy_link.replace('pr_bulk_rust.o ', 'pr_bulk_rust.o -lc -lcompiler_rt ', 1)
    if MARKER in cmake_text:
        if cmake_text.count(MARKER) != 1 or not all(v in cmake_text for v in (cppq, headerq, archiveq)):
            raise lab.LabError('Rust addon patch does not match current source/archive paths')
        legacy_blast_link = legacy_link.replace('pr_bulk_rust.o ', 'pr_bulk_rust.o pr_blast.o ', 1)
        blast_link = link.replace('pr_bulk_rust.o ', 'pr_bulk_rust.o pr_blast.o ', 1)
        if sum(cmake_text.count(value) for value in
               (legacy_link, link, legacy_blast_link, blast_link)) != 1:
            raise lab.LabError('Expected one matching Rust link command')
        return cmake_text.replace(legacy_link, link).replace(legacy_blast_link, blast_link)
    depend = 'DEPENDS physx-js-bindings ${PHYSX_TARGETS}'
    if cmake_text.count(anchor) != 1 or cmake_text.count(depend) != 1:
        raise lab.LabError('CMake link layout changed; refusing to guess the Rust link patch')
    addon = (f'\n{MARKER}\nADD_CUSTOM_COMMAND(\n  OUTPUT pr_bulk_rust.o\n'
             f'  COMMAND em++ {cppq} ${{EMCC_GLUE_ARGS}} {flags} -o pr_bulk_rust.o\n'
             f'  DEPENDS {cppq} {headerq}\n  VERBATIM\n)\n')
    return cmake_text.replace(anchor, link).replace(depend, depend + f' pr_bulk_rust.o {archiveq}') + addon


def blast_layout(root: Path, *, stress: bool = True, authoring: bool = False) -> tuple[list[Path], list[Path]]:
    version = root / 'VERSION.md'
    if not version.is_file() or version.read_text(encoding='utf-8').strip() != '5.0.6':
        raise lab.LabError('Candidate must contain pinned NVIDIA Blast 5.0.6')
    sources = sorted((root / 'source/sdk/common').glob('*.cpp'))
    sources += sorted((root / 'source/sdk/lowlevel').glob('*.cpp'))
    if len(sources) != 10:
        raise lab.LabError(f'Expected 10 inspected Blast core sources, found {len(sources)}')
    includes = [root / 'include/lowlevel', root / 'include/shared/NvFoundation',
        root / 'source/sdk/common', root / 'source/sdk/lowlevel',
        root / 'source/shared/NsFoundation/include']
    if stress:
        sources += [root / 'source/sdk/extensions/stress/NvBlastExtStressSolver.cpp',
                    root / 'source/shared/stress_solver/stress.cpp',
                    root / 'source/sdk/globals/NvBlastGlobals.cpp']
        includes += [root / 'include/extensions/stress', root / 'include/globals',
                     root / 'source/shared/stress_solver', ROOT / 'addons/blast']
    if authoring:
        if not stress:
            raise lab.LabError('Authored physical families require the inspected stress/core build')
        authoring_sources, authoring_includes = authoring_layout(root)
        sources += authoring_sources
        includes += authoring_includes
    if not all(path.is_file() for path in sources):
        raise lab.LabError('Pinned Blast source layout is incomplete')
    if not all(path.is_dir() for path in includes):
        raise lab.LabError('Pinned Blast include layout is incomplete')
    return sources, includes


def blast_header_inputs(root: Path, *, authoring: bool = False) -> list[Path]:
    """Track headers as rebuild dependencies, not only top-level SDK sources."""
    _, includes = blast_layout(root, authoring=authoring)
    return sorted({path for folder in includes if folder.is_relative_to(root)
                   for path in folder.rglob('*') if path.suffix in ('.h', '.inl')})


def blast_addon(root: Path, wrapper: Path, compat: Path, *, stress: bool, authoring: bool = False) -> str:
    sources, includes = blast_layout(root, stress=stress, authoring=authoring)
    compile_sources = list(sources)
    wrappers = [wrapper]
    dependencies = [wrapper, compat, *sources]
    if stress:
        generated = ROOT / 'work/blast-stress-generated'
        adapted = {
            root / 'source/sdk/extensions/stress/NvBlastExtStressSolver.cpp': generated / 'NvBlastExtStressSolver.cpp',
            root / 'source/shared/stress_solver/stress.cpp': generated / 'stress.cpp',
        }
        compile_sources = [adapted.get(path, path) for path in compile_sources]
        bridge_inputs = evidence_tools.BLAST_BRIDGE if authoring else evidence_tools.BLAST_STRESS_LEGACY_BRIDGE
        dependencies += [*adapted.values(), *blast_header_inputs(root, authoring=authoring),
                         *(ROOT / name for name in bridge_inputs[2:])]
    if authoring:
        wrappers.append(ROOT / 'addons/blast/pr_blast_authoring.cpp')
        adapted = ROOT / 'work/blast-authoring-generated/NvBlastExtApexSharedParts.cpp'
        original = root / 'source/sdk/extensions/authoring/NvBlastExtApexSharedParts.cpp'
        compile_sources = [adapted if path == original else path for path in compile_sources]
        dependencies.append(adapted)
    paths = [*dependencies, *compile_sources, *includes]
    quoted = {path: cmake_literal(path) for path in paths}
    source_args = ' '.join(quoted[path] for path in compile_sources)
    wrapper_args = ' '.join(quoted[path] for path in wrappers)
    include_args = ' '.join('-I ' + quoted[path] for path in includes)
    dependency_args = ' '.join(quoted[path] for path in dict.fromkeys(dependencies))
    # AVX declarations let pinned upstream templates parse. The queried backend
    # is explicitly scalar; this is not an x86/AVX execution path in WASM.
    intrinsic_flags = ' -mavx' if stress else ''
    return (f'\n{BLAST_MARKER}\nADD_CUSTOM_COMMAND(\n  OUTPUT pr_blast.o\n'
            f'  COMMAND em++ {wrapper_args} {source_args} {include_args} '
            f'-include {quoted[compat]} -std=c++17 -O3 -msimd128{intrinsic_flags} -DNDEBUG '
            f'-fno-rtti -fno-exceptions -r -o pr_blast.o\n'
            f'  DEPENDS {dependency_args}\n  VERBATIM\n)\n')


def patch_blast(cmake_text: str, root: Path, wrapper: Path, compat: Path) -> str:
    link_anchor = 'COMMAND em++ glue.o pr_bulk_rust.o -lc -lcompiler_rt '
    link = 'COMMAND em++ glue.o pr_bulk_rust.o pr_blast.o -lc -lcompiler_rt '
    depend_anchor = 'DEPENDS physx-js-bindings ${PHYSX_TARGETS} pr_bulk_rust.o '
    depend = 'DEPENDS physx-js-bindings ${PHYSX_TARGETS} pr_bulk_rust.o pr_blast.o '
    addon = blast_addon(root, wrapper, compat, stress=True, authoring=True)
    if BLAST_MARKER in cmake_text:
        _, includes = blast_layout(root, authoring=True)
        for path in includes:
            quoted = cmake_literal(path)
            cmake_text = cmake_text.replace('-I' + quoted, '-I ' + quoted)
        if cmake_text.count(link) != 1 or cmake_text.count(depend) != 1:
            raise lab.LabError('Expected one matching unified Blast link command')
        blocks = re.findall(r'\n' + re.escape(BLAST_MARKER) + r'\nADD_CUSTOM_COMMAND\(\n.*?\n\)\n', cmake_text, re.S)
        legacy = blast_addon(root, wrapper, compat, stress=False)
        stress_only = blast_addon(root, wrapper, compat, stress=True)
        if cmake_text.count(BLAST_MARKER) != 1 or len(blocks) != 1 or blocks[0] not in (legacy, stress_only, addon):
            raise lab.LabError('Blast addon patch does not match the inspected source/flag/dependency layout')
        return cmake_text.replace(blocks[0], addon, 1)
    if cmake_text.count(link_anchor) != 1 or cmake_text.count(depend_anchor) != 1:
        raise lab.LabError('Unified PhysX/Rust link layout changed; refusing to guess the Blast patch')
    return cmake_text.replace(link_anchor, link).replace(depend_anchor, depend) + addon


def require_single_thread_preset(text: str) -> None:
    values = re.findall(r"(?im)^\s*SET\s*\(\s*PHYSX_WASM_MULTI_THREADING\s+([^\s)]+)\s*\)", text)
    if len(values) != 1 or values[0].upper() != 'FALSE':
        raise lab.LabError('Expected the pinned single-thread PhysX preset; this Rust adapter is not pthread-safe')


def unified_linkage(cmake_source: str, repo: Path, profile: str = 'candidate') -> str:
    """Deterministically render the existing inspected Rust/Blast/Flow linkage."""
    destination = repo / 'physx/source/webidlbindings/pr_rust_bulk'
    archive = ROOT / 'work' / f'rust-{profile}' / 'libpr_pose_core.a'
    flow_object = ROOT / 'work/pr_flow_host.o'
    if profile == 'candidate':
        cmake_source = cmake_source.replace(' ' + cmake_literal(flow_object), '')
        cmake_source = cmake_source.replace('physx-pe.mjs', 'physx-js-webidl.mjs').replace('physx-pe.wasm', 'physx-js-webidl.wasm')
    patched = patch_rust(cmake_source, destination / 'pr_bulk_rust.cpp', destination / 'pr_rust_core.h', archive)
    if profile == 'candidate':
        patched = patch_blast(patched, repo / 'blast', ROOT / 'addons/blast/pr_blast_wasm.cpp',
                              ROOT / 'addons/blast/emscripten_nv_compat.h')
        patched = patched.replace('pr_blast.o -lc', 'pr_blast.o ' + cmake_literal(flow_object) + ' -lc')
        patched = patched.replace('DEPENDS physx-js-bindings ${PHYSX_TARGETS} pr_bulk_rust.o pr_blast.o ',
                                  'DEPENDS physx-js-bindings ${PHYSX_TARGETS} pr_bulk_rust.o pr_blast.o ' + cmake_literal(flow_object) + ' ')
        patched = patched.replace('physx-js-webidl.mjs', 'physx-pe.mjs').replace('physx-js-webidl.wasm', 'physx-pe.wasm')
    return patched


def build_wasm(profile: str) -> None:
    # Refuse all writes/patches until essential tools and target are available.
    info = doctor()
    if info['wasm_missing']:
        raise lab.LabError('Missing tools/target: ' + ', '.join(info['wasm_missing']))
    if not re.search(r'\b' + re.escape(lab.LOCK['emscripten']) + r'\b', info.get('emcc', '')):
        raise lab.LabError(f'Activate pinned Emscripten {lab.LOCK["emscripten"]}')
    repo = ROOT / 'work' / profile / 'PhysX'
    if not (repo / '.git').exists():
        raise lab.LabError('Missing source checkout. Run python tools/prepare_sources.py first.')
    if lab.git(repo, 'diff', '--name-only', '--diff-filter=U'):
        raise lab.LabError('Resolve source merge conflicts first; this command does not hide them')
    actual = lab.sdk_version((repo / 'physx/include/foundation/PxPhysicsVersion.h').read_text())
    expected = lab.LOCK['baseline_sdk'] if profile == 'baseline' else lab.LOCK['candidate_sdk']
    if actual != expected:
        raise lab.LabError(f'Source SDK version {actual} does not match {expected}')
    platform = repo / 'physx/source/compiler/cmake/emscripten/CMakeLists.txt'
    require_single_thread_preset(platform.read_text())
    for key in ('EMCC_CFLAGS', 'CFLAGS', 'CXXFLAGS', 'RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS'):
        if os.environ.get(key, '').strip():
            raise lab.LabError(f'Unset {key} for this controlled build; custom ABI flags are not validated')
    cmake = repo / 'physx/source/compiler/cmake/emscripten/PhysXWasmBindings.cmake'
    dest = repo / 'physx/source/webidlbindings/pr_rust_bulk'
    archive = ROOT / 'work' / f'rust-{profile}' / 'libpr_pose_core.a'
    # Validate the exact patch BEFORE compiling or copying anything.
    cmake_source = cmake.read_text()
    if profile == 'candidate':
        command([sys.executable, ROOT / 'addons/flow/build_host.py', '--object'], timeout=1800)
    patched = unified_linkage(cmake_source, repo, profile)
    blast_sources: list[Path] = []
    if profile == 'candidate':
        blast_root = repo / 'blast'
        prepare_stress_sources(blast_root, ROOT / 'work/blast-stress-generated')
        prepare_authoring_sources(blast_root, ROOT / 'work/blast-authoring-generated')
        blast_sources, _ = blast_layout(blast_root, authoring=True)
    archive.parent.mkdir(parents=True, exist_ok=True)
    command(rust_args('wasm', archive))
    if not archive.is_file() or archive.stat().st_size < 8:
        raise lab.LabError('Rust static archive missing after compiler success')
    dest.mkdir(parents=True, exist_ok=True)
    for name in ('pr_bulk_rust.cpp', 'pr_rust_core.h'):
        shutil.copy2(ROOT / 'bridge' / name, dest / name)
    cmake.write_text(patched, encoding='utf-8', newline='\n')
    bridge_source_names = list(evidence_tools.BRIDGE)
    if profile == 'candidate':
        bridge_source_names += evidence_tools.BLAST_BRIDGE + evidence_tools.FLOW_BRIDGE
    inputs = {'sdk': actual, 'profile': profile, 'rustc': info.get('rustc'),
        'emscripten': info.get('emcc'), 'rust_archive_sha256': lab.sha256(archive),
        'sources': {n: lab.sha256(ROOT / n) for n in bridge_source_names},
        'blast_sources': {str(path.relative_to(repo / 'blast')): lab.sha256(path)
                          for path in blast_sources},
        'source_head': lab.git(repo, 'rev-parse', 'HEAD'),
        'source_status': lab.git(repo, 'status', '--porcelain'),
        'compiled': False, 'physics_tested': False}
    if profile == 'candidate':
        inputs['blast_headers'] = {str(path.relative_to(blast_root)): lab.sha256(path)
                                  for path in blast_header_inputs(blast_root, authoring=True)}
        inputs['blast_stress_adaptation'] = json.loads((ROOT / 'work/blast-stress-generated/adaptation.json').read_text())
        inputs['blast_authoring_adaptation'] = json.loads((ROOT / 'work/blast-authoring-generated/adaptation.json').read_text())
    record(f'{profile}-rust-inputs.json', inputs)
    out = repo / 'physx/compiler/emscripten-release'
    cache = out / 'CMakeCache.txt'
    if cache.is_file():
        cached_source = re.search(r'^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$', cache.read_text(), re.M)
        if not cached_source or Path(cached_source[1]).resolve() != (repo / 'physx/compiler/public').resolve():
            raise lab.LabError('Existing CMake cache belongs to another source tree')
        print('Reusing existing CMake build; changed commands will be regenerated.', flush=True)
        if not (out / 'Makefile').is_file() and not (out / 'build.ninja').is_file():
            command(['cmake', '-S', repo / 'physx/compiler/public', '-B', out], timeout=1800)
    else:
        lab.run(['bash', './generate_projects.sh', 'emscripten'], cwd=repo / 'physx', timeout=1800)
    if not (out / 'CMakeCache.txt').is_file():
        raise lab.LabError('Expected release build directory was not generated')
    command(['cmake', '--build', out, '--parallel', str(min(os.cpu_count() or 2, 8))], timeout=7200)
    lab.stage(profile, out / 'sdk_source_bin', compiled=True, bulk=True)
    manifest = ROOT / 'dist' / profile / 'build-manifest.json'
    # The workbench uses build-manifest.json; verify exact path instead of inventing it.
    if not manifest.is_file():
        raise lab.LabError('Staged build is missing build-manifest.json; not promoting it')
    data = json.loads(manifest.read_text())
    data.update({'bridge_backend': 'rust', 'rust_abi': 2, 'js_bulk_abi': 1,
                 'blast_core_version': '5.0.6' if profile == 'candidate' else None,
                 'flow_webgpu_stage_abi': 1,
                 'blast_authoring_abi': 1 if profile == 'candidate' else None,
                 'flow_collision_abi': 1 if profile == 'candidate' else None,
                 'rust_archive_sha256': lab.sha256(archive), 'runtime_verified': False,
                 'engine_integration_verified': False, 'bridge_sources': inputs['sources']})
    if profile == 'candidate':
        data.update({key: inputs[key] for key in ('blast_sources', 'blast_headers', 'blast_stress_adaptation', 'blast_authoring_adaptation')})
    lab.write_json(manifest, data)
    record(f'{profile}-rust-build.json', data)
    patch = lab.run(['git', '-C', repo, 'diff', '--binary', 'HEAD']).stdout
    (REPORTS / f'{profile}-rust-working-tree.patch').write_text(patch)
    print('Compiled + packaged. Run real browser tests next; NOT release certified.')


def abi_wasm_args(archive: Path, output: Path) -> list[str | Path]:
    return ['em++', ROOT / 'tests/abi_smoke.cpp', '-lc', '-lcompiler_rt', archive, '-I', ROOT / 'bridge',
        '-std=c++17', '-O2', '-msimd128', '-fno-exceptions', '-fno-rtti', '--no-entry',
        '-sMODULARIZE=1', '-sEXPORT_ES6=1', '-sEXPORT_NAME=PrAbiModule',
        '-sENVIRONMENT=web,worker', '-sFILESYSTEM=0', '-sASSERTIONS=2',
        '-sALLOW_MEMORY_GROWTH=1', '-sINITIAL_MEMORY=16777216',
        '-sMAXIMUM_MEMORY=2147483648', '-sSTACK_SIZE=1048576',
        '-sDISABLE_EXCEPTION_CATCHING=1', '-o', output]


def build_abi(target: str) -> None:
    """Compile a real Rust/C++ ABI executable, independently of SDK downloads."""
    record('abi-build.json', {'status': 'NOT_RUN', 'target': target, 'physics_tested': False})
    if target == 'native':
        if os.name == 'nt':
            raise lab.LabError('The C++ native ABI executable currently needs Linux/macOS/WSL; '
                               'use build.py native for the Windows Rust/ctypes test suite.')
        compiler=shutil.which('clang++') or shutil.which('g++') or shutil.which('c++')
        if not compiler: raise lab.LabError('C++ compiler missing')
        library=compile_native()
        output=library.parent / 'abi-probe'
        command([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                 '-I', ROOT / 'bridge', ROOT / 'tests/abi_smoke.cpp', library,
                 '-Xlinker', '-rpath', '-Xlinker', library.parent.resolve(), '-o', output])
        result=command([output], check=False)
        record('abi-build.json', {'status': 'ABI_PASSED_NOT_PHYSICS' if result.returncode==0 else 'FAILED',
            'target': target, 'exit_code': result.returncode, 'log': result.stdout,
            'physics_tested': False, 'rust_source_sha256': lab.sha256(RUST)})
        if result.returncode: raise lab.LabError('Real Rust/C++ native ABI probe failed')
        return
    if target != 'wasm': raise lab.LabError('Unknown ABI target')
    info=doctor()
    if info['wasm_missing']:
        raise lab.LabError('Missing tools/target: ' + ', '.join(info['wasm_missing']))
    if not re.search(r'\b' + re.escape(lab.LOCK['emscripten']) + r'\b', info.get('emcc','')):
        raise lab.LabError('Activate pinned Emscripten ' + lab.LOCK['emscripten'])
    for key in ('EMCC_CFLAGS','CFLAGS','CXXFLAGS','RUSTFLAGS','CARGO_ENCODED_RUSTFLAGS'):
        if os.environ.get(key,'').strip(): raise lab.LabError('Unset custom ABI flags: ' + key)
    output=ROOT/'work/abi-wasm';output.mkdir(parents=True,exist_ok=True)
    archive=output/'libpr_pose_core.a'
    command(rust_args('wasm',archive))
    command(abi_wasm_args(archive,output/'abi-probe.mjs'))
    lab.verify_wasm(output/'abi-probe.wasm')
    manifest={'scope':'Rust/C++ ABI probe, NO PhysX', 'status':'COMPILED_NOT_RUNTIME_TESTED',
        'rustc':info.get('rustc'), 'emscripten':info.get('emcc'), 'physics_tested':False,
        'sources':{name:lab.sha256(ROOT/name) for name in
            ('rust/src/lib.rs','bridge/pr_rust_core.h','tests/abi_smoke.cpp')},
        'artifacts':{name:{'sha256':lab.sha256(output/name),'bytes':(output/name).stat().st_size}
            for name in ('abi-probe.mjs','abi-probe.wasm')}}
    # Reuse a transactional staging directory rather than publishing half a pair.
    import tempfile
    parent=ROOT/'dist';parent.mkdir(exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.abi-',dir=parent));backup=parent/'.abi-previous'
    try:
        if backup.exists(): raise lab.LabError('Unresolved prior ABI staging backup; inspect it first')
        for name in manifest['artifacts']:
            shutil.copy2(output/name,staging/name)
            if lab.sha256(staging/name)!=manifest['artifacts'][name]['sha256']:
                raise lab.LabError('ABI source artifacts changed during staging')
        lab.write_json(staging/'build-manifest.json',manifest)
        dest=parent/'abi'
        if dest.exists(): dest.rename(backup)
        try: staging.rename(dest)
        except OSError:
            if backup.exists(): backup.rename(dest)
            raise
        if backup.exists(): shutil.rmtree(backup)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    record('abi-build.json',manifest)
    print('ABI probe compiled. Run tools/abi_browser_test.py; NO PhysX solver was built.')


def header_checks() -> dict[str, Any]:
    out = ROOT / 'work/header-check'; out.mkdir(parents=True, exist_ok=True)
    # Compile actual shared header, not a fake PhysX header or a stubbed adapter.
    results = {}
    for language, compiler, extension, std in (
            ('C', 'clang', 'c', 'c11'), ('C++', 'clang++', 'cpp', 'c++17')):
        if not shutil.which(compiler):
            results[language] = {'status': 'NOT_RUN', 'reason': f'{compiler} missing'}
            continue
        source = out / ('layout.' + extension)
        assertion = '_Static_assert' if language == 'C' else 'static_assert'
        source.write_text('#include "pr_rust_core.h"\n' +
            f'{assertion}(sizeof(PrEntry) == 8 + sizeof(void*), "Entry layout");\n' +
            f'{assertion}(sizeof(PrCache) == 16 + 5*sizeof(void*), "Cache layout");\n' +
            f'{assertion}(offsetof(PrEntry, actor) == 8, "Actor offset");\n' +
            f'{assertion}(sizeof(float) == 4 && sizeof(uint32_t) == 4, "Scalar widths");\n' +
            ''.join(f'{assertion}(offsetof(PrCache, {name}) == {i}*sizeof(void*), "{name} offset");\n'
                for i, name in enumerate(('entries','ids','poses','scratch_ids','scratch_poses'))) +
            ''.join(f'{assertion}(offsetof(PrCache, {name}) == 5*sizeof(void*)+{i}*4, "{name} offset");\n'
                for i, name in enumerate(('capacity','count','published','initialized'))))
        p = command([compiler, '-std=' + std, '-Wall', '-Wextra', '-Werror', '-pedantic',
                     '-fsyntax-only', '-I', ROOT / 'bridge', source], check=False)
        results[language] = {'status': 'PASSED' if p.returncode == 0 else 'FAILED',
                             'exit_code': p.returncode, 'log': p.stdout}
        # Clang's freestanding wasm32 layout check is NOT an Emscripten link,
        # Rust compile or runtime test. It independently checks the shared header.
        wasm = out / ('layout-wasm32.' + extension)
        wasm.write_text(source.read_text() +
            f'{assertion}(sizeof(void*) == 4, "wasm32 pointer");\n' +
            f'{assertion}(sizeof(PrEntry) == 12, "wasm32 Entry");\n' +
            f'{assertion}(sizeof(PrCache) == 36, "wasm32 Cache");\n')
        w = command([compiler, '--target=wasm32-unknown-unknown', '-ffreestanding',
                     '-std=' + std, '-Wall', '-Wextra', '-Werror', '-pedantic',
                     '-fsyntax-only', '-I', ROOT / 'bridge', wasm], check=False)
        results[language + ' wasm32 layout-only'] = {
            'status': 'PASSED' if w.returncode == 0 else 'FAILED',
            'exit_code': w.returncode, 'log': w.stdout,
            'scope': 'Clang freestanding header only; NOT Emscripten, Rust or PhysX'}
    return results


def test_all() -> bool:
    results: dict[str, Any] = {'utc': datetime.now(timezone.utc).isoformat(),
        'physx_compiled': False, 'physics_simulation_tested': False,
        'browser_integration_tested': False, 'engine_release_approved': False}
    env = dict(os.environ)
    env.pop('PR_RUST_LIB', None)  # Standalone Python tests cannot silently pick a stale library.
    py = command([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], env=env, check=False)
    results['python'] = {'status': 'PASSED' if py.returncode == 0 else 'FAILED',
                         'exit_code': py.returncode, 'log': py.stdout}
    results['c_abi_header'] = header_checks()
    if not shutil.which('rustc'):
        results['rust_unit_tests'] = {'status': 'NOT_RUN', 'reason': 'rustc not installed'}
        results['native_rust_ffi'] = {'status': 'NOT_RUN', 'reason': 'Rust library was not compiled'}
        success = False
    else:
        out = ROOT / 'work/rust-native'; out.mkdir(parents=True, exist_ok=True)
        testbin = out / ('rust-tests.exe' if os.name == 'nt' else 'rust-tests')
        compile_test = command(rust_args('test', testbin), check=False)
        test = command([testbin], check=False) if compile_test.returncode == 0 else compile_test
        results['rust_unit_tests'] = {'status': 'PASSED' if test.returncode == 0 else 'FAILED',
                                      'log': compile_test.stdout + test.stdout}
        try:
            lib = compile_native()
            ffi = run_ffi_tests(lib)
            results['native_rust_ffi'] = {'status': 'PASSED' if ffi.returncode == 0 else 'FAILED',
                                          'log': ffi.stdout}
        except (lab.LabError, OSError) as e:
            results['native_rust_ffi'] = {'status': 'FAILED', 'error': str(e)}
        success = all(results[k]['status'] == 'PASSED' for k in ('rust_unit_tests', 'native_rust_ffi'))
    success = success and py.returncode == 0 and not any(
        v['status'] != 'PASSED' for v in results['c_abi_header'].values())
    results['status'] = 'NON_PHYSICS_TESTS_PASSED' if success else 'INCOMPLETE_OR_FAILED'
    record('rust-python-tests.json', results)
    print(json.dumps({k:v for k,v in results.items() if k not in ('python', 'c_abi_header')}, indent=2))
    return success


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('doctor', 'test', 'native'):
        commands.add_parser(name)
    wasm = commands.add_parser('wasm')
    wasm.add_argument('--profile', choices=['baseline', 'candidate'], required=True)
    abi = commands.add_parser('abi', help='Compile the real Rust/C++ ABI probe, not PhysX')
    abi.add_argument('--target', choices=['native','wasm'], default='native')
    gate = commands.add_parser('gate', help='Reject stale/mismatched physics evidence')
    gate.add_argument('--profile', choices=['baseline','candidate'], required=True)
    args = parser.parse_args()
    try:
        if args.command == 'gate':
            return command([sys.executable, ROOT/'tools/evidence.py', '--profile', args.profile], check=False).returncode
        if args.command == 'doctor':
            return 2 if doctor()['status'] == 'BLOCKED' else 0
        if args.command == 'test':
            return 0 if test_all() else 2
        if args.command == 'native':
            lib = compile_native()
            result = run_ffi_tests(lib)
            record('rust-native-ffi.json', {'exit_code': result.returncode, 'log': result.stdout,
                'status': 'PASSED' if result.returncode == 0 else 'FAILED', 'physics_tested': False})
            return 0 if result.returncode == 0 else 2
        if args.command == 'abi': build_abi(args.target)
        elif args.command == 'wasm': build_wasm(args.profile)
        return 0
    except (lab.LabError, OSError, ValueError) as e:
        if args.command == 'abi':
            record('abi-build.json', {'status':'BLOCKED_OR_FAILED','target':args.target,
                'error':str(e),'physics_tested':False})
        record('rust-python-last-failure.json', {'command': args.command, 'status': 'BLOCKED_OR_FAILED',
            'error': str(e), 'compiled_and_verified': False})
        print('ERROR:', e, file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
