# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Admit the exact source-built Flow corpus; no cached object or runtime claim."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import physx_lab as lab

ROOT = lab.ROOT
sys.path.insert(0, str(ROOT))
from addons.flow.component_evidence import member as component_member
CORPUS_COUNTS = {'manifest.json': 97, 'addons/solid/manifest.json': 21,
                 'addons/scalar/manifest.json': 4, 'addons/momentum/manifest.json': 2}
CAPABILITIES = dict.fromkeys(('flowHostAbi', 'flowSolidBoundaryAbi', 'flowScalarSourceAbi',
                             'flowRebaseAbi', 'flowMomentumExchangeAbi'), 1)


def member(root: Path, name: str) -> Path:
    """Reuse the component's contained, regular-file admission requirements."""
    try:
        return component_member(root, name)
    except ValueError as error:
        raise lab.LabError(str(error)) from error


def upstream_inputs(root: Path = ROOT) -> dict[str, str]:
    folder = root / 'source-inputs/flow'
    pin = json.loads(member(folder, 'source-pins.json').read_text(encoding='utf-8'))
    expected = pin.get('files')
    if (pin.get('schema') != 'physx-pe.flow-upstream-source-inputs/v1'
            or pin.get('upstreamCommit') != lab.LOCK['upstream_commit']
            or not isinstance(expected, dict) or len(expected) != 265):
        raise lab.LabError('Different upstream Flow source selection')
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual != set(expected) | {'source-pins.json'}:
        raise lab.LabError('Upstream Flow input inventory changed')
    for name, digest in expected.items():
        if lab.sha256(member(folder, name)) != digest:
            raise lab.LabError('Pinned upstream Flow source changed: ' + name)
    return dict(expected)


def shader_inventory(root: Path = ROOT) -> dict[str, dict]:
    """Retain the original 195 files and exactly 57 additional corpus files."""
    upstream_inputs(root)
    folder = root / 'dist/flow-wgsl'
    names: set[str] = set()
    shader_names: set[str] = set()
    for manifest_name, count in CORPUS_COUNTS.items():
        data = json.loads(member(folder, manifest_name).read_text(encoding='utf-8'))
        rows = data.get('shaders')
        if not isinstance(rows, list) or len(rows) != count:
            raise lab.LabError('Incomplete Flow corpus: ' + manifest_name)
        if manifest_name == 'manifest.json' and (data.get('status') != 'FLOW_WGSL_CORPUS_COMPILED'
                or data.get('shaderCount') != 97 or data.get('failed') != 0):
            raise lab.LabError('Original Flow corpus did not compile completely')
        if manifest_name != 'manifest.json' and (type(data.get('abi')) is not int or data['abi'] != 1):
            raise lab.LabError('Different Flow extension corpus ABI')
        for name, digest in data.get('inputHashes', {}).items():
            if lab.sha256(member(root / 'source-inputs/flow', name)) != digest:
                raise lab.LabError('Upstream shader input changed: ' + name)
        for name, digest in data.get('sourceHashes', {}).items():
            if lab.sha256(member(root / 'addons/flow', name)) != digest:
                raise lab.LabError('First-party shader input changed: ' + name)
        names.add(manifest_name)
        for row in rows:
            if manifest_name == 'manifest.json' and row.get('status') != 'PASS':
                raise lab.LabError('Failed original Flow shader compilation')
            source_root = root if row.get('source', '').startswith('addons/') else root / 'source-inputs/flow'
            if lab.sha256(member(source_root, row['source'])) != row.get('sourceSha256'):
                raise lab.LabError('Flow shader source changed: ' + row['source'])
            for field in ('wgsl', 'reflection'):
                name = row[field]
                if name in names or name == manifest_name:
                    raise lab.LabError('Repeated Flow output member: ' + name)
                if lab.sha256(member(folder, name)) != row.get(field + 'Sha256'):
                    raise lab.LabError('Generated Flow output changed: ' + name)
                names.add(name)
                if field == 'wgsl':
                    stem = Path(name).stem
                    if stem in shader_names:
                        raise lab.LabError('Duplicate host pipeline name: ' + stem)
                    shader_names.add(stem)
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if len(names) != 252 or len(shader_names) != 124 or actual != names:
        raise lab.LabError('Flow output must contain exactly 124 kernels / 252 corpus files')
    original = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    base_names = {'manifest.json'} | {row[field] for row in original['shaders'] for field in ('wgsl', 'reflection')}
    if len(base_names) != 195 or len(names - base_names) != 57:
        raise lab.LabError('Original Flow inventory was displaced by extension outputs')
    return {name: {'bytes': member(folder, name).stat().st_size,
                   'sha256': lab.sha256(member(folder, name))} for name in sorted(names)}


def native_capabilities(manifest: dict) -> dict[str, int]:
    values = {key: manifest.get(key) for key in CAPABILITIES}
    if values != CAPABILITIES or any(type(value) is not int for value in values.values()):
        raise lab.LabError('Native Flow build metadata has an unknown capability')
    return values


def require_final_selection(root: Path = ROOT) -> dict:
    selected = json.loads((root / 'source-selection.json').read_text(encoding='utf-8'))
    if selected.get('schema') != 'physx-pe.source-selection/v2':
        raise lab.LabError('Missing or unknown final source-selection schema')
    if ('pendingNativeInputs' not in selected or not isinstance(selected['pendingNativeInputs'], list)
            or any(not isinstance(value, str) or not value.strip() for value in selected['pendingNativeInputs'])):
        raise lab.LabError('Explicit pending native-source inventory is required')
    status = selected.get('finalSelectionStatus')
    if status not in ('PENDING_NATIVE_INPUTS', 'FINAL_SOURCES_SELECTED_FOR_BUILD'):
        raise lab.LabError('Missing or unknown final source-selection status')
    if status == 'PENDING_NATIVE_INPUTS' or selected['pendingNativeInputs']:
        raise lab.LabError('Final unified source selection is pending: '
                           + '; '.join(selected['pendingNativeInputs'] or ['explicit pending status']))
    if selected.get('upstreamCommit') != lab.LOCK['upstream_commit']:
        raise lab.LabError('Final PhysX/Flow upstream identity differs from the full source pin')
    components = selected.get('selectedComponents')
    if not isinstance(components, dict) or set(components) != {'flow', 'blast', 'woodThermal'}:
        raise lab.LabError('Final Flow, Blast and thermal component identities are required')
    prefixes = {'flow': 'addons/flow/', 'blast': 'addons/blast/', 'woodThermal': 'addons/thermal/'}
    for name, row in components.items():
        if not isinstance(row, dict) or set(row) != {'manifestPath', 'manifestSha256', 'manifestBytes', 'nativeSourceHashes'}:
            raise lab.LabError('Incomplete final component selection: ' + name)
        manifest = member(root, row['manifestPath'])
        if (type(row['manifestBytes']) is not int or row['manifestBytes'] <= 0
                or manifest.stat().st_size != row['manifestBytes'] or lab.sha256(manifest) != row['manifestSha256']):
            raise lab.LabError('Selected component manifest identity changed: ' + name)
        hashes = row['nativeSourceHashes']
        if not isinstance(hashes, dict) or not hashes or not any(path.endswith('.cpp') for path in hashes):
            raise lab.LabError('Selected native component lacks actual C++ source inputs: ' + name)
        for path, digest in hashes.items():
            if (not path.startswith(prefixes[name]) or not isinstance(digest, str)
                    or not re.fullmatch(r'[0-9a-f]{64}', digest)
                    or selected.get('stableBridgeSources', {}).get(path) != digest
                    or lab.sha256(member(root, path)) != digest):
                raise lab.LabError('Selected native component input changed: ' + path)
    from native_components import selected_source_inputs
    selected_source_inputs(root)
    return selected
