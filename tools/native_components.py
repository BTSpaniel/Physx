# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Fresh source recipes for the selected Blast and owner-MIT thermal components.

The external build receipts are identity/provenance data only. No archived
object, loader, WASM, Alpha orchestration helper or previous output is linked.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
import uuid

import physx_lab as lab

ROOT = lab.ROOT
REFERENCE = 'reference/combined-native-06'
sys.path.insert(0, str(ROOT / 'addons/blast'))
from prepare_stress_sources import prepare as prepare_stress
from prepare_authoring_sources import authoring_layout, prepare as prepare_authoring

STRICT_FLAGS = ('-O3', '-fno-fast-math', '-fno-associative-math', '-ffp-contract=off', '-msimd128')
THERMAL_FLAGS = ('-std=c++17', '-O3', '-fno-fast-math', '-fno-associative-math',
                 '-ffp-contract=off', '-fno-rtti', '-fno-exceptions', '-msimd128',
                 '-Wall', '-Wextra', '-Werror', '-DPR_THERMAL_STABLE_DEPLETION=1')


def selected_source_inputs(root: Path = ROOT) -> dict[str, str]:
    """Recheck actual selected sources, raw upstream pins and reversible notices."""
    from flow_source_evidence import member
    selection = json.loads(member(root, 'source-selection.json').read_text(encoding='utf-8'))
    selected = selection.get('prospectiveNativeSelection')
    if not isinstance(selected, dict) or selected.get('schema') != 'physx-pe.full-native-selection/v1':
        raise lab.LabError('Missing complete prospective native source selection')
    expected = selected.get('sourceHashes')
    if not isinstance(expected, dict) or len(expected) != 215:
        raise lab.LabError('Missing selected native source closure')
    for key, value in (('blastTranslationUnits', 27), ('thermalAbi', 2), ('thermalNumerics', 9),
                       ('massAbi', 1), ('preparationAbi', 1), ('convexAbi', 1)):
        if type(selected.get(key)) is not int or selected[key] != value:
            raise lab.LabError('Selected native capability identity changed: ' + key)
    for name, digest in expected.items():
        if lab.sha256(member(root, name)) != digest:
            raise lab.LabError('Selected native source changed: ' + name)
    from generate_addon_types import native_signatures
    signatures = {name: {'returnType': result, 'parameters': parameters}
                  for name, (result, parameters) in native_signatures(root).items()}
    contract = json.loads(member(root, selected.get('addonSignatureContract', '')).read_text(encoding='utf-8'))
    if (selected.get('signatureCount') != 96 or len(signatures) != 96
            or contract.get('schema') != 'physx-pe.selected-addon-exports/v1'
            or signatures != contract.get('exports')
            or selected.get('cachedObjectsAdmitted') is not False
            or selected.get('alphaExecutionDependenciesAdmitted') is not False):
        raise lab.LabError('Selected actual 96-export contract or source-only admission changed')
    pin = json.loads(member(root, 'source-inputs/blast/source-pins.json').read_text(encoding='utf-8'))
    if (pin.get('schema') != 'physx-pe.pinned-blast-source/v1'
            or pin.get('upstreamCommit') != lab.LOCK['upstream_commit']
            or len(pin.get('sourceHashes', {})) != 182
            or pin.get('compiledObjectsAdmitted') is not False
            or pin.get('generatedSourcesAdmitted') is not False):
        raise lab.LabError('Different raw Blast source selection')
    folder = root / 'source-inputs/blast'
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual != set(pin['sourceHashes']) | {'source-pins.json'}:
        raise lab.LabError('Raw Blast source inventory changed')
    for name, digest in pin['sourceHashes'].items():
        if lab.sha256(member(folder, name)) != digest:
            raise lab.LabError('Raw pinned Blast source changed: ' + name)
    transform = json.loads(member(root, REFERENCE + '/provenance/thermal-license-transform.json')
                           .read_text(encoding='utf-8'))
    if (transform.get('schema') != 'physx-pe.owner-thermal-license-transform/v1'
            or transform.get('onlyLicenseIdentifierChanged') is not True
            or transform.get('freshCompilationRequired') is not True
            or transform.get('originalArtifactsSelected') is not False
            or len(transform.get('files', {})) != 4):
        raise lab.LabError('Invalid owner thermal license transform')
    derivation_name = selected.get('ownerThermalSourceDerivation')
    derivation = None
    if derivation_name is not None:
        if derivation_name != 'provenance/thermal-finite-char-rate-source-selection-01.json':
            raise lab.LabError('Unknown owner thermal source derivation')
        component = selection.get('selectedComponents', {}).get('woodThermal', {})
        path = member(root, derivation_name)
        derivation = json.loads(path.read_text(encoding='utf-8'))
        if (component.get('manifestPath') != derivation_name
                or component.get('manifestBytes') != path.stat().st_size
                or component.get('manifestSha256') != lab.sha256(path)
                or derivation.get('schema') != 'physx-pe.owner-thermal-source-derivation/v1'
                or derivation.get('status') != 'SOURCE_DERIVED_NOT_COMPILED_NOT_RUNTIME_VERIFIED'
                or derivation.get('onlyNativeSourceChanged') != 'addons/thermal/pr_wood_thermal.cpp'
                or derivation.get('cachedObjectsAdmitted') is not False
                or derivation.get('compiledArtifactsAdmitted') is not False
                or derivation.get('engineAdmission') is not False
                or derivation.get('realtimeAdmission') is not False
                or derivation.get('source') != {'file': 'addons/thermal/pr_wood_thermal.cpp',
                    'before': {'bytes': 20532, 'sha256': '2dc948452bf58895e793c4a07375ff502164f09839b7057528c27c9e01a62856'},
                    'after': {'bytes': 20592, 'sha256': '43032e2dbcdf46f46fedce2d3b29c0881981b387337d5bd0178e6faf4eeb0442'}}
                or derivation.get('historicalLicenseTransform') != {
                    'file': REFERENCE + '/provenance/thermal-license-transform.json',
                    'bytes': 2386, 'sha256': '5221d38283d191d12ab83953af1fb3d05ef48c90ea5563aa1dd67bcbeabfe885'}):
            raise lab.LabError('Invalid explicit owner thermal source derivation')
    for name, row in transform['files'].items():
        original = member(root, REFERENCE + '/' + row['originalPath']).read_bytes()
        chosen = member(root, name).read_bytes()
        licensed = original.replace(b'SPDX-License-Identifier: LicenseRef-ParticleRealms-Alpha',
                                    b'SPDX-License-Identifier: MIT')
        if (lab.sha256(member(root, REFERENCE + '/' + row['originalPath'])) != row['originalSha256']
                or hashlib.sha256(licensed).hexdigest() != row['selectedSha256']
                or len(original) != row['originalBytes'] or len(licensed) != row['selectedBytes']):
            raise lab.LabError('Thermal transformation changes more than owner license identifier: ' + name)
        expected_chosen = licensed
        if derivation is not None and name == 'addons/thermal/pr_wood_thermal.cpp':
            anchor = b'                const double charRate = reactionRate(l[CHAR], l[INITIAL] * material[10], material + 60, l[TEMPERATURE], w[CHAR_FACTOR]);\r\n'
            if licensed.count(anchor) != 1:
                raise lab.LabError('Owner thermal finite char-rate anchor changed')
            expected_chosen = licensed.replace(anchor, anchor + b'                if (!isFinite(charRate)) return NONFINITE;\r\n', 1)
        if chosen != expected_chosen:
            raise lab.LabError('Thermal source differs from exact licensed origin or explicit finite char-rate derivation: ' + name)
    return dict(expected)


def prepare_blast_sources(sdk: Path, root: Path = ROOT) -> dict:
    """Regenerate the exact extensions from raw pinned NVIDIA source bytes."""
    selected_source_inputs(root)
    pins = json.loads((root / 'source-inputs/blast/source-pins.json').read_text())['sourceHashes']
    for name, digest in pins.items():
        if not (sdk / name).is_file() or lab.sha256(sdk / name) != digest:
            raise lab.LabError('Prepared upstream Blast differs from selected input: ' + name)
    stress_dir = root / 'work/blast-stress-generated'
    stress = prepare_stress(sdk, stress_dir, physical_extension=True, physical_tolerance=1e-6)
    authoring = prepare_authoring(sdk, root / 'work/blast-authoring-generated')
    origin = json.loads((root / REFERENCE / 'provenance/selected-blast-build.json').read_text())
    expected = {Path(n).name: h for n, h in origin['sources'].items()
                if n.startswith('work/') and n.endswith(('.cpp', '.h'))}
    generated = [*stress, stress_dir / 'pr_section_cgnr.h', authoring]
    if set(p.name for p in generated) != set(expected):
        raise lab.LabError('Selected generated Blast source set changed')
    for path in generated:
        if lab.sha256(path) != expected[path.name]:
            raise lab.LabError('Fresh generated Blast source differs: ' + path.name)
    return {'generated': {p.relative_to(root).as_posix(): lab.sha256(p) for p in generated},
            'physicalTolerance': 1e-6, 'massAbi': 1, 'preparationAbi': 1}


def blast_recipe(sdk: Path, root: Path = ROOT) -> tuple[list[Path], list[str]]:
    """Preserve selected 27-TU/include order with explicit strict FP flags."""
    stress_dir = root / 'work/blast-stress-generated'
    sources = sorted((sdk / 'source/sdk/common').glob('*.cpp'))
    sources += sorted((sdk / 'source/sdk/lowlevel').glob('*.cpp'))
    if len(sources) != 10:
        raise lab.LabError('Expected exactly ten selected Blast core translation units')
    authoring, extra_includes = authoring_layout(sdk)
    sources += [(root / 'work/blast-authoring-generated/NvBlastExtApexSharedParts.cpp')
                if p.name == 'NvBlastExtApexSharedParts.cpp' else p for p in authoring]
    sources += [root / 'addons/blast/pr_blast_wasm.cpp', root / 'addons/blast/pr_blast_authoring.cpp',
                stress_dir / 'NvBlastExtStressSolver.cpp', stress_dir / 'stress.cpp',
                sdk / 'source/sdk/globals/NvBlastGlobals.cpp']
    if len(sources) != 27 or len(set(sources)) != 27:
        raise lab.LabError('Expected exactly 27 distinct selected Blast translation units')
    includes = [stress_dir, root / 'addons/blast'] + [sdk / name for name in (
        'include/extensions/stress', 'include/globals', 'include/lowlevel',
        'include/shared/NvFoundation', 'source/sdk/common', 'source/sdk/lowlevel',
        'source/shared/NsFoundation/include', 'source/shared/stress_solver')] + extra_includes
    flags = ['-include', str(root / 'addons/blast/emscripten_nv_compat.h'),
             '-std=c++17', *STRICT_FLAGS, '-mavx', '-DNDEBUG', '-fno-rtti', '-fno-exceptions']
    for include in includes:
        flags += ['-I', str(include)]
    return sources, flags


def compile_native_components(sdk: Path, root: Path = ROOT) -> dict:
    """Compile every selected TU freshly, then produce relocatable link inputs."""
    before = selected_source_inputs(root)
    generated = prepare_blast_sources(sdk, root)
    sources, flags = blast_recipe(sdk, root)
    phase = uuid.uuid4().hex
    output = root / 'work/selected-native-objects' / phase
    output.mkdir(parents=True, exist_ok=True)
    version = lab.run(['em++', '--version'], cwd=root, timeout=60).stdout
    if lab.LOCK['emscripten'] not in version.splitlines()[0].split():
        raise lab.LabError('Selected native components require the pinned Emscripten compiler')
    def compile_one(item):
        index, source = item
        obj = output / f'{index:02d}-{source.stem}.o'
        argv = ['em++', source, *flags, '-c', '-o', obj]
        print('Fresh selected Blast compile: ' + source.name, flush=True)
        result = lab.run(argv, cwd=root, timeout=1800)
        if not obj.is_file() or obj.stat().st_size < 8:
            raise lab.LabError('Compiler did not produce selected Blast object: ' + source.name)
        return {'source': str(source), 'argv': list(map(str, argv)), 'stdout': result.stdout,
                'stderr': result.stderr, 'exitCode': result.returncode,
                'object': obj.relative_to(root).as_posix(), 'sha256': lab.sha256(obj)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(compile_one, enumerate(sources)))
    blast = root / 'work/pr_blast.o'
    partial = ['em++', *(root / row['object'] for row in rows), '-r', '-o', blast]
    partial_result = lab.run(partial, cwd=root, timeout=1800)
    thermal = root / 'work/pr_wood_thermal_multirate.o'
    thermal_argv = ['em++', root / 'addons/thermal/pr_wood_thermal_multirate.cpp',
                    *THERMAL_FLAGS, '-c', '-o', thermal]
    thermal_result = lab.run(thermal_argv, cwd=root, timeout=1800)
    if before != selected_source_inputs(root):
        raise lab.LabError('Selected native inputs changed during fresh compilation')
    if generated != prepare_blast_sources(sdk, root):
        raise lab.LabError('Prepared Blast inputs changed during fresh compilation')
    for obj in (blast, thermal):
        if not obj.is_file() or obj.stat().st_size < 8:
            raise lab.LabError('Fresh selected component link input missing: ' + str(obj))
    receipt = {'schema': 'physx-pe.fresh-selected-native-build/v1',
               'status': 'COMPILED_NOT_RUNTIME_VERIFIED', 'builtUtc': datetime.now(timezone.utc).isoformat(),
               'compiler': version, 'sources': before, 'sourcesAfter': before,
               'reportPath': 'reports/fresh-selected-native-build-' + phase + '.json',
               'compilerOutputMode': 'stdout-and-stderr-combined-by-existing-lab-runner',
               'generated': generated, 'blastCompileFlags': flags, 'blastCompileCommands': rows,
               'allTranslationUnitsRecompiled': True, 'archivedObjectsUsed': False,
               'blastPartialLink': {'argv': list(map(str, partial)), 'stdout': partial_result.stdout,
                                    'stderr': partial_result.stderr, 'exitCode': partial_result.returncode},
               'thermalCompile': {'argv': list(map(str, thermal_argv)), 'stdout': thermal_result.stdout,
                                  'stderr': thermal_result.stderr, 'exitCode': thermal_result.returncode},
               'artifacts': {p.relative_to(root).as_posix(): {'sha256': lab.sha256(p), 'bytes': p.stat().st_size}
                             for p in (blast, thermal)}}
    lab.write_json(root / receipt['reportPath'], receipt)
    return receipt
