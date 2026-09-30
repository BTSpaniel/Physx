# SPDX-License-Identifier: MIT
"""Generate checked platform-only adaptations of pinned NVIDIA stress sources.

The original checkout and solver algorithms are unchanged. Original NVIDIA
copyright/license text remains in each generated translation unit.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

PINNED = {
    'source/sdk/extensions/stress/NvBlastExtStressSolver.cpp':
        '2da71df9db94973fa1c1b645062a7bc588fea8cca3251fdb16ea5634c7b40e69',
    'source/shared/stress_solver/stress.cpp':
        'ff5b35f91788d6395d2884ffff57acf6179b8692114c7ce14fb8bd33c96e498c',
}

def prepare(root: Path, output: Path) -> list[Path]:
    generated = []
    output.mkdir(parents=True, exist_ok=True)
    receipt = {}
    for relative, expected in PINNED.items():
        path = root / relative
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f'Pinned NVIDIA stress source differs: {relative}')
        source = data.decode('utf-8')
        original = '#include "simd/simd_device_query.h"'
        if source.count(original) != 1:
            raise ValueError(f'Expected exactly one platform query include: {relative}')
        # NsUnixFPU uses CSR intrinsics. Emscripten supplies their fixed-FP-mode
        # no-op implementations; it does not emulate an x86 floating environment.
        source = source.replace(original, '#include "emscripten_stress_device.h"')
        source = '#include "emscripten_stress_intrinsics.h"\n' + source
        target = output / path.name
        encoded = source.encode('utf-8')
        if not target.is_file() or target.read_bytes() != encoded:
            target.write_bytes(encoded)
        receipt[relative] = {'sourceSha256': expected,
            'generatedSha256': hashlib.sha256(encoded).hexdigest()}
        generated.append(target)
    (output / 'adaptation.json').write_text(json.dumps({
        'backend': 'NVIDIA ExtStress scalar CPU/WASM', 'sources': receipt,
        'transform': 'Replace x86 device query include; provide Emscripten CSR declarations. No solver algorithm changes.'
    }, indent=2) + '\n', encoding='utf-8')
    return generated

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.root, args.output)
