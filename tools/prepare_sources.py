# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Prepare the pinned NVIDIA checkout and reviewed browser overlay without a merge."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import physx_lab as lab

ROOT = lab.ROOT


def modified_text(path: Path, text: str) -> str:
    """Keep every upstream header and prominently identify the port modification."""
    message = 'Modified for the Particle Realms PhysX PE browser port; upstream notices retained.'
    if path.suffix == '.xml':
        banner = '<!-- ' + message + ' -->\n'
        if text.startswith('<?xml'):
            end = text.index('?>') + 2
            text = text[:end] + '\n' + banner + text[end:]
        else:
            text = banner + text
    elif path.suffix == '.bat':
        text = 'REM ' + message + '\n' + text
    elif path.suffix in ('.h', '.cpp', '.idl', '.js'):
        text = '// ' + message + '\n' + text
    else:
        banner = '# ' + message + '\n'
        if text.startswith('#!'):
            first, rest = text.split('\n', 1)
            text = first + '\n' + banner + rest
        else:
            text = banner + text
    return text


def added_linkage(patch: Path, relative: str) -> str:
    """Recover the exact added CMake file from the reviewed portable patch."""
    blocks = re.split(r'(?=^diff --git )', patch.read_text(encoding='utf-8'), flags=re.M)
    matching = [block for block in blocks if block.startswith(f'diff --git a/{relative} b/{relative}\n')]
    if len(matching) != 1 or 'new file mode 100644\n' not in matching[0]:
        raise lab.LabError('Reviewed linkage is not one complete added source file')
    lines = matching[0].splitlines()
    hunks = [index for index, line in enumerate(lines) if line.startswith('@@ ')]
    if len(hunks) != 1 or not re.fullmatch(r'@@ -0,0 \+1,84 @@', lines[hunks[0]]):
        raise lab.LabError('Reviewed linkage file inventory changed')
    content = lines[hunks[0] + 1:]
    if len(content) != 84 or any(not line.startswith('+') for line in content):
        raise lab.LabError('Reviewed linkage contains an unexpected patch operation')
    return modified_text(Path(relative), ''.join(line[1:] + '\n' for line in content))


def prepare() -> dict:
    lock = lab.LOCK
    patch = ROOT / 'patches/browser-overlay.patch'
    stream = ROOT / 'source-overlays/PrWasmStreams.h'
    if lab.sha256(patch) != lock['browser_overlay_sha256']:
        raise lab.LabError('Reviewed browser overlay changed')
    if lab.sha256(stream) != lock['stream_adapter_sha256']:
        raise lab.LabError('Reviewed stream adapter changed')
    target = ROOT / 'work/candidate/PhysX'
    receipt_path = ROOT / 'work/source-preparation.json'
    if target.exists():
        if not (target / '.git').is_dir() or not receipt_path.is_file():
            raise lab.LabError('Existing source path is not owned by this preparation; preserve it')
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if (receipt.get('upstreamCommit') != lock['upstream_commit'] or
                receipt.get('overlaySha256') != lab.sha256(patch) or
                lab.git(target, 'rev-parse', 'HEAD') != lock['upstream_commit']):
            raise lab.LabError('Existing source checkout identity differs; preserve it')
        # Only deterministic build-owned linkage and exact copied bulk bytes
        # may differ from the reviewed overlay. Other upstream edits fail closed.
        linkage = 'physx/source/compiler/cmake/emscripten/PhysXWasmBindings.cmake'
        expected_paths = set(re.findall(r'^diff --git a/(.*?) b/.*$', patch.read_text(encoding='utf-8'), re.M))
        if set(receipt['modifiedSources']) != expected_paths or len(expected_paths) != 141:
            raise lab.LabError('Prepared overlay receipt differs from its exact 141-file inventory')
        for relative, expected in receipt['modifiedSources'].items():
            if relative != linkage and lab.sha256(target / relative) != expected:
                raise lab.LabError('Prepared source changed: ' + relative)
        base = added_linkage(patch, linkage)
        if hashlib.sha256(base.encode('utf-8')).hexdigest() != receipt['modifiedSources'][linkage]:
            raise lab.LabError('Prepared linkage receipt differs from the reviewed overlay')
        if lab.sha256(target / linkage) != receipt['modifiedSources'][linkage]:
            import sys
            sys.path.insert(0, str(ROOT))
            from build import unified_linkage
            if (target / linkage).read_bytes() != unified_linkage(base, target).encode('utf-8'):
                raise lab.LabError('Generated CMake linkage differs from its deterministic source')
        destination = 'physx/source/webidlbindings/src/common/PrWasmStreams.h'
        if (receipt['streamAdapterSha256'] != lock['stream_adapter_sha256'] or
                lab.sha256(target / destination) != lock['stream_adapter_sha256']):
            raise lab.LabError('Copied stream adapter changed')
        owned = set(receipt['modifiedSources']) | {destination}
        for name in ('pr_bulk_rust.cpp', 'pr_rust_core.h'):
            relative = 'physx/source/webidlbindings/pr_rust_bulk/' + name
            if (target / relative).exists():
                if lab.sha256(target / relative) != lab.sha256(ROOT / 'bridge' / name):
                    raise lab.LabError('Copied bulk source changed: ' + name)
                owned.add(relative)
        changed = set(filter(None, lab.git(target, 'diff', 'HEAD', '--name-only', '--no-renames', '-z').split('\0')))
        untracked = set(filter(None, lab.git(target, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')))
        # generate_projects creates four exact output trees. They contain
        # CMake/glue/object artifacts and cannot stand in for upstream sources.
        output_roots = [target / 'physx/compiler' / ('emscripten-' + name)
                        for name in ('release', 'checked', 'debug', 'profile')]
        for output in output_roots:
            if output.exists() and (not output.is_dir() or output.is_symlink()
                                    or not output.resolve().is_relative_to(target.resolve())):
                raise lab.LabError('Generated build output path escapes its owned tree')
        output_prefixes = tuple(path.relative_to(target).as_posix() + '/' for path in output_roots)
        unexpected = (changed - owned) | {name for name in untracked - owned
                                         if not name.startswith(output_prefixes)}
        if unexpected:
            raise lab.LabError('Unexpected upstream source changes: ' + ', '.join(sorted(unexpected)))
        return receipt
    lab.require(['git'])
    target.parent.mkdir(parents=True, exist_ok=True)
    lab.run(['git', 'init', target])
    # Hash-identical source text must not depend on the host Git CRLF default.
    lab.git(target, 'config', 'core.autocrlf', 'false')
    lab.git(target, 'config', 'core.eol', 'lf')
    lab.git(target, 'remote', 'add', 'origin', lock['upstream_repo'])
    lab.run(['git', '-C', target, 'fetch', '--depth', '1', 'origin', lock['upstream_commit']], timeout=1800)
    lab.git(target, 'checkout', '--detach', 'FETCH_HEAD')
    if lab.git(target, 'rev-parse', 'HEAD') != lock['upstream_commit']:
        raise lab.LabError('Fetched upstream commit differs from its full pin')
    lab.run(['git', '-C', target, 'apply', '--check', patch])
    lab.run(['git', '-C', target, 'apply', patch])
    version = lab.sdk_version((target / 'physx/include/foundation/PxPhysicsVersion.h').read_text())
    if version != '5.11.0':
        raise lab.LabError('Prepared SDK version differs from 5.11.0')
    idl = target / 'physx/source/webidlbindings/src/wasm/PhysXWasm.idl'
    idl_hash = lab.sha256(idl)
    if idl_hash != lock['declaration_idl_sha256']:
        raise lab.LabError('Checked-in declarations belong to a different IDL source')
    modified = re.findall(r'^diff --git a/(.*?) b/.*$', patch.read_text(encoding='utf-8'), re.M)
    if len(modified) != 141 or len(set(modified)) != len(modified):
        raise lab.LabError('Unexpected reviewed overlay file inventory')
    for relative in modified:
        path = target / relative
        path.write_text(modified_text(path, path.read_text(encoding='utf-8')), encoding='utf-8', newline='\n')
    destination = target / 'physx/source/webidlbindings/src/common/PrWasmStreams.h'
    shutil.copyfile(stream, destination)
    receipt = {'schema': 'physx-pe.prepared-source/v1', 'upstreamCommit': lock['upstream_commit'],
               'overlaySha256': lab.sha256(patch), 'originalIdlSha256': idl_hash,
               'preparedIdlSha256': lab.sha256(idl),
               'modifiedSources': {name: lab.sha256(target / name) for name in modified},
               'streamAdapterSha256': lab.sha256(destination)}
    lab.write_json(receipt_path, receipt)
    return receipt


if __name__ == '__main__':
    print(json.dumps(prepare(), indent=2))
