# SPDX-License-Identifier: MIT
"""Build NVIDIA Flow host operators with the browser context, for integration tests."""
from pathlib import Path
import subprocess
import hashlib
import sys
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from generate_host_headers import generate, ROOT
from generate_rebase_sources import generate as generate_rebase

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--object', action='store_true')
parser.add_argument('--output', type=Path, default=ROOT / 'dist/flow-host')
parser.add_argument('--objects', type=Path, default=ROOT / 'work/flow-host-objects')
args = parser.parse_args()
flow = ROOT / 'work/candidate/PhysX/flow'
headers = generate()
sources = sorted((flow / 'source/nvflow').glob('*.cpp')) + sorted((flow / 'source/nvflowext').glob('*.cpp'))
sources = [p for p in sources if p.name not in ('GridOpt.cpp', 'ThreadPool.cpp')]
rebase_sources = generate_rebase(flow, ROOT / 'work/flow-rebase-sources')
sources = [rebase_sources.get(p, p) for p in sources]
includes = [flow / 'include/nvflow', flow / 'include/nvflowext', flow / 'shared', flow / 'external', flow / 'include/nvflow/shaders', flow / 'include/nvflowext/shaders', headers, Path(__file__).parent]
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
sources.insert(0, Path(__file__).with_name('pr_flow_host.cpp'))
flags = [*['-I' + str(p) for p in includes], '-std=c++17', '-O2', '-msimd128', '-fno-rtti', '-fno-exceptions', '-Wno-null-conversion']
objects = args.objects.resolve()
objects.mkdir(parents=True, exist_ok=True)
header_inputs = sorted(headers.rglob('*.h')) + sorted(Path(__file__).parent.glob('*.h'))
header_hash = hashlib.sha256(b''.join(p.read_bytes() for p in header_inputs)).hexdigest()

def compile_one(source):
    target = objects / (source.stem + '.o')
    receipt = target.with_suffix('.sha256')
    digest = hashlib.sha256(source.read_bytes() + (header_hash + repr(flags)).encode()).hexdigest()
    if not target.exists() or not receipt.exists() or receipt.read_text() != digest:
        print('Compiling ' + source.name, flush=True)
        subprocess.run(['em++', str(source), *flags, '-c', '-o', str(target)], check=True)
        receipt.write_text(digest)
    return str(target)

with ThreadPoolExecutor(max_workers=4) as pool:
    compiled = list(pool.map(compile_one, sources))
command = ['em++', *compiled, '-O2', '-msimd128',
           '-sALLOW_MEMORY_GROWTH=1', '-sMODULARIZE=1', '-sEXPORT_ES6=1', '-sENVIRONMENT=web',
           '-sEXPORTED_FUNCTIONS=_malloc,_free', '-sEXPORTED_RUNTIME_METHODS=HEAPU8,HEAPU32,HEAPF32', '-o', str(output / 'flow-host.mjs')]
subprocess.run(command, check=True)
if args.object:
    subprocess.run(['em++', *compiled, '-r', '-o', str(ROOT / 'work/pr_flow_host.o')], check=True)
inputs = set(sources) | set(rebase_sources) | set(header_inputs)
inputs.update(Path(__file__).parent.glob('*.h'))
inputs.update(Path(__file__).parent / name for name in ('build_host.py', 'generate_host_headers.py', 'generate_rebase_sources.py', 'flow_host_webgpu.mjs', 'flow_solid_boundary.mjs', 'flow_scalar_sources.mjs'))
for directory in ('include', 'shared', 'external', 'source'):
    inputs.update((flow / directory).rglob('*.h'))
manifest = {
    'schema': 'flow-host-build-v1', 'flowHostAbi': 1, 'flowSolidBoundaryAbi': 1,
    'flowScalarSourceAbi': 1, 'flowRebaseAbi': 1, 'flowMomentumExchangeAbi': 1, 'flags': flags,
    'compiler': subprocess.check_output(['em++', '--version'], text=True),
    'sourceHashes': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(inputs) if p.is_file()},
    'artifacts': {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size': p.stat().st_size} for p in (output / 'flow-host.mjs', output / 'flow-host.wasm')},
}
(output / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
