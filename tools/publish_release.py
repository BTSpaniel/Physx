# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Publish this private prerelease only from a verified tag-build artifact."""
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
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "PhysX-PE-private-release",
        "Content-Type": content_type,
    })
    with urllib.request.build_opener(NoRedirect).open(operation, timeout=120) as response:
        return json.load(response)


def checked_payload(directory: Path, tag: str, expected_commit: str | None = None) -> tuple[dict, list[Path]]:
    metadata_path = directory / "release-artifacts.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    version = metadata["version"]
    if (metadata.get("schema") != "physx-pe.release-artifacts/v1" or
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
    if name != f"physx-pe-{version}.zip":
        raise ValueError("Unexpected release archive name")
    archive = directory / name
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if archive.stat().st_size != entry["bytes"] or digest != entry["sha256"]:
        raise ValueError("Release archive differs from the verified artifact")
    checksum = archive.with_suffix(".zip.sha256")
    if checksum.read_text(encoding="utf-8").strip() != digest + "  " + name:
        raise ValueError("Release checksum sidecar disagrees")
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Inner archive has duplicate file names")
        manifest = json.loads(bundle.read("runtime-manifest.json"))
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
            content = bundle.read(relative)
            if len(content) != expected["bytes"] or hashlib.sha256(content).hexdigest() != expected["sha256"]:
                raise ValueError("Inner archive integrity failure: " + relative)
    return metadata, [archive, checksum, metadata_path]


def publish(directory: Path, tag: str) -> None:
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("Publishing is restricted to the configured private repository")
    commit = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A source commit SHA is required")
    metadata, files = checked_payload(directory, tag, expected_commit=commit)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GitHub Actions token is missing")
    base = f"https://api.github.com/repos/{REPOSITORY}"
    if request(base, token).get("private") is not True:
        raise ValueError("Repository must stay private for this release")
    releases = request(base + "/releases?per_page=100", token)
    matches = [release for release in releases if release["tag_name"] == tag]
    if len(matches) > 1:
        raise ValueError("Ambiguous existing release")
    title = "PhysX PE " + metadata["version"]
    body = ("Browser physics alpha built from pinned NVIDIA PhysX 5.11.0, Blast 5.0.6, "
            "Flow, fabmax-derived bindings and custom Rust SIMD/WASM bridges.\n\n"
            "Download the ZIP for the complete matched runtime. The WASM is at "
            "`dist/candidate/physx-pe.wasm`; its loader and declarations are beside it. "
            "Flow shaders and adapters, full licenses and verification receipts are included.\n\n"
            "Original PhysX PE additions are MIT. Upstream components retain their licenses "
            "and credits. This is an attributed integration, not a clean-room SDK rewrite.\n\n"
            "Verification covers functional browser/ABI checks and an extracted-archive "
            "test using software WebGPU in CI. No real-time hardware performance or full "
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
    assets = request(base + f"/releases/{release['id']}/assets", token)
    by_name = {asset["name"]: asset for asset in assets}
    for path in files:
        data = path.read_bytes()
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if path.name in by_name:
            asset = by_name[path.name]
        else:
            if not release["draft"]:
                raise ValueError("Published release is missing an asset; refusing to mutate it")
            query = urllib.parse.urlencode({"name": path.name, "label": digest})
            asset = request(upload_url + "?" + query, token, method="POST", data=data,
                            content_type="application/zip" if path.suffix == ".zip" else "application/octet-stream")
        if asset.get("size") != len(data) or asset.get("digest") != digest:
            raise ValueError("Uploaded release asset digest disagrees: " + path.name)
    if release["draft"]:
        release = request(base + f"/releases/{release['id']}", token, method="PATCH",
                          data=json.dumps({"draft": False, "prerelease": True}).encode())
    print("Private verified prerelease: " + release["html_url"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--check", action="store_true", help="Validate artifacts without accessing GitHub")
    args = parser.parse_args()
    try:
        if args.check:
            checked_payload(args.directory, args.tag)
            print("Verified release payload")
        else:
            publish(args.directory, args.tag)
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print("Release refused: " + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
