# SPDX-License-Identifier: MIT
"""Build NVIDIA Flow host operators with the browser context, for integration tests."""
from pathlib import Path
import subprocess
import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor
from generate_host_headers import generate, ROOT

flow = ROOT / 'work/candidate/PhysX/flow'
headers = generate()
sources = sorted((flow / 'source/nvflow').glob('*.cpp')) + sorted((flow / 'source/nvflowext').glob('*.cpp'))
sources = [p for p in sources if p.name not in ('GridOpt.cpp', 'ThreadPool.cpp')]
includes = [flow / 'include/nvflow', flow / 'include/nvflowext', flow / 'shared', flow / 'external', flow / 'include/nvflow/shaders', flow / 'include/nvflowext/shaders', headers]
output = ROOT / 'dist/flow-host'
output.mkdir(parents=True, exist_ok=True)
sources.insert(0, Path(__file__).with_name('pr_flow_host.cpp'))
flags = [*['-I' + str(p) for p in includes], '-std=c++17', '-O2', '-msimd128', '-fno-rtti', '-fno-exceptions', '-Wno-null-conversion']
objects = ROOT / 'work/flow-host-objects'
objects.mkdir(parents=True, exist_ok=True)
header_hash = hashlib.sha256(b''.join(p.read_bytes() for p in sorted(headers.rglob('*.h')))).hexdigest()

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
if '--object' in sys.argv:
    subprocess.run(['em++', *compiled, '-r', '-o', str(ROOT / 'work/pr_flow_host.o')], check=True)
