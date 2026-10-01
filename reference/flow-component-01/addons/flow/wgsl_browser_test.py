#!/usr/bin/env python3
"""Validate emitted Flow shaders and compute pipelines with Chromium WebGPU."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from serve import create_server


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shader-dir", type=Path, default=ROOT / "dist/flow-wgsl")
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--advection", action="store_true")
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/flow-wgsl-browser.json")
    parser.add_argument("--modules-only", action="store_true")
    parser.add_argument("--mesh-scan", action="store_true")
    parser.add_argument("--exclude-shader", action="append", default=[],
                        help="Exact manifest path to omit; omissions prevent full-corpus acceptance")
    args = parser.parse_args()
    directory = args.shader_dir.resolve()
    files = sorted(directory.rglob("*.wgsl"))
    if not files:
        parser.error("No compiled WGSL shaders found")
    payload = [{"name": p.relative_to(directory).as_posix(),
                "code": p.read_text(encoding="utf-8"),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "work/local-pc/browsers"))
    server = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {"started": datetime.now(timezone.utc).isoformat(), "status": "RUNNING"}
    report["tests"] = []
    def record_result(row):
        report["tests"].append(row)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    manifest_path = directory / "manifest.json"
    if manifest_path.is_file():
        report["manifestSha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = {row["wgsl"]: row["wgslSha256"] for row in manifest["shaders"] if row["status"] == "PASS"}
        actual = {row["name"]: row["sha256"] for row in payload}
        addons = {}
        report["separateAddonManifests"] = {}
        for path in sorted((directory / 'addons').glob('*/manifest.json')):
            addon = json.loads(path.read_text(encoding='utf-8'))
            prefix = path.parent.relative_to(directory).as_posix() + '/'
            rows = addon.get('shaders', [])
            entries = {row['wgsl']: row['wgslSha256'] for row in rows}
            if (not rows or len(entries) != len(rows) or any(not name.startswith(prefix) for name in entries)
                    or any(name in expected or name in addons for name in entries)):
                raise RuntimeError('Separate addon manifest has an invalid shader inventory')
            addons.update(entries)
            report['separateAddonManifests'][path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        if {**expected, **addons} != actual:
            server.shutdown()
            server.server_close()
            raise RuntimeError("WGSL files differ from their build manifest")
        # Preserve the original 97-module/96-selected-pipeline acceptance.
        # Derived operators have their own native numerical acceptance suites.
        payload = [row for row in payload if row['name'] in expected]
        report["corpusStatus"] = manifest["status"]
        report["configuredShaderCount"] = manifest["shaderCount"]
    unknown = set(args.exclude_shader) - {row["name"] for row in payload}
    if unknown:
        server.shutdown()
        server.server_close()
        raise RuntimeError(f"Unknown shader exclusions: {sorted(unknown)}")
    report["excludedShaders"] = args.exclude_shader
    payload = [row for row in payload if row["name"] not in args.exclude_shader]
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            flags = ["--enable-unsafe-webgpu"]
            if not args.hardware:
                flags += ["--use-angle=swiftshader",
                          "--enable-features=Vulkan,WebGPUDeveloperFeatures",
                          "--disable-vulkan-surface"]
            browser = playwright.chromium.launch(headless=True, args=flags,
                executable_path=str(args.browser_executable) if args.browser_executable else None)
            try:
                page = browser.new_page()
                page.expose_function("recordFlowResult", record_result)
                page.on("console", lambda message: print(message.text, flush=True)
                        if message.text.startswith("Flow shader ") else None)
                page.goto(f"http://127.0.0.1:{server.server_port}/web/index.html")
                report.update(page.evaluate("""async ({shaders, modulesOnly}) => {
                    const adapter = await navigator.gpu.requestAdapter();
                    if (!adapter) throw new Error('WebGPU adapter unavailable');
                    const features = ['float32-filterable', 'shader-f16', 'subgroups',
                        'texture-formats-tier1', 'texture-formats-tier2']
                        .filter(name => adapter.features.has(name));
                    const device = await adapter.requestDevice({
                        requiredFeatures: features,
                        requiredLimits: {
                            maxStorageBuffersPerShaderStage: adapter.limits.maxStorageBuffersPerShaderStage,
                            maxStorageTexturesPerShaderStage: adapter.limits.maxStorageTexturesPerShaderStage,
                            maxComputeWorkgroupStorageSize: adapter.limits.maxComputeWorkgroupStorageSize,
                            maxComputeInvocationsPerWorkgroup: adapter.limits.maxComputeInvocationsPerWorkgroup,
                            maxComputeWorkgroupSizeX: adapter.limits.maxComputeWorkgroupSizeX,
                            maxComputeWorkgroupSizeY: adapter.limits.maxComputeWorkgroupSizeY,
                            maxComputeWorkgroupSizeZ: adapter.limits.maxComputeWorkgroupSizeZ,
                        },
                    });
                    const tests = [];
                    try {
                        for (const shader of shaders) {
                            console.log('Flow shader ' + shader.name);
                            device.pushErrorScope('validation');
                            let diagnostic = null;
                            let errors = [];
                            try {
                                const module = device.createShaderModule({code: shader.code, label: shader.name});
                                errors = (await module.getCompilationInfo()).messages
                                    .filter(m => m.type === 'error').map(m => ({
                                        message: m.message, line: m.lineNum, column: m.linePos,
                                    }));
                                if (!errors.length && !modulesOnly) {
                                    let timer;
                                    try {
                                        await Promise.race([
                                            device.createComputePipelineAsync({
                                                layout: 'auto', compute: {module, entryPoint: 'main'},
                                            }),
                                            new Promise((_, reject) => { timer = setTimeout(
                                                () => reject(new Error('Pipeline compilation exceeded 120 seconds')), 120000); }),
                                        ]);
                                    } finally { clearTimeout(timer); }
                                }
                            } catch (error) { diagnostic = String(error); }
                            const validation = await device.popErrorScope();
                            const row = {name: shader.name, sha256: shader.sha256,
                                status: errors.length || diagnostic || validation ? 'FAIL' : 'PASS',
                                errors, diagnostic, validation: validation?.message ?? null};
                            tests.push(row);
                            await window.recordFlowResult(row);
                            if (diagnostic?.includes('exceeded 120 seconds')) break;
                        }
                        return {tests, features, supportedFeatures: [...adapter.features],
                            adapter: {vendor: adapter.info.vendor, architecture: adapter.info.architecture,
                                description: adapter.info.description, device: adapter.info.device},
                            passed: tests.filter(t => t.status === 'PASS').length,
                            failed: tests.filter(t => t.status === 'FAIL').length,
                            untested: shaders.length - tests.length};
                    } finally { device.destroy(); }
                }""", {"shaders": payload, "modulesOnly": args.modules_only}))
                report["browser"] = browser.version
                if args.advection or args.mesh_scan:
                    shader_url = "/" + directory.relative_to(ROOT).as_posix()
                    report["execution"] = page.evaluate("""async ({shaderRoot, advection, meshScan}) => {
                        const factory = (await import('/dist/candidate/physx-pe.mjs')).default;
                        const module = await factory({locateFile: name => '/dist/candidate/' + name});
                        const result = {};
                        if (advection) {
                            const {runAdvectionSmoke} = await import('/addons/flow/advection_smoke.mjs');
                            result.advection = await runAdvectionSmoke(module, shaderRoot);
                        }
                        if (meshScan) {
                            const {runMeshScanSmoke} = await import('/addons/flow/mesh_scan_smoke.mjs');
                            result.meshScan = await runMeshScanSmoke(module, shaderRoot);
                        }
                        return result;
                    }""", {"shaderRoot": shader_url, "advection": args.advection, "meshScan": args.mesh_scan})
                    report["candidateArtifacts"] = {name: hashlib.sha256(
                        (ROOT / "dist/candidate" / name).read_bytes()).hexdigest()
                        for name in ("physx-pe.mjs", "physx-pe.wasm")}
                    report["advectionSourceSha256"] = hashlib.sha256(
                        (ROOT / "addons/flow/advection_smoke.mjs").read_bytes()).hexdigest()
                    report["meshScanSourceSha256"] = hashlib.sha256(
                        (ROOT / "addons/flow/mesh_scan_smoke.mjs").read_bytes()).hexdigest()
                success = "FLOW_WGSL_MODULES_PASSED" if args.modules_only else "FLOW_WGSL_PIPELINES_PASSED"
                report["status"] = success if report["failed"] == 0 else "FAILED"
                if report["status"] == success and args.exclude_shader:
                    report["status"] = "SELECTED_FLOW_WGSL_CHECKS_PASSED_INCOMPLETE_CORPUS"
            finally:
                browser.close()
    except Exception as error:
        report.update(status="BLOCKED_OR_FAILED", error=str(error))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        report["finished"] = datetime.now(timezone.utc).isoformat()
        path = args.report
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "tests"}, indent=2))
    return 0 if report["status"] in ("FLOW_WGSL_PIPELINES_PASSED", "FLOW_WGSL_MODULES_PASSED",
                                    "SELECTED_FLOW_WGSL_CHECKS_PASSED_INCOMPLETE_CORPUS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
