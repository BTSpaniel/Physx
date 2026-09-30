# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Publish immutable verified runtime assets with explicit repository visibility."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPOSITORY = "BTSpaniel/Physx"
ASSET_CONTENT_TYPES = {'.wasm': 'application/wasm', '.mjs': 'text/javascript',
                       '.ts': 'text/plain', '.sha256': 'text/plain', '': 'text/plain',
                       '.json': 'application/json', '.zip': 'application/zip'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, url):
        return None


def request(url: str, token: str, *, method: str = "GET", data: bytes | None = None,
            content_type: str = "application/json") -> dict | list:
    destination = urllib.parse.urlsplit(url)
    if (destination.scheme != "https" or destination.username is not None or
            destination.password is not None or destination.port not in (None, 443) or
            destination.hostname not in {"api.github.com", "uploads.github.com"}):
        raise ValueError("Unexpected GitHub API host")
    operation = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "PhysX-PE-release",
        "Content-Type": content_type,
    })
    with urllib.request.build_opener(NoRedirect).open(operation, timeout=120) as response:
        return json.load(response)


def ordinary_asset(directory: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name or '/' in name or '\\' in name:
        raise ValueError("Invalid release asset name")
    path = directory / name
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Release asset must be an ordinary owned file: " + name)
    return path


def checked_payload(directory: Path, tag: str, expected_commit: str | None = None) -> tuple[dict, list[Path]]:
    metadata_path = ordinary_asset(directory, "release-artifacts.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    version = metadata["version"]
    schema = metadata.get("schema")
    if (schema not in {"physx-pe.release-artifacts/v1", "physx-pe.release-artifacts/v2"} or
            metadata.get("status") != "VERIFIED_ALPHA_ARCHIVE" or
            not re.fullmatch(r"5\.11\.0-alpha\.[1-9][0-9]*", version) or tag != "v" + version):
        raise ValueError("Tag and verified alpha archive identity disagree")
    revision = metadata["sourceRevision"]
    if (set(revision) != {"commit", "tree", "inventorySha256"} or
            not re.fullmatch(r"[0-9a-f]{40}", revision["commit"]) or
            not re.fullmatch(r"[0-9a-f]{40}", revision["tree"]) or
            not re.fullmatch(r"[0-9a-f]{64}", revision["inventorySha256"]) or
            (expected_commit is not None and revision["commit"] != expected_commit)):
        raise ValueError("Artifact does not belong to the expected source revision")
    entry = metadata["archive"]
    name = entry["name"]
    expected_name = f"physx-pe-{version}" + ("-runtime.zip" if schema.endswith('/v2') else ".zip")
    if name != expected_name:
        raise ValueError("Unexpected release archive name")
    archive = ordinary_asset(directory, name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if archive.stat().st_size != entry["bytes"] or digest != entry["sha256"]:
        raise ValueError("Release archive differs from the verified artifact")
    checksum = ordinary_asset(directory, archive.name + ".sha256")
    if checksum.read_text(encoding="utf-8").strip() != digest + "  " + name:
        raise ValueError("Release checksum sidecar disagrees")
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Inner archive has duplicate file names")
        manifest_bytes = bundle.read("runtime-manifest.json")
        manifest = json.loads(manifest_bytes)
        verification = json.loads(bundle.read("reports/verification.json"))
        if (manifest.get("version") != version or manifest.get("sdkVersion") != "5.11.0" or
                manifest.get("sourceRevision") != revision or
                verification.get("sourceRevision") != revision or
                verification.get("sourceInventorySha256") != revision["inventorySha256"] or
                verification.get("status") != "BUILD_AND_BROWSER_SMOKE_PASSED_ALPHA"):
            raise ValueError("Inner archive version disagrees")
        if set(bundle.namelist()) != set(manifest["files"]) | {"runtime-manifest.json"}:
            raise ValueError("Inner archive file inventory disagrees")
        for relative, expected in manifest["files"].items():
            if (relative.startswith('/') or '\\' in relative or '..' in Path(relative).parts or
                    Path(relative).as_posix() != relative):
                raise ValueError("Invalid inner archive path: " + relative)
            content = bundle.read(relative)
            if len(content) != expected["bytes"] or hashlib.sha256(content).hexdigest() != expected["sha256"]:
                raise ValueError("Inner archive integrity failure: " + relative)
        files = [archive, checksum, metadata_path]
        if schema.endswith('/v2'):
            if metadata.get('runtimeManifest') != {'archivePath': 'runtime-manifest.json',
                    'bytes': len(manifest_bytes), 'sha256': hashlib.sha256(manifest_bytes).hexdigest()}:
                raise ValueError("Release runtime manifest binding disagrees")
            direct = metadata['directAssets']
            if set(direct) != {'physx-pe.wasm', 'physx-pe.mjs', 'physx-pe.d.ts'}:
                raise ValueError("Direct release assets must contain the matched WASM, loader and declarations")
            sums = {archive.name: digest}
            for name, entry in sorted(direct.items()):
                relative = 'dist/candidate/' + name
                if entry.get('archivePath') != relative or {key: entry.get(key) for key in ('bytes', 'sha256')} != manifest['files'][relative]:
                    raise ValueError("Direct asset identity disagrees with the complete runtime: " + name)
                path = ordinary_asset(directory, name)
                data = path.read_bytes()
                if data != bundle.read(relative):
                    raise ValueError("Direct asset differs from the verified runtime: " + name)
                sums[name] = entry['sha256']
                files.append(path)
            entry = metadata['checksums']
            if entry.get('name') != 'SHA256SUMS':
                raise ValueError("Unexpected release checksums file")
            checksums = ordinary_asset(directory, 'SHA256SUMS')
            content = checksums.read_bytes()
            expected_sums = ''.join(sums[name] + '  ' + name + '\n' for name in sorted(sums)).encode()
            if (content != expected_sums or len(content) != entry['bytes'] or
                    hashlib.sha256(content).hexdigest() != entry['sha256']):
                raise ValueError("Release SHA256SUMS disagrees with its matched runtime assets")
            files.append(checksums)
    return metadata, files


def paginated(url: str, token: str) -> list[dict]:
    """Read the whole bounded GitHub inventory, including releases beyond page one."""
    rows = []
    for page in range(1, 1001):
        result = request(url + f'?per_page=100&page={page}', token)
        if not isinstance(result, list):
            raise ValueError("Unexpected GitHub inventory response")
        rows.extend(result)
        if len(result) < 100:
            return rows
    raise ValueError("GitHub inventory exceeded the bounded pagination limit")


def check_remote_asset(asset: dict, name: str, data: bytes) -> None:
    if (asset.get('name') != name or asset.get('state') != 'uploaded' or
            asset.get('size') != len(data) or
            asset.get('digest') != 'sha256:' + hashlib.sha256(data).hexdigest()):
        raise ValueError("Release asset digest disagrees: " + name)


def publish(directory: Path, tag: str, visibility: str = 'private') -> None:
    if visibility not in ('private', 'public'):
        raise ValueError("Repository visibility must be private or public")
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("Publishing is restricted to the configured repository")
    commit = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A source commit SHA is required")
    metadata, files = checked_payload(directory, tag, expected_commit=commit)
    payload = {path.name: path.read_bytes() for path in files}
    # Freeze admitted bytes before any GitHub mutation; a local edit cannot alter an upload.
    if json.loads(payload['release-artifacts.json']) != metadata:
        raise ValueError("Release metadata changed after admission")
    identities = {metadata['archive']['name']: metadata['archive'], **metadata.get('directAssets', {})}
    if 'checksums' in metadata:
        identities['SHA256SUMS'] = metadata['checksums']
    for name, entry in identities.items():
        if len(payload[name]) != entry['bytes'] or hashlib.sha256(payload[name]).hexdigest() != entry['sha256']:
            raise ValueError("Release asset changed after admission: " + name)
    archive_name = metadata['archive']['name']
    if payload[archive_name + '.sha256'] != (metadata['archive']['sha256'] + '  ' + archive_name + '\n').encode():
        raise ValueError("Release archive checksum changed after admission")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GitHub Actions token is missing")
    base = f"https://api.github.com/repos/{REPOSITORY}"
    if request(base, token).get("private") is not (visibility == 'private'):
        raise ValueError("Repository visibility does not match explicit publication mode: " + visibility)
    releases = paginated(base + "/releases", token)
    matches = [release for release in releases if release["tag_name"] == tag]
    if len(matches) > 1:
        raise ValueError("Ambiguous existing release")
    title = "PhysX PE " + metadata["version"]
    downloads = ("Download `physx-pe.wasm` with its adjacent `physx-pe.mjs` loader and "
            "`physx-pe.d.ts` declarations, or download the complete `-runtime.zip`. "
            "`SHA256SUMS` binds the direct assets and archive. " if metadata['schema'].endswith('/v2') else
            "Download the ZIP for the complete matched runtime. ")
    body = ("Browser physics alpha built from pinned NVIDIA PhysX 5.11.0, Blast 5.0.6, "
            "Flow, fabmax-derived bindings and custom Rust SIMD/WASM bridges.\n\n"
            + downloads + "Inside the complete ZIP the WASM is at "
            "`dist/candidate/physx-pe.wasm`; its loader and declarations are beside it. "
            "Flow shaders and adapters, full licenses and verification receipts are included.\n\n"
            "Original PhysX PE additions are MIT. Upstream components retain their licenses "
            "and credits. This is an attributed integration, not a clean-room SDK rewrite.\n\n"
            "Verification covers functional browser/ABI checks, actual Flow compute and "
            "an extracted-archive test in CI. Chromium requests SwiftShader; Firefox Flow "
            "requests software Vulkan and reports redacted adapter identity as unknown. "
            "No real-time hardware performance or full "
            "upstream feature parity is claimed.\n\nSource commit: `" + commit + "`.\n")
    if matches:
        release = matches[0]
        if (release["target_commitish"] != commit or release["name"] != title or
                release.get("prerelease") is not True):
            raise ValueError("Existing release belongs to a different source identity")
    else:
        release = request(base + "/releases", token, method="POST", data=json.dumps({
            "tag_name": tag, "target_commitish": commit, "name": title, "body": body,
            "draft": True, "prerelease": True,
        }).encode())
    upload_url = release["upload_url"].split("{", 1)[0]
    assets = paginated(base + f"/releases/{release['id']}/assets", token)
    by_name = {asset["name"]: asset for asset in assets}
    if len(by_name) != len(assets) or set(by_name) - set(payload):
        raise ValueError("Existing release has ambiguous or unexpected assets")
    # Preflight every existing asset before uploading even one missing draft asset.
    for name, asset in by_name.items():
        check_remote_asset(asset, name, payload[name])
    if not release['draft'] and set(by_name) != set(payload):
        raise ValueError("Published release is missing an asset; refusing to mutate it")
    for name, data in payload.items():
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if name in by_name:
            asset = by_name[name]
        else:
            if not release["draft"]:
                raise ValueError("Published release is missing an asset; refusing to mutate it")
            query = urllib.parse.urlencode({"name": name, "label": digest})
            asset = request(upload_url + "?" + query, token, method="POST", data=data,
                            content_type=ASSET_CONTENT_TYPES.get(Path(name).suffix, 'application/octet-stream'))
        check_remote_asset(asset, name, data)
    if release["draft"]:
        release = request(base + f"/releases/{release['id']}", token, method="PATCH",
                          data=json.dumps({"draft": False, "prerelease": True}).encode())
    print(visibility.capitalize() + " verified prerelease: " + release["html_url"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--visibility", choices=['private', 'public'], default='private',
                        help='Require this explicit repository visibility; never changes repository settings')
    parser.add_argument("--check", action="store_true", help="Validate artifacts without accessing GitHub")
    args = parser.parse_args()
    try:
        if args.check:
            checked_payload(args.directory, args.tag)
            print("Verified release payload")
        else:
            publish(args.directory, args.tag, args.visibility)
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print("Release refused: " + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
