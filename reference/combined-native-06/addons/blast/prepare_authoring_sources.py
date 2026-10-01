# SPDX-License-Identifier: MIT
"""Adapt pinned scalar vector types without altering NVIDIA authoring algorithms."""
import hashlib
import json
import re
from pathlib import Path

SOURCE = 'source/sdk/extensions/authoring/NvBlastExtApexSharedParts.cpp'
SHA256 = '001f75f334b6cab8bfffdaaa29b2cdfc2359127d50c1273d05942d00ba0ed1e7'
AUTHORING_NAMES = (
    'NvBlastExtAuthoringFractureToolImpl.cpp', 'NvBlastExtAuthoringBooleanToolImpl.cpp',
    'NvBlastExtAuthoringBondGeneratorImpl.cpp', 'NvBlastExtAuthoringMeshUtils.cpp',
    'NvBlastExtAuthoringTriangulator.cpp', 'NvBlastExtAuthoringMeshNoiser.cpp',
    'NvBlastExtAuthoringPatternGeneratorImpl.cpp', 'NvBlastExtAuthoringCutoutImpl.cpp',
    'NvBlastExtApexSharedParts.cpp', 'NvBlastExtTriangleProcessor.cpp',
)
AUTHORING_INCLUDES = (
    'include/extensions/authoring', 'include/extensions/authoringCommon',
    'source/sdk/extensions/authoring', 'source/sdk/extensions/authoringCommon',
)

def authoring_layout(root):
    sources = [root / 'source/sdk/extensions/authoring' / name for name in AUTHORING_NAMES]
    sources += [root / 'source/sdk/extensions/authoringCommon' / name for name in
                ('NvBlastExtAuthoringMeshImpl.cpp', 'NvBlastExtAuthoringAcceleratorImpl.cpp')]
    return sources, [root / name for name in AUTHORING_INCLUDES]

def prepare(root: Path, output: Path) -> Path:
    data = (root / SOURCE).read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Pinned NVIDIA Apex authoring source differs')
    source = data.decode('utf-8')
    start = source.index('static void _arrayVec3ToVec4(')
    end = source.index('// TODO: move this to a better long term home', start)
    block = source[start:end]
    block, count = re.subn(r'Vec3V (v[0-3]) = (V3(?:LoadU|Mul)\([^\n]+);',
                          r'Vec4V \1 = Vec4V_From_Vec3V(\2);', block)
    if count != 8 or block.count('Vec3V work[4];') != 2:
        raise ValueError('Unexpected NVIDIA scalar packing helper layout')
    block = block.replace('Vec3V work[4];', 'Vec4V work[4];')
    block, count = re.subn(r'work\[i\] = (V3(?:LoadU|Mul)\([^\n]+);',
                          r'work[i] = Vec4V_From_Vec3V(\1);', block)
    if count != 2:
        raise ValueError('Unexpected NVIDIA scalar remainder packing layout')
    # The SSE implementation aliases both vector types. The scalar backend needs
    # this explicit upstream conversion before its unchanged four-lane transpose.
    encoded = (source[:start] + block + source[end:]).encode('utf-8')
    output.mkdir(parents=True, exist_ok=True)
    target = output / Path(SOURCE).name
    if not target.is_file() or target.read_bytes() != encoded:
        target.write_bytes(encoded)
    (output / 'adaptation.json').write_text(json.dumps({
        'backend': 'NVIDIA ExtAuthoring scalar CPU/WASM',
        'sources': {SOURCE: {'sourceSha256': SHA256,
                             'generatedSha256': hashlib.sha256(encoded).hexdigest()}},
        'transform': 'Two packing helpers use Vec4V temporaries and upstream Vec4V_From_Vec3V conversion before unchanged V4Transpose. No authoring algorithm changes.'
    }, indent=2) + '\n')
    return target
