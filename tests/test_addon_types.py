# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Real-source declaration coverage and fail-closed signature generation."""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import generate_addon_types as addon
from generate_physx_types import generate_types


class AddonDeclarations(unittest.TestCase):
    def test_generated_outputs_match_actual_native_sources(self):
        outputs = addon.generate(ROOT)
        for name, expected in outputs.items():
            with self.subTest(path=name):
                self.assertEqual((ROOT / name).read_bytes(), expected)
        metadata = json.loads(outputs['types/addon-abi.json'])
        signatures = addon.native_signatures(ROOT)
        self.assertEqual(set(metadata['exports']), set(signatures))
        declaration = outputs['types/physx-pe.d.ts'].decode()
        for name, (result, parameters) in signatures.items():
            self.assertIn('function ' + name + '(' + ', '.join(parameters) + '): ' + result + ';', declaration)
        self.assertGreater(len(signatures), 40)

    def test_unsupported_and_duplicate_exports_fail(self):
        for source in ('EMSCRIPTEN_KEEPALIVE bool pr_bad() {return false;}',
                       'EMSCRIPTEN_KEEPALIVE int pr_bad(int (*callback)(int)) {return 0;}',
                       'EMSCRIPTEN_KEEPALIVE int pr_bad() {return 0;}\nEMSCRIPTEN_KEEPALIVE int pr_bad() {return 0;}'):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for relative in addon.NATIVE_SOURCES:
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source if relative == addon.NATIVE_SOURCES[0] else '', encoding='utf-8')
                with self.assertRaises(ValueError):
                    addon.native_signatures(root)

    def test_i64_values_use_bigint_and_helpers_use_real_prototypes(self):
        source = ('interface PxSerialization { static unsigned long long find(unsigned long long id); };'
                  'interface PxTopLevelFunctions { static long long forwarded(long long id); };')
        generated = generate_types(source).decode()
        self.assertIn('find(id: bigint): bigint;', generated)
        self.assertNotIn('static find', generated)
        self.assertIn('static forwarded(id: bigint): bigint;', generated)

    def test_every_public_wrapper_method_has_a_declaration(self):
        for relative in ('bridge/physx-bulk.mjs', 'bridge/physx-bulk-rust.mjs',
                         'addons/flow/flow_host_webgpu.mjs', 'addons/flow/webgpu_bridge.mjs',
                         'addons/flow/flow_solid_boundary.mjs', 'addons/flow/flow_scalar_sources.mjs'):
            source = (ROOT / relative).read_text(encoding='utf-8')
            declaration = (ROOT / relative.replace('.mjs', '.d.mts')).read_text(encoding='utf-8')
            constructor = re.search(r'^( +)constructor\(', source, re.M)
            methods = set(re.findall(r'^' + constructor[1] + r'(?:static )?(?:async )?([A-Za-z]\w*)\([^;\n]*\)\s*\{', source, re.M)) if constructor else set()
            methods.update(re.findall(r'^export (?:async )?function (\w+)\(', source, re.M))
            for name in methods:
                with self.subTest(module=relative, method=name):
                    self.assertRegex(declaration, r'\b' + name + r'(?:<[^;\n]+>)?\(')
        self.assertIn('Promise<FlowOutput | null>', (ROOT / 'addons/flow/flow_host_webgpu.d.mts').read_text())

    def test_native_array_attributes_require_indexed_accessors(self):
        source = ('interface Wheel { attribute float radius; }; '
                  'interface Car { attribute Wheel[] wheels; readonly attribute float[] loads; '
                  'attribute unsigned long ids[4]; attribute float mass; };')
        generated = generate_types(source).decode()
        for signature in ('get_wheels(index: number): Wheel;', 'set_wheels(index: number, value: Wheel): void;',
                          'get_loads(index: number): number;', 'get_ids(index: number): number;',
                          'set_ids(index: number, value: number): void;', 'get_mass(): number;'):
            self.assertIn(signature, generated)
        self.assertNotIn('ReadonlyArray<', generated)
        self.assertNotIn('set_loads', generated)
        self.assertNotIn('wheels:', generated)

    def test_js_callback_pointer_arguments_accept_delivered_native_numbers(self):
        source = ('interface Header {}; interface Ordinary { void read(Header header); }; '
                  '[JSImplementation="NativeCallback"] interface Callback { '
                  'void Callback(); void onContact([Const, Ref] Header header, unsigned long count); };')
        generated = generate_types(source).decode()
        self.assertIn('read(header: Header): void;', generated)
        self.assertIn('onContact(header: Header | number, count: number): void;', generated)
        self.assertIn('function wrapPointer<T>(pointer: number, wrapper: {prototype: T}): T;', generated)
        self.assertIn('function castObject<T>(object: object, wrapper: {prototype: T}): T;', generated)

    def test_runtime_metadata_is_part_of_verified_harness(self):
        import evidence
        self.assertIn('types/addon-abi.json', evidence.HARNESS)
        self.assertIn('web/advanced-regressions.mjs', evidence.HARNESS)
        self.assertIn('web/vehicle-callback-regressions.mjs', evidence.HARNESS)
        for module in ('web/advanced-regressions.mjs', 'web/vehicle-callback-regressions.mjs'):
            source = (ROOT / module).read_text(encoding='utf-8')
            fixture_names = re.findall(r"'([^']+)'", source.split('export const ', 1)[1].split('];', 1)[0])
            self.assertEqual(len(fixture_names), len(set(fixture_names)))
            for name in fixture_names:
                self.assertIn(name, evidence.CORE_TESTS)


if __name__ == '__main__':
    unittest.main()
