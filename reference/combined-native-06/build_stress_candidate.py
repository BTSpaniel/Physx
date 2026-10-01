# SPDX-License-Identifier: MIT
"""Build an isolated, pinned installed-baseline Blast numerical candidate.

No installed runtime or upstream checkout is modified. Immutable original inputs
are copied by hash into this workbench; generated extensions retain attribution.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
WORKBENCH = ROOT.parents[1]
ARCHIVE = Path('/mnt/c/Coding/game/artifacts/physx-authoring-collision-2026-09-22/workbench-source')
sys.path.insert(0, str(ROOT / 'addons/blast'))
from prepare_stress_sources import prepare
from prepare_authoring_sources import authoring_layout, prepare as prepare_authoring


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--legacy', action='store_true')
    parser.add_argument('--output', required=True)
    parser.add_argument('--physical-tolerance', type=float, choices=(1e-5, 1e-6), default=1e-6)
    args = parser.parse_args()
    output = ROOT / 'dist' / args.output
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / 'build-manifest.json'
    if manifest_path.exists():
        raise ValueError('Preserve prior manifest; choose a new output')
    baseline = json.loads((ROOT / 'baseline-build-manifest.json').read_text())
    origin = WORKBENCH / 'work/candidate/PhysX/blast'
    sdk = ROOT / 'upstream/blast'
    for field in ('blast_sources', 'blast_headers'):
        for relative, digest in baseline[field].items():
            source = origin / relative
            if sha(source) != digest:
                raise ValueError('Pinned upstream drift: ' + relative)
            target = sdk / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and sha(target) != digest:
                raise ValueError('Preserve differing snapshot: ' + relative)
            if not target.exists():
                shutil.copy2(source, target)
    baseline_wrapper = ROOT / 'baseline/pr_blast_wasm.cpp'
    baseline_wrapper.parent.mkdir(parents=True, exist_ok=True)
    original = ARCHIVE / 'addons/blast/pr_blast_wasm.cpp'
    if sha(original) != baseline['bridge_sources']['addons/blast/pr_blast_wasm.cpp']:
        raise ValueError('Installed baseline wrapper drift')
    if not baseline_wrapper.exists():
        shutil.copy2(original, baseline_wrapper)
    elif sha(baseline_wrapper) != baseline['bridge_sources']['addons/blast/pr_blast_wasm.cpp']:
        raise ValueError('Preserve differing baseline wrapper snapshot')
    wrapper = baseline_wrapper if args.legacy else ROOT / 'addons/blast/pr_blast_wasm.cpp'
    generated_dir = ROOT / 'work' / ('legacy-generated' if args.legacy else 'physical-generated') / args.output
    generated = prepare(sdk, generated_dir, physical_extension=not args.legacy,
                        physical_tolerance=args.physical_tolerance)
    authoring_generated = prepare_authoring(sdk, ROOT / 'work/authoring-generated')
    sources = sorted((sdk / 'source/sdk/common').glob('*.cpp'))
    sources += sorted((sdk / 'source/sdk/lowlevel').glob('*.cpp'))
    extra, extra_includes = authoring_layout(sdk)
    sources += [authoring_generated if p.name == authoring_generated.name else p for p in extra]
    sources += [wrapper, ROOT / 'addons/blast/pr_blast_authoring.cpp', *generated,
                sdk / 'source/sdk/globals/NvBlastGlobals.cpp']
    includes = [generated_dir, ROOT / 'addons/blast'] + [sdk / name for name in (
        'include/extensions/stress', 'include/globals', 'include/lowlevel',
        'include/shared/NvFoundation', 'source/sdk/common', 'source/sdk/lowlevel',
        'source/shared/NsFoundation/include', 'source/shared/stress_solver')] + extra_includes
    flags = ['-include', str(ROOT / 'addons/blast/emscripten_nv_compat.h'),
             '-std=c++17', '-O3', '-msimd128', '-mavx', '-DNDEBUG', '-fno-rtti', '-fno-exceptions']
    for include in includes:
        flags += ['-I', str(include)]
    headers = sorted({p for folder in includes for p in folder.rglob('*')
                      if p.is_file() and p.suffix in ('.h', '.inl')}
                     | {ROOT / 'addons/blast/tr1/type_traits'})
    tool_version = subprocess.check_output(['em++', '--version'], text=True)
    if '4.0.19' not in tool_version:
        raise ValueError('Expected pinned Emscripten4.0.19')
    inputs = sorted(set([*sources, *headers, Path(__file__), ROOT / 'baseline-build-manifest.json',
        ROOT / 'section-factor-admission.json', ROOT / 'section-numerics-v3.json',
        ROOT / 'addons/blast/prepare_stress_sources.py', ROOT / 'addons/blast/physical_stress_extension.py',
        ROOT / 'addons/blast/section_stress_extension.py',
        ROOT / 'addons/blast/section_v3_extension.py',
        ROOT / 'addons/blast/prepare_authoring_sources.py', generated_dir / 'adaptation.json']))
    hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in inputs}
    cache_seed = hashlib.sha256((tool_version + json.dumps(flags)).encode())
    for header in headers:
        cache_seed.update(header.read_bytes())
    cache = ROOT / 'work/objects'
    cache.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    def compile_one(source):
        digest = cache_seed.copy()
        digest.update(str(source).encode()); digest.update(source.read_bytes())
        obj = cache / (source.stem + '-' + digest.hexdigest() + '.o')
        if not obj.exists():
            print('Compiling ' + source.name, flush=True)
            temporary = obj.with_suffix('.building.o')
            subprocess.run(['em++', str(source), *flags, '-c', '-o', str(temporary)], check=True)
            temporary.replace(obj)
        return obj
    with ThreadPoolExecutor(max_workers=4) as pool:
        objects = list(pool.map(compile_one, sources))
    module = output / 'physx-stress.mjs'
    command = ['em++', *map(str, objects), '-O3', '-msimd128', '-sMODULARIZE=1', '-sEXPORT_ES6=1',
               '-sEXPORT_NAME=createBlastStress', '-sALLOW_MEMORY_GROWTH=1', '-sSTACK_SIZE=1048576',
               '-sEXPORTED_FUNCTIONS=_malloc,_free', '-sEXPORTED_RUNTIME_METHODS=HEAPF32,HEAPU32,HEAPF64',
               '-o', str(module)]
    subprocess.run(command, check=True)
    if any(sha(ROOT / name) != digest for name, digest in hashes.items()):
        raise ValueError('Build source changed during compilation')
    manifest = {'status': 'COMPILED_NOT_RUNTIME_TESTED', 'scope': 'Isolated installed-baseline native Blast extension',
        'physicalStressAbi': 0 if args.legacy else 1, 'legacyStressAbi': 1, 'installedRuntimeModified': False,
        'physicalSolverRelativeTolerance': None if args.legacy else args.physical_tolerance,
        'sectionsAbi': 0 if args.legacy else 1, 'physicalNumericalRevisions': [] if args.legacy else [1,2,3],
        'sectionsV3Abi': 0 if args.legacy else 1,
        'sectionsV3PreparationAbi': 0 if args.legacy else 1,
        'stressMassAbi': 0 if args.legacy else 1,
        'sectionNumericsV3': None if args.legacy else json.loads((ROOT / 'section-numerics-v3.json').read_text()),
        'thermalIncluded': False, 'sources': hashes, 'compiler': tool_version, 'compileFlags': flags,
        'sectionFactorAdmission': None if args.legacy else json.loads((ROOT / 'section-factor-admission.json').read_text()),
        'linkCommand': command, 'sourceRoot': str(ROOT), 'objects': {str(p.relative_to(ROOT)): sha(p) for p in objects},
        'baselineArtifacts': baseline['artifacts'], 'adaptation': json.loads((generated_dir / 'adaptation.json').read_text()),
        'artifacts': {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)} for p in (module, module.with_suffix('.wasm'))},
        'elapsedSeconds': time.monotonic()-started, 'builtUtc': datetime.now(timezone.utc).isoformat()}
    manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({k:manifest[k] for k in ('status','artifacts','elapsedSeconds')}, indent=2), flush=True)


if __name__ == '__main__':
    main()
