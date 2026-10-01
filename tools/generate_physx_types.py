# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) <https://github.com/BTSpaniel>
# SPDX-License-Identifier: MIT
"""Generate declarations from the restricted Emscripten WebIDL used by PhysX, without npm.

Unknown syntax or type names fail closed. Numeric enum constants are described as
numbers rather than inventing values from declaration order (PhysX uses bitmasks).
"""
from __future__ import annotations
import re


def generate_types(source, addon_root=None):
    source = re.sub(r'/\*.*?\*/|//[^\n]*', '', source, flags=re.S)
    # JSImplementation overrides receive native pointer numbers when called
    # from C++; the generated callable methods also accept wrapper objects.
    callback_interfaces = set(re.findall(
        r'\[[^\[\]]*\bJSImplementation\s*=\s*"[^"]+"[^\[\]]*\]\s*interface\s+(\w+)', source))
    source = re.sub(r'\[(?:[A-Za-z_][^\[\]]*)\]', '', source)
    declarations = list(re.finditer(r'\b(interface|enum)\s+(\w+)\s*\{([^{}]*)\}\s*;', source, re.S))
    parents = dict(re.findall(r'(\w+)\s+implements\s+(\w+)\s*;', source))
    remainder = re.sub(r'\b(interface|enum)\s+\w+\s*\{[^{}]*\}\s*;|\w+\s+implements\s+\w+\s*;', '', source, flags=re.S)
    if remainder.strip():
        raise ValueError(f'Unsupported WebIDL declaration: {remainder[:200]}')
    names = {match[2] for match in declarations} | {'VoidPtr'}
    signatures = {}
    for match in declarations:
        methods = {}
        for member in match[3].split(';'):
            method = re.fullmatch(r'(?:static )?(.+?) (\w+)\((.*)\)', ' '.join(member.split()))
            if method:
                result, name, arguments = method.groups()
                argument_types = tuple(re.sub(r'\s+\w+\s*$', '', argument.strip()) for argument in arguments.split(',') if argument.strip())
                methods[name] = (result, argument_types)
        signatures[match[2]] = methods

    def inherited_methods(name):
        methods = inherited_methods(parents[name]) if name in parents else {}
        return {**methods, **signatures.get(name, {})}
    primitives = {'void': 'void', 'boolean': 'boolean', 'DOMString': 'string', 'any': 'any',
                  'octet': 'number', 'byte': 'number', 'short': 'number', 'unsigned short': 'number',
                  'long': 'number', 'unsigned long': 'number', 'long long': 'bigint', 'unsigned long long': 'bigint',
                  'float': 'number', 'double': 'number', 'unrestricted float': 'number', 'unrestricted double': 'number'}

    def ts_type(value):
        value = ' '.join(value.split())
        if value.endswith('[]'):
            return f'ReadonlyArray<{ts_type(value[:-2])}>'
        if value in primitives:
            return primitives[value]
        if value in names:
            return value
        raise ValueError(f'Unknown WebIDL type {value!r}')

    lines = ['// Generated from the pinned PhysXWasm.idl by tools/generate_physx_types.py.',
             '// Bindings retain the MIT license in LICENSES/Fabmax-Bindings-MIT.txt.',
             '// Adapted declarations: inherited SDK notices also apply; see THIRD_PARTY_NOTICES.md.',
             'declare function PhysX(options?: { wasmBinary?: BufferSource; locateFile?: (path: string, prefix: string) => string; print?: (...args: unknown[]) => void; printErr?: (...args: unknown[]) => void }): Promise<typeof PhysX & typeof PhysX.PxTopLevelFunctions>;',
             'export default PhysX;', 'declare namespace PhysX {',
             '    function destroy(object: object): void;', '    function getPointer(object: object): number;',
             '    function wrapPointer<T>(pointer: number, wrapper: {prototype: T}): T;',
             '    function castObject<T>(object: object, wrapper: {prototype: T}): T;',
             '    function _malloc(bytes: number): number;', '    function _free(pointer: number): void;',
             '    class VoidPtr { readonly ptr: number; }']
    for name, array in [('HEAP8', 'Int8Array'), ('HEAPU8', 'Uint8Array'), ('HEAP16', 'Int16Array'), ('HEAPU16', 'Uint16Array'), ('HEAP32', 'Int32Array'), ('HEAPU32', 'Uint32Array'), ('HEAPF32', 'Float32Array'), ('HEAPF64', 'Float64Array')]:
        lines.append(f'    const {name}: {array};')
    for match in declarations:
        kind, name, body = match.groups()
        if kind == 'enum':
            values = re.findall(r'"([^"]+)"', body)
            if re.sub(r'"[^"]+"|[,\s]', '', body):
                raise ValueError(f'Unsupported enum {name}')
            lines += [f'    type {name} = number;', f'    const {name}: {{']
            lines += [f'        readonly {value.split("::")[-1]}: number;' for value in values]
            lines.append('    };')
            continue
        overridden = []
        if name in parents:
            inherited = inherited_methods(parents[name])
            overridden = [method for method, signature in signatures[name].items()
                          if method in inherited and inherited[method] != signature]
        # Native bindings can replace a base method with a different required
        # signature. Model that replacement without invalid TS subclassing or
        # fabricating inherited overloads that the runtime cannot execute.
        lines.append(f'    class {name}' + (f' extends {parents[name]}' if name in parents and not overridden else '') + ' {')
        for member in body.split(';'):
            member = ' '.join(member.split())
            if not member:
                continue
            attribute = re.fullmatch(r'(static )?(readonly )?attribute (.+?) (\w+)(?:\[(\d+)\])?', member)
            if attribute:
                static, readonly, value_type, field, size = attribute.groups()
                indexed = value_type.endswith('[]') or size is not None
                field_type = ts_type(value_type[:-2] if value_type.endswith('[]') else value_type)
                if indexed:
                    # Emscripten does not expose a JS Array here. Its generated
                    # property aliases call indexed functions without an index,
                    # so require explicit element access rather than typing the
                    # misleading property or accepting an array as a setter.
                    lines.append(f'        {static or ""}get_{field}(index: number): {field_type};')
                    if not readonly:
                        lines.append(f'        {static or ""}set_{field}(index: number, value: {field_type}): void;')
                else:
                    lines.append(f'        {static or ""}{readonly or ""}{field}: {field_type};')
                    lines.append(f'        {static or ""}get_{field}(): {field_type};')
                    if not readonly:
                        lines.append(f'        {static or ""}set_{field}(value: {field_type}): void;')
                continue
            method = re.fullmatch(r'(static )?(.+?) (\w+)\((.*)\)', member)
            if not method:
                raise ValueError(f'Unsupported {name} member: {member}')
            static, result, method_name, arguments = method.groups()
            params = []
            for arg in arguments.split(',') if arguments.strip() else []:
                argument = re.fullmatch(r'\s*(optional )?(.+?) (\w+)\s*', arg)
                if not argument:
                    raise ValueError(f'Unsupported argument {arg}')
                optional, value_type, param = argument.groups()
                argument_type = ts_type(value_type)
                if name in callback_interfaces and value_type in names:
                    argument_type += ' | number'
                params.append(f'{param}{"?" if optional else ""}: {argument_type}')
            signature = ', '.join(params)
            if method_name == name:
                lines.append(f'        constructor({signature});')
            else:
                # webidl_binder exposes IDL static helpers on the constructor's
                # prototype. Only PxTopLevelFunctions is forwarded onto Module.
                lines.append(f'        {static if static and name == "PxTopLevelFunctions" else ""}{method_name}({signature}): {ts_type(result)};')
        lines.append('    }')
        if overridden:
            excluded = ' | '.join("'" + method + "'" for method in overridden)
            lines.append(f'    interface {name} extends Omit<{parents[name]}, {excluded}> {{}}')
    lines.append('}')
    if addon_root is not None:
        from generate_addon_types import native_declarations
        lines.append(native_declarations(addon_root))
    return ('\n'.join(lines) + '\n').encode()
