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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import physx_lab as lab
from flow_source_evidence import upstream_inputs, shader_inventory, CAPABILITIES

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--object', action='store_true')
parser.add_argument('--output', type=Path, default=ROOT / 'dist/flow-host')
parser.add_argument('--objects', type=Path, default=ROOT / 'work/flow-host-objects')
args = parser.parse_args()
flow = ROOT / 'source-inputs/flow'
upstream_inputs(ROOT)
shader_files = shader_inventory(ROOT)
compiler = subprocess.check_output(['em++', '--version'], text=True)
if lab.LOCK['emscripten'] not in compiler.splitlines()[0].split():
    raise ValueError('Activate pinned Emscripten ' + lab.LOCK['emscripten'])
headers = generate()
if len(list(headers.rglob('*.h'))) != 124:
    raise ValueError('Native Flow requires exactly 124 generated pipeline headers')
sources = sorted((flow / 'source/nvflow').glob('*.cpp')) + sorted((flow / 'source/nvflowext').glob('*.cpp'))
sources = [p for p in sources if p.name not in ('GridOpt.cpp', 'ThreadPool.cpp')]
rebase_sources = generate_rebase(flow, ROOT / 'work/flow-rebase-sources')
sources = [rebase_sources.get(p, p) for p in sources]
includes = [flow / 'include/nvflow', flow / 'include/nvflowext', flow / 'shared', flow / 'external', flow / 'include/nvflow/shaders', flow / 'include/nvflowext/shaders', headers, Path(__file__).parent]
output = args.output.resolve()
if not output.is_relative_to((ROOT / 'dist').resolve()) or output == (ROOT / 'dist').resolve():
    raise ValueError('Host output must be contained beneath this source kit dist')
output.mkdir(parents=True, exist_ok=True)
sources.insert(0, Path(__file__).with_name('pr_flow_host.cpp'))
flags = [*['-I' + str(p) for p in includes], '-std=c++17', '-O2', '-msimd128', '-fno-rtti', '-fno-exceptions', '-Wno-null-conversion']
objects = args.objects.resolve()
if not objects.is_relative_to((ROOT / 'work').resolve()) or objects == (ROOT / 'work').resolve():
    raise ValueError('Object output must be contained beneath this source kit work')
objects.mkdir(parents=True, exist_ok=True)
header_inputs = sorted(headers.rglob('*.h')) + sorted(Path(__file__).parent.glob('*.h'))
inputs = set(sources) | set(rebase_sources) | set(header_inputs)
inputs.update(p for p in flow.rglob('*') if p.is_file())
inputs.update(p for p in Path(__file__).parent.iterdir() if p.is_file()
              and p.suffix in ('.py', '.mjs', '.h', '.hlsl', '.hlsli', '.mts'))
inputs.update(ROOT / name for name in ('tools/prepare_sources.py', 'tools/flow_source_evidence.py',
                                      'source-selection.json', 'upstream.lock.json'))
source_hashes = {p.relative_to(ROOT).as_posix(): lab.sha256(p) for p in sorted(inputs)}

def compile_one(source):
    target = objects / (source.stem + '.o')
    # Every translation unit is compiled from the admitted sources. Old object
    # caches cannot stand in for a new declaration/capability source selection.
    compile_command = ['em++', str(source), *flags, '-c', '-o', str(target)]
    print('Compiling ' + source.name, flush=True)
    subprocess.run(compile_command, check=True)
    return {'source': source.relative_to(ROOT).as_posix(), 'object': str(target), 'command': compile_command}

with ThreadPoolExecutor(max_workers=4) as pool:
    compilations = list(pool.map(compile_one, sources))
compiled = [row['object'] for row in compilations]
commands = [row['command'] for row in compilations]
command = ['em++', *compiled, '-O2', '-msimd128',
           '-sALLOW_MEMORY_GROWTH=1', '-sMODULARIZE=1', '-sEXPORT_ES6=1', '-sENVIRONMENT=web',
           '-sEXPORTED_FUNCTIONS=_malloc,_free', '-sEXPORTED_RUNTIME_METHODS=HEAPU8,HEAPU32,HEAPF32', '-o', str(output / 'flow-host.mjs')]
subprocess.run(command, check=True)
commands.append(command)
if args.object:
    command = ['em++', *compiled, '-r', '-o', str(ROOT / 'work/pr_flow_host.o')]
    subprocess.run(command, check=True)
    commands.append(command)
after = {p.relative_to(ROOT).as_posix(): lab.sha256(p) for p in sorted(inputs)}
if after != source_hashes or shader_inventory(ROOT) != shader_files:
    raise ValueError('Flow source or shader inputs changed during compilation')
manifest = {
    'schema': 'flow-host-build-v1', **CAPABILITIES, 'flags': flags,
    'compiler': compiler, 'sourceHashes': source_hashes, 'sourceHashesAfter': after,
    'shaderInventory': shader_files, 'commands': commands, 'compilations': compilations,
    'allTranslationUnitsRecompiled': True, 'generatedHostHeaderCount': 124, 'generatedIncludeWrapperCount': 6,
    'artifacts': {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size': p.stat().st_size} for p in (output / 'flow-host.mjs', output / 'flow-host.wasm')},
}
(output / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
