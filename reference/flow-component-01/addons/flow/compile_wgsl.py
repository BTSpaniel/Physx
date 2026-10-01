#!/usr/bin/env python3
"""Compile the pinned NVIDIA Flow compute shader corpus to WebGPU WGSL."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FLOW_ROOT = ROOT / "work/candidate/PhysX/flow"
DEFAULT_SLANGC = ROOT / "work/flow-tools/slang-2025.6.1/bin/slangc"
DEFAULT_OUTPUT = ROOT / "dist/flow-wgsl"
DEFAULT_REPORT = ROOT / "reports/flow-wgsl-compile.json"
EXPECTED_SLANG_VERSION = "2025.6.1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def shader_sources(flow_root: Path) -> list[Path]:
    sources = set()
    for project in flow_root.glob("source/*/*.nfproj"):
        for name in re.findall(r'computeShader\("([^"]+)"\)', project.read_text(encoding="utf-8")):
            source = (project.parent / name).resolve()
            if not source.is_relative_to(flow_root.resolve()) or not source.is_file():
                raise RuntimeError(f"Invalid shader project entry: {project}: {name}")
            sources.add(source)
    return sorted(sources, key=lambda path: path.as_posix())


def include_paths(flow_root: Path, sources: list[Path]) -> list[Path]:
    paths = {
        flow_root / "include/nvflow",
        flow_root / "include/nvflow/shaders",
        flow_root / "include/nvflow/nanovdb",
        flow_root / "include/nvflowext",
        flow_root / "include/nvflowext/shaders",
        *(source.parent for source in sources),
    }
    return sorted((path.resolve() for path in paths), key=lambda path: path.as_posix())


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=False)


def narrow_storage_access(code: str) -> tuple[str, list[str]]:
    """Narrow only globals whose every use is a store or dimensions query."""
    declarations = re.compile(
        r"var\s+(\w+)\s*:\s*texture_storage_\w+<\w+,\s*read_write>")
    narrowed = []
    for match in list(declarations.finditer(code)):
        name = match.group(1)
        uses = list(re.finditer(r"\b" + re.escape(name) + r"\b", code))
        if len(uses) < 2:
            continue
        if all(use.start() == match.start(1) or re.search(
                r"texture(?:Store|Dimensions)\s*\(\s*\(*\s*$", code[:use.start()])
               for use in uses):
            narrowed.append(name)
    for name in narrowed:
        code = re.sub(r"(var\s+" + re.escape(name) +
                      r"\s*:\s*texture_storage_\w+<\w+,\s*)read_write>",
                      r"\g<1>write>", code)
    return code, narrowed


def license_header(source: str) -> str:
    """Retain only the initial comment, never HLSL declarations or directives."""
    lines = []
    for line in source.splitlines():
        line = line.lstrip("\ufeff")
        if line.strip() and not line.lstrip().startswith("//"):
            break
        lines.append(line)
    return "\n".join(lines) + "\n"


def uniform_mesh_scan(code: str) -> str:
    # The upstream blockScan synchronizes sdata1 before every lane reads these
    # four fixed addresses. Make that group-uniform value explicit to WGSL.
    old = "sdata1_0[i32(63)] + sdata1_0[i32(127)] + sdata1_0[i32(191)] + sdata1_0[i32(255)]"
    if code.count(old) != 1:
        raise RuntimeError("Pinned EmitterMeshClosest scan-total layout changed")
    new = " + ".join(f"workgroupUniformLoad(&sdata1_0[i32({index})])"
                     for index in (63, 127, 191, 255))
    return code.replace(old, new)


def git_commit(flow_root: Path) -> str:
    result = run(["git", "-C", str(flow_root), "rev-parse", "HEAD"])
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def compile_corpus(flow_root: Path, slangc: Path, output: Path) -> dict:
    output = output.resolve()
    dist_root = (ROOT / "dist").resolve()
    if output == dist_root or not output.is_relative_to(dist_root):
        raise RuntimeError("Shader output must be a child directory of workbench dist")
    if output.exists():
        old_manifest = output / "manifest.json"
        if not old_manifest.is_file() or json.loads(old_manifest.read_text(
                encoding="utf-8")).get("component") != "NVIDIA Flow WGSL shader corpus":
            raise RuntimeError("Refusing to replace a directory not owned by the Flow shader build")
    started = datetime.now(timezone.utc).isoformat()
    sources = shader_sources(flow_root)
    if not sources:
        raise RuntimeError(f"No Flow HLSL shaders found under {flow_root}")
    if not slangc.is_file():
        raise RuntimeError(f"Pinned slangc is missing: {slangc}")

    version_result = run([str(slangc), "-version"])
    version = version_result.stdout.strip()
    if version_result.returncode or version != EXPECTED_SLANG_VERSION:
        raise RuntimeError(f"Expected Slang {EXPECTED_SLANG_VERSION}, got: {version!r}")

    includes = include_paths(flow_root, sources)
    references = set()
    for cpp in flow_root.glob("source/**/*.cpp"):
        references.update(re.findall(r'#include "shaders/([^"]+)\.h"', cpp.read_text(encoding="utf-8")))
    missing = [str(path) for path in includes if not path.is_dir()]
    if missing:
        raise RuntimeError(f"Flow include directories are missing: {missing}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="flow-wgsl-", dir=output.parent))
    snapshot = tempfile.TemporaryDirectory(prefix="pr-flow-inputs-")
    snapshot_root = Path(snapshot.name)
    for name in ("include", "source"):
        shutil.copytree(flow_root / name, snapshot_root / name)
    input_hashes = {path.relative_to(snapshot_root).as_posix(): sha256(path)
                    for path in sorted(snapshot_root.rglob("*"))
                    if path.is_file() and path.suffix in (".h", ".hlsli", ".hlsl", ".nfproj")}
    rows: list[dict] = []
    failures: list[dict] = []
    try:
        for source in sources:
            relative = source.relative_to(flow_root)
            shader_output = temp_root / relative.with_suffix(".wgsl")
            reflection_output = shader_output.with_suffix(".reflection.json")
            shader_output.parent.mkdir(parents=True, exist_ok=True)
            command = [
                str(slangc), str(snapshot_root / relative),
                "-lang", "hlsl",
                "-target", "wgsl",
                "-entry", "main",
                "-stage", "compute",
                "-matrix-layout-column-major",
                "-preserve-params",
                "-reflection-json", str(reflection_output),
            ]
            for include in includes:
                command.extend(["-I", str(snapshot_root / include.relative_to(flow_root))])
            command.extend(["-o", str(shader_output)])
            result = run(command)
            row = {
                "source": relative.as_posix(),
                "sourceSha256": input_hashes[relative.as_posix()],
                "referencedByRuntime": source.name in references,
                "status": "PASS" if result.returncode == 0 else "FAIL",
            }
            if result.returncode == 0:
                raw = shader_output.read_text(encoding="utf-8")
                normalized, narrowed = narrow_storage_access(raw)
                if source.name == "EmitterMeshClosestCS.hlsl":
                    normalized = uniform_mesh_scan(normalized)
                notice = license_header((snapshot_root / relative).read_text(encoding="utf-8"))
                normalized = notice + "// Generated by pinned Slang; see manifest.json.\n" + normalized
                shader_output.write_text(normalized, encoding="utf-8")
                row.update({
                    "storageAccessNarrowed": narrowed,
                    "meshScanUniformLoads": source.name == "EmitterMeshClosestCS.hlsl",
                    "rawWgslSha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                    "wgsl": shader_output.relative_to(temp_root).as_posix(),
                    "wgslBytes": shader_output.stat().st_size,
                    "wgslSha256": sha256(shader_output),
                    "reflection": reflection_output.relative_to(temp_root).as_posix(),
                    "reflectionSha256": sha256(reflection_output),
                })
            else:
                row["diagnostics"] = result.stdout.strip()
                failures.append(row)
            rows.append(row)
            print(f"{row['status']}: {relative}", flush=True)

        manifest = {
            "schema": 1,
            "component": "NVIDIA Flow WGSL shader corpus",
            "source": "NVIDIA-Omniverse/PhysX flow at ovphysx-0.6.3",
            "sourceCommit": git_commit(flow_root),
            "inputHashes": input_hashes,
            "excludedUnconfiguredSources": [
                path.relative_to(flow_root).as_posix()
                for path in sorted(flow_root.glob("source/*/shaders/*.hlsl"))
                if path.resolve() not in sources
            ],
            "slangVersion": version,
            "slangSha256": sha256(slangc),
            "target": "wgsl",
            "matrixLayout": "column-major",
            "shaderCount": len(rows),
            "passed": len(rows) - len(failures),
            "failed": len(failures),
            "runtimeShaderCount": sum(row["referencedByRuntime"] for row in rows),
            "runtimeFailed": sum(row["referencedByRuntime"] for row in failures),
            "status": "FLOW_WGSL_CORPUS_COMPILED" if not failures else "FAILED",
            "started": started,
            "finished": datetime.now(timezone.utc).isoformat(),
            "shaders": rows,
        }
        (temp_root / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            if not output.is_relative_to(ROOT / "dist"):
                raise RuntimeError("Refusing to replace output outside the workbench dist directory")
            shutil.rmtree(output)
        temp_root.replace(output)
        temp_root = None
        return manifest
    finally:
        snapshot.cleanup()
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flow-root", type=Path, default=DEFAULT_FLOW_ROOT)
    parser.add_argument("--slangc", type=Path, default=DEFAULT_SLANGC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = compile_corpus(args.flow_root.resolve(), args.slangc.resolve(),
                                args.output.resolve())
    except Exception as error:
        report = {
            "component": "NVIDIA Flow WGSL shader corpus",
            "status": "BLOCKED_OR_FAILED",
            "error": str(error),
            "finished": datetime.now(timezone.utc).isoformat(),
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "FLOW_WGSL_CORPUS_COMPILED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
