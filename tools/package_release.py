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


def package() -> Path:
    from release import VERSION, inventory, selection, shader_inventory, source_revision
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
    for path in sorted((ROOT / 'dist/flow-wgsl').rglob('*')):
        if path.is_file():
            include(path)
    for relative in ('addons/flow/flow_host_webgpu.mjs', 'addons/flow/webgpu_bridge.mjs',
                     'bridge/physx-bulk.mjs', 'bridge/physx-bulk-rust.mjs',
                     'tools/serve.py', 'LICENSE', 'README.md', 'AUTHORS.md',
                     'PROVENANCE.md', 'THIRD_PARTY_NOTICES.md', 'source-selection.json',
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
    archive = ROOT / 'dist' / f'physx-pe-{VERSION}.zip'
    if not archive.resolve().is_relative_to((ROOT / 'dist').resolve()):
        raise lab.LabError('Archive target escapes release dist')
    with tempfile.TemporaryDirectory(prefix='.package-', dir=ROOT / 'dist') as temporary:
        staging = Path(temporary) / archive.name
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
        # Only a completed extracted-archive simulation can replace an old archive.
        staging.replace(archive)
    checksum = archive.with_suffix(archive.suffix + '.sha256')
    checksum.write_text(lab.sha256(archive) + '  ' + archive.name + '\n', encoding='utf-8')
    lab.write_json(ROOT / 'dist/release-artifacts.json', {
        'schema': 'physx-pe.release-artifacts/v1', 'version': VERSION,
        'sourceRevision': revision,
        'status': 'VERIFIED_ALPHA_ARCHIVE', 'archive': {'name': archive.name,
        'bytes': archive.stat().st_size, 'sha256': lab.sha256(archive)},
        'archiveSmokeReportSha256': lab.sha256(ROOT / 'reports/package-smoke.json'),
        'verificationScope': summary['scope']})
    print('Verified archive:', archive)
    return archive
