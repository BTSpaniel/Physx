# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) <https://github.com/BTSpaniel>
# SPDX-License-Identifier: MIT
"""Build isolated numerical ABI2; never install or change the baseline pair."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import subprocess
from build_thermal import FLAGS, ROOT, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostics', action='store_true', help='Observe macro-error provenance without changing admission')
    args = parser.parse_args()
    source = Path(__file__).with_name('pr_wood_thermal_multirate.cpp')
    output = ROOT / ('dist/wood-thermal-multirate-diagnostics' if args.diagnostics else 'dist/wood-thermal-multirate')
    output.mkdir(parents=True, exist_ok=True)
    obj = output / 'pr_wood_thermal_multirate.o'
    diagnostic_flags = ['-DPR_THERMAL_STABLE_DEPLETION=1', *(['-DPR_THERMAL_MR_DIAGNOSTICS=1'] if args.diagnostics else [])]
    compile_command = ['em++', str(source), *FLAGS, *diagnostic_flags, '-c', '-o', str(obj)]
    subprocess.run(compile_command, check=True)
    link_command = ['em++', str(obj), '-O3', '-msimd128', '-sALLOW_MEMORY_GROWTH=1',
                    '-sINITIAL_MEMORY=4194304', '-sSTACK_SIZE=131072', '-sMAXIMUM_MEMORY=2147483648',
                    '-sMODULARIZE=1', '-sEXPORT_ES6=1', '-sENVIRONMENT=web', '-sFILESYSTEM=0',
                    '-sEXPORTED_FUNCTIONS=_malloc,_free,_pr_wood_thermal_mr_abi,_pr_wood_thermal_mr_numerics,_pr_wood_thermal_mr_workspace_bytes,_pr_wood_thermal_mr_step',
                    '-sEXPORTED_RUNTIME_METHODS=HEAPU8,HEAPU32,HEAPF64', '-o', str(output / 'wood-thermal.mjs')]
    subprocess.run(link_command, check=True)
    sources = [source, source.with_name('pr_wood_thermal.cpp'), source.with_name('build_thermal.py'), Path(__file__)]
    manifest = {'status': 'COMPILED_NOT_RUNTIME_VERIFIED', 'abi': 2, 'numericalVersion': 9,
                'diagnosticVersion': 1 if args.diagnostics else None,
                'utc': datetime.now(timezone.utc).isoformat(),
                'scope': 'Isolated MRI-GARK-ERK22a paired conductive forcing with an explicit physical-domain order filter: admissible second-order Richardson or actual positive first-order four-substep maps with both step-doubling errors. Same material, frozen-temperature law, controls and trial budgets. Order branches are counted; no clipping. Not installed or accepted.',
                'compiler': subprocess.run(['em++', '--version'], check=True, text=True, capture_output=True).stdout,
                'compileCommand': compile_command, 'linkCommand': link_command,
                'sources': {str(p.relative_to(ROOT)): sha(p) for p in sources},
                'artifacts': {name: {'bytes': (output / name).stat().st_size, 'sha256': sha(output / name)}
                              for name in ('pr_wood_thermal_multirate.o', 'wood-thermal.mjs', 'wood-thermal.wasm')}}
    (output / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
