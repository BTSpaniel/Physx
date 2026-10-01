# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Compile the admitted Flow source component; no final unified release claim."""
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import physx_lab as lab
from prepare_sources import prepare_flow_sources
from flow_source_evidence import shader_inventory, native_capabilities
from release import inventory

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--report', type=Path, required=True)
args = parser.parse_args()
report_path = args.report.resolve()
if report_path.exists() or not report_path.is_relative_to((ROOT / 'reports').resolve()):
    raise SystemExit('Preserve previous receipts; choose a fresh contained reports path')
before = inventory(ROOT)
started = datetime.now(timezone.utc).isoformat()
prepared = prepare_flow_sources()
shaders = shader_inventory(ROOT)
command = [sys.executable, str(ROOT / 'addons/flow/build_host.py'), '--object']
subprocess.run(command, check=True)
host_path = ROOT / 'dist/flow-host/build-manifest.json'
host = json.loads(host_path.read_text(encoding='utf-8'))
capabilities = native_capabilities(host)
if host.get('allTranslationUnitsRecompiled') is not True or host.get('generatedHostHeaderCount') != 124 or host.get('generatedIncludeWrapperCount') != 6:
    raise lab.LabError('Flow native build did not compile the complete admitted source/header closure')
obj = ROOT / 'work/pr_flow_host.o'
lab.verify_wasm(obj)
after = inventory(ROOT)
if before != after or shader_inventory(ROOT) != shaders:
    raise lab.LabError('Source/shader closure changed during Flow native compilation')
phase = ROOT / 'dist' / ('flow-component-' + report_path.stem)
if phase.exists():
    raise lab.LabError('Preserve the previous component outputs; use a fresh report name')
phase.mkdir(parents=True)
for name in ('flow-host.mjs', 'flow-host.wasm', 'build-manifest.json'):
    shutil.copyfile(host_path.parent / name, phase / name)
shutil.copyfile(obj, phase / 'pr_flow_host.o')
artifact = lambda p: {'path': p.relative_to(ROOT).as_posix(), 'bytes': p.stat().st_size, 'sha256': lab.sha256(p)}
receipt = {'schema': 'physx-pe.flow-source-build/v1', 'status': 'COMPILED_NOT_RUNTIME_VERIFIED',
           'startedUtc': started, 'finishedUtc': datetime.now(timezone.utc).isoformat(),
           'sourceHashesBefore': before, 'sourceHashesAfter': after,
           'upstreamPreparation': prepared, 'shaderInventory': shaders,
           'capabilities': capabilities, 'compiler': host['compiler'],
           'command': command, 'compileCommands': host['commands'],
           'allTranslationUnitsRecompiled': True,
           'generatedHostHeaderCount': 124, 'generatedIncludeWrapperCount': 6,
           'compiledTranslationUnits': len(host['compilations']),
           'hostBuildManifest': artifact(phase / 'build-manifest.json'),
           'artifacts': {name: artifact(phase / name) for name in ('flow-host.mjs', 'flow-host.wasm', 'pr_flow_host.o')},
           'scope': 'Source-built Flow only. No final PhysX/Blast/thermal pair, GPU behavior, numerical acceptance, publication or realtime claim.'}
lab.write_json(report_path, receipt)
print(json.dumps({'status': receipt['status'], 'report': str(report_path), 'translationUnits': receipt['compiledTranslationUnits'],
                  'headers': 124, 'kernels': 124, 'corpusFiles': len(shaders)}, indent=2))
