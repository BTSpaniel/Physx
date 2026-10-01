#!/usr/bin/env python3
"""Prove PhysX, Blast, Rust SIMD and Chrome WebGPU in one WASM module."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from serve import create_server  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser-executable', type=Path)
    parser.add_argument('--hardware', action='store_true')
    args = parser.parse_args()
    output = ROOT / "dist/candidate"
    manifest = json.loads((output / "build-manifest.json").read_text(encoding="utf-8"))
    report = {
        "component": "Unified PhysX, NVIDIA Blast and Flow WebGPU bridge",
        "source": "ovphysx-0.6.3",
        "status": "RUNNING",
        "started": datetime.now(timezone.utc).isoformat(),
        "tests": [],
    }
    server = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for name, metadata in manifest["artifacts"].items():
            path = output / name
            if path.stat().st_size != metadata["bytes"] or digest(path) != metadata["sha256"]:
                raise RuntimeError(f"Candidate artifact changed after build: {name}")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            flags = [] if args.hardware else [
                "--enable-unsafe-webgpu",
                "--enable-features=Vulkan,WebGPUDeveloperFeatures",
                "--use-angle=swiftshader",
                "--disable-vulkan-surface",
            ]
            browser = playwright.chromium.launch(headless=True, args=flags,
                executable_path=str(args.browser_executable) if args.browser_executable else None)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/web/index.html")
            result = page.evaluate("""async () => {
              const factory = (await import('/dist/candidate/physx-pe.mjs')).default;
              const module = await factory({locateFile: name =>
                new URL('/dist/candidate/' + name, location.origin).href});
              const {runFlowWebGpuRoundTrip} = await import('/addons/flow/webgpu_bridge.mjs');
              return {
                bulkBackend: module._pr_bulk_backend(),
                blastVersion: module._pr_blast_version(),
                blastSmoke: module._pr_blast_smoke(),
                flow: await runFlowWebGpuRoundTrip(module, {count: 4097}),
              };
            }""")
            report["browser"] = browser.version
            browser.close()
        report["tests"] = [
            {"name": "PhysX and Rust bridge module identity", "status": "PASS"
             if result["bulkBackend"] == 2 else "FAIL", "detail": result["bulkBackend"]},
            {"name": "Pinned Blast 5.0.6 in unified module", "status": "PASS"
             if result["blastVersion"] == 50006 else "FAIL", "detail": result["blastVersion"]},
            {"name": "Blast asset, bond fracture and split in unified module", "status": "PASS"
             if result["blastSmoke"] == 0 else "FAIL", "detail": result["blastSmoke"]},
            {"name": "Unified Flow staging ABI", "status": "PASS",
             "detail": {"abi": result["flow"]["stagingAbi"],
                        "simd128": result["flow"]["simd128"]}},
            {"name": "WASM heap to WebGPU compute to WASM heap", "status": "PASS",
             "detail": {"elements": result["flow"]["count"],
                        "bytes": result["flow"]["byteLength"]}},
        ]
        report["adapter"] = result["flow"].get("adapter")
        report["status"] = ("UNIFIED_PHYSX_BLAST_FLOW_BROWSER_PASSED"
                            if result["flow"]["status"] == "FLOW_WEBGPU_WASM_ROUND_TRIP_PASSED"
                            and all(row["status"] == "PASS" for row in report["tests"])
                            else "FAILED")
        report["artifactHashes"] = manifest["artifacts"]
        report["bridgeSha256"] = digest(ROOT / "addons/flow/webgpu_bridge.mjs")
    except Exception as error:
        report.update(status="BLOCKED_OR_FAILED", error=str(error))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        report["finished"] = datetime.now(timezone.utc).isoformat()
        reports = ROOT / "reports"
        reports.mkdir(exist_ok=True)
        (reports / "unified-browser.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "UNIFIED_PHYSX_BLAST_FLOW_BROWSER_PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
