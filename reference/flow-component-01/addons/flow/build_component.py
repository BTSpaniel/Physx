# SPDX-License-Identifier: MIT
"""Build an isolated Flow partial object with its complete compiler/runtime closure."""
from pathlib import Path
import json
import subprocess
import sys
from component_evidence import BASE_MANIFEST_SHA256, CAPABILITIES, sha, input_inventory, shader_inventory, validate_component

ROOT = Path(__file__).resolve().parents[2]
output = ROOT / 'dist/flow-component/manifest.json'
if output.exists():
    raise SystemExit('Refuse to replace an existing component manifest')
baseline_path = ROOT / 'baseline/flow-host-build-manifest.json'
assert sha(baseline_path) == BASE_MANIFEST_SHA256
baseline = json.loads(baseline_path.read_text())
for name, digest in baseline['sourceHashes'].items():
    if not name.startswith(('work/flow-host-headers/', 'work/flow-rebase-sources/')):
        assert sha(ROOT / name) == digest, 'Proven native input changed: ' + name
initial = {p.relative_to(ROOT).as_posix(): sha(p) for folder in ('addons/flow', 'work/candidate/PhysX/flow', 'dist/flow-wgsl', 'baseline')
           for p in (ROOT / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
command = [sys.executable, str(ROOT / 'addons/flow/build_host.py'), '--object']
subprocess.run(command, check=True)
assert all(sha(ROOT / name) == digest for name, digest in initial.items()), 'Inputs changed during component build'
host_path = ROOT / 'dist/flow-host/build-manifest.json'
host = json.loads(host_path.read_text())
obj = ROOT / 'work/pr_flow_host.o'
assert obj.is_file() and obj.stat().st_size > 8
record = lambda p: {'path': p.relative_to(ROOT).as_posix(), 'sha256': sha(p), 'size': p.stat().st_size}
manifest = {'schema': 'flow-component-build-v1', 'status': 'COMPILED_NOT_RUNTIME_TESTED',
            'capabilities': CAPABILITIES, 'compiler': host['compiler'], 'command': command,
            'hostBuildManifest': record(host_path), 'object': record(obj),
            'inputs': input_inventory(ROOT, host), 'shaderInventory': shader_inventory(ROOT),
            'baselineManifestSha256': BASE_MANIFEST_SHA256,
            'scope': 'Linkable Flow component only. Final unified runtime not built or admitted.'}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(manifest, indent=2) + '\n')
validate_component(ROOT)
print(json.dumps({'componentManifest': str(output), 'manifestSha256': sha(output), 'object': manifest['object'],
                  'inputCount': len(manifest['inputs'])}, indent=2))
