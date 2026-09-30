# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Install this release's pinned build tools in Linux/WSL, only with --install."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

import physx_lab as lab


def verify_slang_tree(archive: Path, destination: Path) -> dict[str, dict]:
    """Compare every compiler/library entry with the pinned archive on each reuse."""
    expected = {}
    with tarfile.open(archive, 'r:gz') as bundle:
        for member in bundle.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts:
                raise lab.LabError('Slang archive contains an unsafe member path')
            relative = name.as_posix()
            if relative == '.':
                continue
            path = destination / relative
            if not path.resolve().is_relative_to(destination.resolve()):
                raise lab.LabError('Slang tool member escapes its installed directory')
            if member.isdir():
                if not path.is_dir() or path.is_symlink():
                    raise lab.LabError('Slang tool directory differs: ' + relative)
                continue
            if relative in expected:
                raise lab.LabError('Slang archive has duplicate tool members')
            if member.issym():
                if not path.is_symlink() or os.readlink(path) != member.linkname:
                    raise lab.LabError('Slang tool symbolic link differs: ' + relative)
                expected[relative] = {'link': member.linkname}
            elif member.isfile() or member.islnk():
                original = bundle.extractfile(member)
                if original is None or not path.is_file() or path.is_symlink():
                    raise lab.LabError('Slang tool file is missing or differs: ' + relative)
                with original:
                    digest = hashlib.file_digest(original, 'sha256').hexdigest()
                if lab.sha256(path) != digest or path.stat().st_size != member.size and member.isfile():
                    raise lab.LabError('Slang tool file changed: ' + relative)
                if os.name != 'nt' and path.stat().st_mode & 0o111 != member.mode & 0o111:
                    raise lab.LabError('Slang tool executable permissions changed: ' + relative)
                expected[relative] = {'sha256': digest, 'bytes': path.stat().st_size}
            else:
                raise lab.LabError('Slang archive has an unsupported tool member')
    actual = {path.relative_to(destination).as_posix() for path in destination.rglob('*')
              if path.is_file() or path.is_symlink()}
    if actual != set(expected):
        raise lab.LabError('Installed Slang tool inventory differs from the pinned archive')
    return dict(sorted(expected.items()))


def install() -> None:
    if os.name == 'nt':
        raise lab.LabError('Run the build tool setup inside Linux/WSL')
    lab.require(['git', 'rustup', 'cmake', 'make'])
    root = lab.ROOT
    work = root / 'work'
    work.mkdir(exist_ok=True)
    version = lab.LOCK['emscripten']
    sdk = work / 'emsdk'
    url = 'https://github.com/emscripten-core/emsdk.git'
    if not sdk.exists():
        lab.run(['git', 'clone', '--depth', '1', '--branch', version, url, sdk], timeout=1800)
    if (not (sdk / '.git').is_dir() or lab.git(sdk, 'remote', 'get-url', 'origin') != url or
            lab.git(sdk, 'rev-parse', 'HEAD') != lab.LOCK['emsdk_commit'] or
            lab.git(sdk, 'status', '--porcelain', '--untracked-files=no')):
        raise lab.LabError('Existing Emscripten SDK checkout differs from its pin; preserve it')
    rust = lab.LOCK['rust']
    lab.run(['rustup', 'toolchain', 'install', rust, '--profile', 'minimal'])
    lab.run(['rustup', 'target', 'add', '--toolchain', rust, 'wasm32-unknown-emscripten'])
    lab.run(['python3', 'emsdk.py', 'install', version], cwd=sdk)
    lab.run(['python3', 'emsdk.py', 'activate', version], cwd=sdk)
    slang = work / 'flow-tools/slang-2025.6.1'
    slang_binary = slang / 'bin/slangc'
    archive = work / 'flow-tools/slang-2025.6.1.tar.gz'
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(lab.LOCK['flow_slang_archive_url'], archive)
    if lab.sha256(archive) != lab.LOCK['flow_slang_archive_sha256']:
        raise lab.LabError('Slang archive SHA-256 differs from its full pin')
    if not slang_binary.is_file():
        if slang.exists():
            raise lab.LabError('Incomplete existing Slang directory; preserve it')
        slang.mkdir()
        with tarfile.open(archive, 'r:gz') as bundle:
            bundle.extractall(slang, filter='data')
    slang_members = verify_slang_tree(archive, slang)
    env = '# Generated local environment; never an uploaded source file.\n'
    env += 'export RUSTUP_TOOLCHAIN=' + shlex.quote(rust) + '\n'
    env += 'source ' + shlex.quote(str(sdk / 'emsdk_env.sh')) + '\n'
    (work / 'env.sh').write_text(env, encoding='utf-8')
    lab.write_json(root / 'reports/bootstrap.json', {
        'status': 'PINNED_TOOLS_INSTALLED_NOT_RUNTIME_VERIFIED', 'rust': rust,
        'emscripten': version, 'emsdkCommit': lab.LOCK['emsdk_commit'],
        'slangArchiveSha256': lab.sha256(archive), 'slangBinarySha256': lab.sha256(slang_binary),
        'slangMemberInventory': slang_members})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    if not args.install:
        print(json.dumps({'mode': 'PLAN_ONLY', 'rust': lab.LOCK['rust'],
                          'emscripten': lab.LOCK['emscripten'],
                          'slang': lab.LOCK['flow_slang_version']}, indent=2))
        return 0
    try:
        install()
    except (OSError, ValueError, lab.LabError) as exc:
        print('ERROR:', exc)
        return 2
    print('Source work/env.sh, then run python release.py all')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
