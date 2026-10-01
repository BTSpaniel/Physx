# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) <https://github.com/BTSpaniel>
# SPDX-License-Identifier: LicenseRef-ParticleRealms-Alpha
"""Compile the standalone, strict-f64 wood loop prototype; no runtime install."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).with_name('pr_wood_thermal.cpp')
OUTPUT = ROOT / 'dist/wood-thermal'
FLAGS = ['-std=c++17', '-O3', '-fno-fast-math', '-fno-associative-math', '-ffp-contract=off',
         '-fno-rtti', '-fno-exceptions', '-msimd128', '-Wall', '-Wextra', '-Werror']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', action='store_true', help='Emit a separate named-function diagnostic build, with hot helpers kept out of line')
    parser.add_argument('--integer-powers', action='store_true', help='Compare explicit T cubed/fourth multiplications, with declared f64 rounding differences')
    parser.add_argument('--js-math', action='store_true', help='Compare browser Math imports within this standalone addon only')
    args = parser.parse_args()
    suffix = ''.join('-' + name for enabled, name in [(args.integer_powers, 'integer-powers'), (args.js_math, 'js-math'), (args.profile, 'profile')] if enabled)
    output = ROOT / ('dist/wood-thermal' + suffix)
    flags = [*FLAGS, *(['-DPR_THERMAL_PROFILE=1'] if args.profile else []), *(['-DPR_THERMAL_INTEGER_POWERS=1'] if args.integer_powers else [])]
    output.mkdir(parents=True, exist_ok=True)
    obj = output / 'pr_wood_thermal.o'
    compile_command = ['em++', str(SOURCE), *flags, '-c', '-o', str(obj)]
    subprocess.run(compile_command, check=True)
    link_command = ['em++', str(obj), '-O3', '-msimd128', '-sALLOW_MEMORY_GROWTH=1',
                    '-sINITIAL_MEMORY=4194304', '-sSTACK_SIZE=65536', '-sMAXIMUM_MEMORY=2147483648',
                    '-sMODULARIZE=1', '-sEXPORT_ES6=1', '-sENVIRONMENT=web', '-sFILESYSTEM=0',
                    '-sEXPORTED_FUNCTIONS=_malloc,_free,_pr_wood_thermal_abi,_pr_wood_thermal_scratch_bytes,_pr_wood_thermal_step',
                    '-sEXPORTED_RUNTIME_METHODS=HEAPU8,HEAPU32,HEAPF64',
                    *(['-sJS_MATH=1'] if args.js_math else []),
                    *(['--profiling-funcs'] if args.profile else []), '-o', str(output / 'wood-thermal.mjs')]
    subprocess.run(link_command, check=True)
    manifest = {'status': 'COMPILED_NOT_RUNTIME_VERIFIED', 'abi': 1, 'diagnosticProfiling': args.profile,
                'integerPowerMultiplication': args.integer_powers, 'standaloneBrowserMathImports': args.js_math,
                'utc': datetime.now(timezone.utc).isoformat(),
                'scope': 'First-party ordered f64 wood thermal loop prototype. No native physics runtime changed or installed.',
                'compiler': subprocess.run(['em++', '--version'], check=True, text=True, capture_output=True).stdout,
                'compileCommand': compile_command, 'linkCommand': link_command,
                'sources': {str(p.relative_to(ROOT)): sha(p) for p in (SOURCE, Path(__file__))},
                'artifacts': {name: {'bytes': (output / name).stat().st_size, 'sha256': sha(output / name)}
                              for name in ('pr_wood_thermal.o', 'wood-thermal.mjs', 'wood-thermal.wasm')}}
    (output / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
