# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Generate wasm32 addon declarations from actual exported C signatures.

Pointers are numbers in the shared WASM heap; these declarations do not grant
ownership or validate lifetime. Unsupported signatures fail instead of guessing.
Wrapper declarations are reviewed templates checked against their JS methods.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE_SOURCES = ('bridge/pr_bulk_rust.cpp', 'addons/blast/pr_blast_wasm.cpp',
                  'addons/blast/pr_blast_authoring.cpp', 'addons/flow/pr_flow_host.cpp',
                  'addons/flow/rebase_host.h', 'addons/flow/momentum_exchange_host.h',
                  'addons/thermal/pr_wood_thermal.cpp', 'addons/thermal/pr_wood_thermal_multirate.cpp')
SCALARS = {'int', 'unsigned', 'unsigned int', 'float', 'double', 'uint32_t',
           'int32_t', 'uintptr_t', 'std::uint32_t', 'std::int32_t'}


def native_signatures(root: Path) -> dict[str, tuple[str, list[str]]]:
    result = {}
    for relative in NATIVE_SOURCES:
        source = (root / relative).read_text(encoding='utf-8')
        source = re.sub(r'/\*.*?\*/|//[^\n]*', '', source, flags=re.S)
        if relative.startswith('addons/thermal/'):
            # The selected thermal translation unit includes the base source.
            # Scan both definitions once; no general include or macro expansion.
            base = (root / 'addons/thermal/pr_wood_thermal.cpp').read_text(encoding='utf-8')
            defines = re.findall(r'^#define PR_EXPORT([^\n]*)$', base, re.M)
            if [value.strip() for value in defines] != ['EMSCRIPTEN_KEEPALIVE', '']:
                raise ValueError('Thermal export macro differs from its selected wasm32 definition')
            if relative.endswith('_multirate.cpp') and source.count('#include "pr_wood_thermal.cpp"') != 1:
                raise ValueError('Thermal translation unit does not include its selected base source once')
            source = re.sub(r'^\s*#[^\n]*', '', source, flags=re.M)
            source = re.sub(r'\bPR_EXPORT\b', 'EMSCRIPTEN_KEEPALIVE', source)
        expected = source.count('EMSCRIPTEN_KEEPALIVE')
        matches = list(re.finditer(r'EMSCRIPTEN_KEEPALIVE\s+([^(){};]+?)\s+(pr_\w+)\s*\(([^()]*)\)\s*\{', source))
        if len(matches) != expected:
            raise ValueError('Unsupported exported C declaration in ' + relative)
        for match in matches:
            return_type, name, arguments = match.groups()
            if '_' + name in result:
                raise ValueError('Duplicate native export: ' + name)
            return_type = c_type(return_type)
            parameters = []
            for argument in arguments.split(',') if arguments.strip() else []:
                declaration = re.fullmatch(r'\s*(.+?)\s*\b(\w+)\s*', argument)
                if declaration is None:
                    raise ValueError('Unsupported C parameter: ' + argument)
                kind, parameter = declaration.groups()
                if c_type(kind) == 'void':
                    raise ValueError('Void C parameter: ' + argument)
                parameters.append(parameter + ': number')
            result['_' + name] = (return_type, parameters)
    return dict(sorted(result.items()))


def c_type(value: str) -> str:
    value = ' '.join(value.replace('const ', '').split())
    if '*' in value:
        # No callbacks/function pointers or arrays exist in this ABI.
        if not re.fullmatch(r'[\w: ]+\s*\*', value):
            raise ValueError('Unsupported native pointer type: ' + value)
        return 'number'
    if value == 'void':
        return 'void'
    if value not in SCALARS:
        raise ValueError('Unsupported native scalar type: ' + value)
    return 'number'


def native_declarations(root: Path) -> str:
    lines = ['// Generated directly from the MIT addon C ABI sources.',
             '// wasm32 pointers and opaque handles are numbers, not owned JS objects.',
             'declare namespace PhysX {']
    for name, (result, parameters) in native_signatures(root).items():
        lines.append('    function ' + name + '(' + ', '.join(parameters) + '): ' + result + ';')
    lines.append('}')
    return '\n'.join(lines)


def declaration_idl(root: Path) -> str:
    """Read the locked IDL directly from its complete checked-in added-file patch."""
    lock = json.loads((root / 'upstream.lock.json').read_text(encoding='utf-8'))
    patch = (root / 'patches/browser-overlay.patch').read_bytes()
    if hashlib.sha256(patch).hexdigest() != lock['browser_overlay_sha256']:
        raise ValueError('Reviewed browser overlay changed')
    relative = 'physx/source/webidlbindings/src/wasm/PhysXWasm.idl'
    blocks = re.split(r'(?=^diff --git )', patch.decode('utf-8'), flags=re.M)
    matching = [block for block in blocks if block.startswith(f'diff --git a/{relative} b/{relative}\n')]
    if len(matching) != 1 or re.search(r'^new file mode 100(?:644|755)$', matching[0], re.M) is None:
        raise ValueError('Reviewed IDL is not one complete added source')
    rows = matching[0].splitlines()
    hunks = [index for index, row in enumerate(rows) if row.startswith('@@ ')]
    if len(hunks) != 1 or not re.fullmatch(r'@@ -0,0 \+1,\d+ @@', rows[hunks[0]]):
        raise ValueError('Reviewed IDL patch is not a complete single hunk')
    content = rows[hunks[0] + 1:]
    if any(not row.startswith('+') for row in content):
        raise ValueError('Reviewed IDL patch contains unexpected context')
    source = '\n'.join(row[1:] for row in content) + '\n'
    if hashlib.sha256(source.encode()).hexdigest() != lock['declaration_idl_sha256']:
        raise ValueError('Reviewed IDL source differs from its pin')
    return source


def generate(root: Path = ROOT) -> dict[str, bytes]:
    from generate_physx_types import generate_types
    outputs = {'types/physx-pe.d.ts': generate_types(declaration_idl(root), root)}
    outputs['types/physx-pe.d.mts'] = outputs['types/physx-pe.d.ts']
    signatures = native_signatures(root)
    metadata = {'schema': 'physx-pe.addon-declarations/v1', 'pointerModel': 'wasm32',
                'sources': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in NATIVE_SOURCES},
                'exports': {name: {'returnType': result, 'parameters': parameters}
                            for name, (result, parameters) in signatures.items()}}
    outputs['types/addon-abi.json'] = (json.dumps(metadata, indent=2) + '\n').encode()
    for template in sorted((root / 'tools/type_templates').glob('*.d.mts')):
        relative = template.read_text(encoding='utf-8').splitlines()[2].removeprefix('// Output: ')
        target = root / relative
        if not target.resolve().is_relative_to(root.resolve()) or relative in outputs:
            raise ValueError('Invalid wrapper declaration target: ' + relative)
        outputs[relative] = template.read_bytes()
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    for relative, content in generate().items():
        path = ROOT / relative
        if args.check:
            if not path.is_file() or path.read_bytes() != content:
                raise ValueError('Generated addon declaration differs: ' + relative)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        print(('Checked ' if args.check else 'Generated ') + relative)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
