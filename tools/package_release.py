# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Package only verified current runtime bytes, then test the extracted archive."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import physx_lab as lab
import evidence

ROOT = lab.ROOT


def admit_endurance_phase(verified: dict, build: dict) -> None:
    """Require this invocation's bounded execution receipt before packaging."""
    from browser_compat_endurance import ENDURANCE_SOURCES, validate_endurance
    rows = [row for row in verified.get('steps', [])
            if row.get('name') == 'Bounded native CPU endurance and identical-input replay']
    if len(rows) != 1 or rows[0].get('status') != 'PASS' or rows[0].get('testCount') != 3:
        raise lab.LabError('Packaging requires exactly one completed bounded CPU endurance phase')
    row = rows[0]
    phase = ROOT / row['reportFile']
    if (not phase.is_file() or phase.is_symlink()
            or not phase.resolve().is_relative_to((ROOT / 'reports/phases').resolve())
            or lab.sha256(phase) != row.get('reportSha256')):
        raise lab.LabError('Bounded CPU endurance phase bytes changed or escaped their report directory')
    raw = evidence.object_json(phase)
    if raw.get('releasePhase') is not True:
        raise lab.LabError('Bounded CPU endurance was not executed as a release phase')
    admission = validate_endurance(raw, build, evidence.source_hashes(ROOT, ENDURANCE_SOURCES),
        not_before=evidence.instant(verified['startedUtc'], 'release started'))
    if row.get('checks') != admission or row.get('resultStatus') != raw['status']:
        raise lab.LabError('Bounded CPU endurance summary differs from its executed receipt')


def package() -> Path:
    from release import VERSION, inventory, selection, shader_inventory, source_revision
    from flow_source_evidence import require_final_selection
    require_final_selection(ROOT)
    selection()
    source_hashes = inventory()
    revision = source_revision(source_hashes)
    verified = json.loads((ROOT / 'reports/release-verification.json').read_text(encoding='utf-8'))
    if (verified.get('status') != 'BUILD_AND_BROWSER_SMOKE_PASSED_ALPHA' or
            verified.get('sourceHashesBefore') != source_hashes or
            verified.get('sourceHashesAfter') != source_hashes):
        raise lab.LabError('Current release sources have no matching completed verification')
    if verified.get('sourceRevision') != revision:
        raise lab.LabError('Verification belongs to another committed source revision')
    build = json.loads((ROOT / 'dist/candidate/build-manifest.json').read_text())
    evidence.verify_artifacts(build, ROOT / 'dist/candidate', 'candidate')
    if verified.get('artifacts') != build['artifacts']:
        raise lab.LabError('Verified runtime artifact identity changed')
    admit_endurance_phase(verified, build)
    shaders = shader_inventory()
    if verified.get('shaderHashesBefore') != shaders or verified.get('shaderHashesAfter') != shaders:
        raise lab.LabError('Current shaders/sidecars differ from their verified inventory')
    compiled = json.loads((ROOT / 'reports/release-build.json').read_text())
    prepared = ROOT / 'work/source-preparation.json'
    if (compiled.get('sourceHashes') != source_hashes or compiled.get('artifacts') != build['artifacts']
            or compiled.get('sourceRevision') != revision
            or compiled.get('flowShaderInventory') != shaders
            or compiled.get('preparedSourceReceiptSha256') != lab.sha256(prepared)):
        raise lab.LabError('Prepared source/build receipt changed after compilation')
    payload: dict[str, bytes] = {}

    def include(path: Path, name: str | None = None) -> None:
        name = name or path.relative_to(ROOT).as_posix()
        if name in payload:
            raise lab.LabError('Duplicate archive path: ' + name)
        payload[name] = path.read_bytes()

    for name in ('physx-pe.mjs', 'physx-pe.wasm', 'build-manifest.json'):
        include(ROOT / 'dist/candidate' / name)
    declarations = ROOT / 'dist/candidate/physx-pe.d.ts'
    if declarations.read_bytes() != (ROOT / 'types/physx-pe.d.ts').read_bytes():
        raise lab.LabError('Generated declarations changed from their reviewed IDL reference')
    include(declarations)
    esm_declarations = ROOT / 'dist/candidate/physx-pe.d.mts'
    if esm_declarations.read_bytes() != declarations.read_bytes():
        raise lab.LabError('ES module declarations differ from the matched runtime declarations')
    include(esm_declarations)
    from generate_addon_types import generate
    for relative, expected in generate().items():
        if (ROOT / relative).read_bytes() != expected:
            raise lab.LabError('Addon declarations differ from their actual ABI: ' + relative)
        include(ROOT / relative)
    include(ROOT / 'types/webgpu.d.ts')
    for path in sorted((ROOT / 'dist/flow-wgsl').rglob('*')):
        if path.is_file():
            include(path)
    for relative in ('addons/flow/flow_host_webgpu.mjs', 'addons/flow/webgpu_bridge.mjs',
                     'addons/flow/flow_solid_boundary.mjs', 'addons/flow/flow_scalar_sources.mjs',
                     'bridge/physx-bulk.mjs', 'bridge/physx-bulk-rust.mjs',
                     'tools/serve.py', 'LICENSE', 'README.md', 'AUTHORS.md',
                     'PROVENANCE.md', 'THIRD_PARTY_NOTICES.md', 'source-selection.json', 'upstream.lock.json',
                     'license-provenance.json', 'source-license-map.json', 'upstream-modifications.json',
                     'nanovdb-provenance.json'):
        include(ROOT / relative)
    for folder in ('web', 'LICENSES'):
        for path in sorted((ROOT / folder).rglob('*')):
            if path.is_file():
                include(path)
    for row in verified['steps']:
        phase = ROOT / row['reportFile']
        if (row.get('status') != 'PASS' or not phase.resolve().is_relative_to((ROOT / 'reports/phases').resolve())
                or lab.sha256(phase) != row['reportSha256']):
            raise lab.LabError('Completed phase receipt changed: ' + row['name'])
        include(phase)
    include(prepared, 'reports/prepared-source.json')
    # This bounded report retains actual receipt hashes and test results while
    # omitting local command logs, source checkout paths and generated patches.
    summary = {key: verified[key] for key in
               ('schema', 'version', 'status', 'startedUtc', 'finishedUtc', 'gpuMode', 'scope', 'artifacts', 'sourceRevision')}
    summary['steps'] = [{key: row.get(key) for key in
                         ('name', 'status', 'reportFile', 'reportSha256', 'resultStatus', 'checks', 'testCount')}
                        for row in verified['steps']]
    summary['sourceInventorySha256'] = revision['inventorySha256']
    summary['rawVerificationSha256'] = lab.sha256(ROOT / 'reports/release-verification.json')
    payload['reports/verification.json'] = (json.dumps(summary, indent=2) + '\n').encode()
    manifest = {'schema': 'physx-pe.package-manifest/v1', 'version': VERSION,
                'sourceRevision': revision,
                'sdkVersion': '5.11.0', 'upstreamCommit': lab.LOCK['upstream_commit'],
                'browserOverlaySha256': lab.LOCK['browser_overlay_sha256'],
                'declarationIdlSha256': lab.LOCK['declaration_idl_sha256'],
                'scope': summary['scope'], 'gpuMode': summary['gpuMode'],
                'files': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in sorted(payload.items())}}
    payload['runtime-manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    output = ROOT / 'dist/release'
    if output.is_symlink() or not output.resolve().is_relative_to((ROOT / 'dist').resolve()):
        raise lab.LabError('Release output escapes release dist')
    archive_name = f'physx-pe-{VERSION}-runtime.zip'
    with tempfile.TemporaryDirectory(prefix='.package-', dir=ROOT / 'dist') as temporary:
        staged_output = Path(temporary) / 'release'
        staged_output.mkdir()
        staging = staged_output / archive_name
        with zipfile.ZipFile(staging, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for name, data in sorted(payload.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, data)
        lab.run([sys.executable, ROOT / 'tools/package_smoke.py', staging], timeout=600)
        if inventory() != source_hashes or shader_inventory() != shaders:
            raise lab.LabError('Release sources/generated shaders changed during packaging')
        if source_revision(source_hashes) != revision or lab.sha256(prepared) != compiled['preparedSourceReceiptSha256']:
            raise lab.LabError('Committed/prepared source changed during packaging')
        if declarations.read_bytes() != (ROOT / 'types/physx-pe.d.ts').read_bytes():
            raise lab.LabError('Generated declarations changed during packaging')
        evidence.verify_artifacts(build, ROOT / 'dist/candidate', 'candidate')
        for row in verified['steps']:
            if lab.sha256(ROOT / row['reportFile']) != row['reportSha256']:
                raise lab.LabError('Completed phase receipt changed during packaging')
        write_release_assets(staged_output, staging, VERSION, revision, summary['scope'],
                             lab.sha256(ROOT / 'reports/package-smoke.json'))
        install_release_output(staged_output, output)
    archive = output / archive_name
    print('Verified archive:', archive)
    return archive


def install_release_output(staged: Path, output: Path) -> None:
    """Keep completed releases immutable; an exact rerun is idempotent."""
    if output.is_symlink():
        raise lab.LabError('Existing release output is a symlink')
    if output.exists():
        if (not output.is_dir() or any(path.is_symlink() for path in output.rglob('*')) or
                {path.name: path.read_bytes() for path in output.iterdir() if path.is_file()} !=
                {path.name: path.read_bytes() for path in staged.iterdir()} or
                any(not path.is_file() for path in output.iterdir())):
            raise lab.LabError('Existing release output differs; refusing to overwrite it')
    else:
        staged.replace(output)


def write_release_assets(output: Path, archive: Path, version: str, revision: dict,
                         scope: str, smoke_report_sha256: str) -> dict:
    """Expose the exact matched files from the verified complete runtime ZIP."""
    direct = {}
    with zipfile.ZipFile(archive) as bundle:
        manifest_bytes = bundle.read('runtime-manifest.json')
        manifest = json.loads(manifest_bytes)
        for name in ('physx-pe.wasm', 'physx-pe.mjs', 'physx-pe.d.ts'):
            relative = 'dist/candidate/' + name
            data = bundle.read(relative)
            expected = manifest['files'][relative]
            if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
                raise lab.LabError('Direct runtime asset differs from its package manifest: ' + name)
            (output / name).write_bytes(data)
            direct[name] = {**expected, 'archivePath': relative}
    archive_entry = {'name': archive.name, 'bytes': archive.stat().st_size, 'sha256': lab.sha256(archive)}
    archive.with_suffix('.zip.sha256').write_text(archive_entry['sha256'] + '  ' + archive.name + '\n', encoding='utf-8', newline='\n')
    sums = {name: row['sha256'] for name, row in direct.items()}
    sums[archive.name] = archive_entry['sha256']
    checksums = output / 'SHA256SUMS'
    checksums.write_text(''.join(sums[name] + '  ' + name + '\n' for name in sorted(sums)), encoding='utf-8', newline='\n')
    metadata = {'schema': 'physx-pe.release-artifacts/v2', 'version': version,
                'sourceRevision': revision, 'status': 'VERIFIED_ALPHA_ARCHIVE',
                'archive': archive_entry, 'directAssets': direct,
                'runtimeManifest': {'archivePath': 'runtime-manifest.json', 'bytes': len(manifest_bytes),
                                    'sha256': hashlib.sha256(manifest_bytes).hexdigest()},
                'checksums': {'name': checksums.name, 'bytes': checksums.stat().st_size, 'sha256': lab.sha256(checksums)},
                'archiveSmokeReportSha256': smoke_report_sha256, 'verificationScope': scope}
    lab.write_json(output / 'release-artifacts.json', metadata)
    return metadata
