# SPDX-License-Identifier: MIT
"""Byte-level Flow component/link admission; never a substitute for native tests."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath

BASE_MANIFEST_SHA256 = 'd617a2307811593ad84d5491461475c07f29c087629c42e4612d8fadb3570f82'
CAPABILITIES = dict.fromkeys(('flowHostAbi', 'flowSolidBoundaryAbi', 'flowScalarSourceAbi',
                             'flowRebaseAbi', 'flowMomentumExchangeAbi'), 1)
CORPUS_COUNTS = {'manifest.json': 97, 'addons/solid/manifest.json': 21,
                 'addons/scalar/manifest.json': 4, 'addons/momentum/manifest.json': 2}

def require(condition, message):
    if not condition:
        raise ValueError(message)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def local_path(value):
    require(isinstance(value, str) and value, 'Missing native path')
    if os.name == 'nt' and value.startswith('/mnt/') and len(value) > 7 and value[6] == '/':
        return Path(value[5].upper() + ':' + value[6:])
    return Path(value)

def member(root, name):
    require(isinstance(name, str) and '\\' not in name and ':' not in name, 'Invalid closure member')
    rel = PurePosixPath(name)
    require(not rel.is_absolute() and '..' not in rel.parts and name == rel.as_posix(), 'Closure path escape')
    path = root.joinpath(*rel.parts)
    require(path.is_file() and not path.is_symlink(), 'Missing/aliased closure member: ' + name)
    require(path.resolve().is_relative_to(root.resolve()), 'Resolved closure path escape')
    for parent in path.parents:
        if parent == root:
            break
        require(not parent.is_symlink(), 'Aliased closure directory')
    return path

def artifact(path, row, size_key='size'):
    require(isinstance(row, dict) and type(row.get(size_key)) is int and row[size_key] > 0,
            'Invalid artifact size')
    require(path.is_file() and not path.is_symlink(), 'Missing/aliased artifact')
    require(path.stat().st_size == row[size_key] and sha(path) == row.get('sha256'), 'Artifact byte mismatch: ' + str(path))

def shader_inventory(root):
    folder = root / 'dist/flow-wgsl'
    names = set()
    for name, count in CORPUS_COUNTS.items():
        manifest = member(folder, name)
        data = json.loads(manifest.read_text())
        require(len(data['shaders']) == count, 'Incomplete shader corpus: ' + name)
        for source, digest in data.get('inputHashes', {}).items():
            require(sha(member(root / 'work/candidate/PhysX/flow', source)) == digest, 'Upstream shader input changed')
        for source, digest in data.get('sourceHashes', {}).items():
            require(sha(member(root / 'addons/flow', source)) == digest, 'First-party shader input changed')
        names.add(name)
        for row in data['shaders']:
            source_root = root if row['source'].startswith('addons/') else root / 'work/candidate/PhysX/flow'
            require(sha(member(source_root, row['source'])) == row['sourceSha256'], 'Shader source changed')
            for key in ('wgsl', 'reflection'):
                relative = row[key]
                require(relative not in names, 'Duplicate shader member')
                path = member(folder, relative)
                require(sha(path) == row[key + 'Sha256'], 'Shader differs from its manifest')
                names.add(relative)
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()
              and (p.suffix == '.wgsl' or p.name.endswith('.reflection.json') or p.name == 'manifest.json')}
    require(names == actual, 'Shader inventory contains missing or unknown members')
    return {'dist/flow-wgsl/' + name: sha(folder / name) for name in sorted(names)}

def input_inventory(root, host_manifest):
    names = set(host_manifest['sourceHashes'])
    names.update(p.relative_to(root).as_posix() for p in (root / 'addons/flow').iterdir()
                 if p.is_file() and p.suffix in ('.py', '.mjs', '.h', '.hlsli', '.hlsl'))
    names.update(p.relative_to(root).as_posix() for p in (root / 'work/candidate/PhysX/flow').rglob('*')
                 if p.is_file() and p.suffix in ('.cpp', '.h', '.hpp', '.inl', '.hlsl', '.hlsli', '.nfproj'))
    names.add('baseline/flow-host-build-manifest.json')
    names.update(shader_inventory(root))
    return {name: sha(member(root, name)) for name in sorted(names)}

def validate_component(root, manifest_path=None):
    root = root.resolve()
    path = manifest_path or root / 'dist/flow-component/manifest.json'
    data = json.loads(path.read_text())
    require(data.get('schema') == 'flow-component-build-v1' and data.get('status') == 'COMPILED_NOT_RUNTIME_TESTED', 'Invalid Flow component schema/status')
    require(data.get('capabilities') == CAPABILITIES and all(type(v) is int for v in data['capabilities'].values()), 'Unknown component capability')
    baseline = member(root, 'baseline/flow-host-build-manifest.json')
    require(sha(baseline) == BASE_MANIFEST_SHA256, 'Different proven Flow baseline')
    host_row = data['hostBuildManifest']; host_path = member(root, host_row['path'])
    artifact(host_path, host_row)
    host = json.loads(host_path.read_text())
    require(host.get('schema') == 'flow-host-build-v1' and all(type(host.get(k)) is int and host[k] == v for k, v in CAPABILITIES.items()), 'Host capability mismatch')
    require(data.get('compiler') == host['compiler'], 'Component compiler differs from host build')
    require(data.get('inputs') == input_inventory(root, host), 'Component source/header/shader closure differs')
    for name, digest in host['sourceHashes'].items():
        require(sha(member(root, name)) == digest, 'Host source changed: ' + name)
    original = json.loads(baseline.read_text())
    for name, digest in original['sourceHashes'].items():
        if not name.startswith('work/flow-rebase-sources/'):
            require(sha(member(root, name)) == digest, 'Proven production input changed: ' + name)
    for name, row in host['artifacts'].items():
        artifact(member(root, host_path.parent.relative_to(root).as_posix() + '/' + name), row)
    obj = data['object']; artifact(member(root, obj['path']), obj)
    require(obj['path'] == 'work/pr_flow_host.o', 'Unexpected component object')
    require(data.get('shaderInventory') == shader_inventory(root), 'Component shader metadata differs')
    return data

def validate_unified(root, loader, component_manifest=None):
    component = validate_component(root, component_manifest)
    component_path = component_manifest or root / 'dist/flow-component/manifest.json'
    loader = loader.resolve()
    require(loader.name == 'physx-pe.mjs', 'Unexpected unified loader name')
    manifest_path = loader.with_name('build-manifest.json')
    data = json.loads(manifest_path.read_text())
    require(data.get('status') in ('COMPILED_NOT_RUNTIME_TESTED', 'PASS'), 'Unified build not compiled')
    for name in ('physx-pe.mjs', 'physx-pe.wasm'):
        artifact(loader.with_name(name), data['artifacts'][name], 'bytes')
    ref = data.get('flowComponent', {})
    require(ref.get('schema') == 'flow-component-link-v1', 'Missing explicit Flow component link')
    require(ref.get('manifestSha256') == sha(component_path), 'Wrong component manifest')
    require(ref.get('objectSha256') == component['object']['sha256'] and type(ref.get('objectBytes')) is int
            and ref['objectBytes'] == component['object']['size'], 'Wrong component object identity')
    require(ref.get('capabilities') == CAPABILITIES and all(type(v) is int for v in ref['capabilities'].values()), 'Unknown/missing unified Flow capability')
    require(data.get('compiler') == component['compiler'], 'Unified compiler mismatch')
    linked = local_path(ref['linkInput'])
    artifact(linked, {'sha256': ref['objectSha256'], 'size': ref['objectBytes']})
    commands = data.get('commands'); index = ref.get('linkCommandIndex')
    require(isinstance(commands, list) and type(index) is int and 0 <= index < len(commands), 'Invalid link command index')
    command = commands[index]
    require(isinstance(command, list) and command and all(isinstance(v, str) for v in command), 'Malformed link command')
    require(Path(command[0]).name in ('em++', 'em++.py') and command.count(ref['linkInput']) == 1
            and command.count('-o') == 1, 'Component not bound to a real final link')
    out = command.index('-o') + 1
    require(out < len(command) and local_path(command[out]).resolve() == loader, 'Link output differs from served loader')
    for name, digest in component['inputs'].items():
        if name.startswith('addons/flow/') and Path(name).suffix in ('.mjs', '.h', '.hlsli', '.hlsl', '.py'):
            # Final build records all component inputs, including proof tools;
            # it cannot claim the object came from a different bridge closure.
            require(data.get('bridge_sources', {}).get(name) == digest, 'Unified Flow bridge closure differs: ' + name)
    return {'manifestPath': str(manifest_path), 'manifestSha256': sha(manifest_path),
            'componentManifestSha256': sha(component_path), 'componentObjectSha256': component['object']['sha256'],
            'artifacts': data['artifacts'], 'component': component}
