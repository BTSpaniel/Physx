# SPDX-License-Identifier: MIT
"""Generate checked adaptations of pinned NVIDIA stress sources.

The original checkout is unchanged. The opt-in physical operator preserves
authored mass/centroids and declares its stricter convergence precision.
Original NVIDIA copyright/license text remains in every generated source.
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

def prepare(root: Path, output: Path, *, physical_extension: bool = False,
            physical_tolerance: float = 1e-6) -> list[Path]:
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
        if physical_extension and path.name == 'NvBlastExtStressSolver.cpp':
            from physical_stress_extension import extend
            from section_stress_extension import extend as extend_sections
            from section_v3_extension import extend as extend_v3
            newline = '\r\n' if '\r\n' in source else '\n'
            source = extend_v3(extend_sections(extend(source.replace('\r\n', '\n'), physical_tolerance))).replace('\n', newline)
        target = output / path.name
        encoded = source.encode('utf-8')
        if not target.is_file() or target.read_bytes() != encoded:
            target.write_bytes(encoded)
        receipt[relative] = {'sourceSha256': expected,
            'generatedSha256': hashlib.sha256(encoded).hexdigest()}
        generated.append(target)
    if physical_extension:
        from section_stress_extension import extend_cgnr
        relative = 'source/shared/stress_solver/math/cgnr.h'
        data = (root / relative).read_bytes()
        expected = '779750a94eaa95e8c0c081ba0f2007a63d94786b4c2a3148eac71ac0eed5905c'
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Pinned CGNR header differs')
        encoded = extend_cgnr(data.decode('utf-8').replace('\r\n', '\n')).encode('utf-8')
        target = output / 'pr_section_cgnr.h'
        target.write_bytes(encoded)
        receipt[relative] = {'sourceSha256': expected, 'generatedSha256': hashlib.sha256(encoded).hexdigest()}
    (output / 'adaptation.json').write_text(json.dumps({
        'backend': 'NVIDIA ExtStress scalar CPU/WASM', 'sources': receipt,
        'transform': ('Replace platform includes; add per-instance opt-in physical operator options, declared convergence tolerance and read-only bond wrench hooks. Legacy options remain unchanged.'
                      if physical_extension else 'Replace x86 device query include; provide Emscripten CSR declarations. No solver algorithm changes.'),
        'physicalExtension': physical_extension,
        'physicalSolverRelativeTolerance': physical_tolerance if physical_extension else None,
        'sectionsAbi': 1 if physical_extension else 0,
        'physicalNumericalRevisions': [1, 2, 3] if physical_extension else [],
        'sectionsV3Abi': 1 if physical_extension else 0,
        'sectionsOperator': 'Minimum complementary energy via physical full6x6 interface stiffness; solve-only per-sample damage; exact normalized continuation; fresh original B-transpose residual admission' if physical_extension else None,
        'residualMeaning': 'Normal-equation gradient in mass/length-scaled coordinates; not force or torque units.'
    }, indent=2) + '\n', encoding='utf-8')
    return generated

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.root, args.output)
