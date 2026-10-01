# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Prospective real-source Flow declaration guards; no native execution claim."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/flow-sdk-component-01'
sys.path.insert(0, str(ROOT / 'tools'))
import generate_addon_types as addon
import typecheck_browser as checker


class FlowComponentDeclarations(unittest.TestCase):
    def test_hash_bound_sources_and_api_handoff_are_retained(self):
        receipt = json.loads((FIXTURES / 'source-pins.json').read_text())
        for name, descriptor in receipt['files'].items():
            with self.subTest(path=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), descriptor['sha256'])
        self.assertEqual(hashlib.sha256((FIXTURES / 'api-contract-01.json').read_bytes()).hexdigest(),
            'a649f4f03c074df9fbdc1d6cee2d0310b35482f66432cc517b33098d705cb1e4')

    def test_seven_header_exports_and_eight_cpp_exports_are_additive(self):
        previous = json.loads((FIXTURES / 'alpha2-addon-abi.json').read_text())['exports']
        generated = json.loads(addon.generate(ROOT)['types/addon-abi.json'])['exports']
        self.assertEqual({name: generated[name] for name in previous}, previous)
        added = set(generated) - set(previous)
        contract = json.loads((FIXTURES / 'api-contract-01.json').read_text())
        header = {row['name'] for row in contract['rawExports']}
        cpp = {'_pr_flow_host_solid_abi', '_pr_flow_host_solids', '_pr_flow_host_scalar_abi',
               '_pr_flow_host_scalar_sources', '_pr_flow_host_scalar_receipt', '_pr_flow_host_scalar_count',
               '_pr_flow_host_scalar_frame', '_pr_flow_host_scalar_dt'}
        self.assertTrue(header | cpp <= added)
        selected = json.loads((ROOT / 'reference/combined-native-06/expected-addon-exports.json').read_text())['exports']
        self.assertEqual(generated, selected)
        self.assertEqual(len(generated), 96)
        for name in header | cpp:
            self.assertIn(name + '(', checker.CONSUMER)
        self.assertEqual(generated['_pr_flow_host_rebase']['parameters'],
            ['handle: number', 'x: number', 'y: number', 'z: number', 'commit: number'])
        self.assertEqual(generated['_pr_flow_host_momentum_commit']['parameters'],
            ['handle: number', 'sequence: number', 'pointer: number', 'count: number'])

    def test_missing_new_export_header_fails_instead_of_silently_omitting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            child = Path(directory)
            for name in addon.NATIVE_SOURCES:
                if name == 'addons/flow/momentum_exchange_host.h':
                    continue
                target = child / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
            with self.assertRaises(FileNotFoundError):
                addon.native_signatures(child)

    def test_inherited_idl_declarations_and_notices_remain_byte_identical(self):
        previous = (FIXTURES / 'alpha2-physx-pe.d.ts').read_bytes()
        generated = addon.generate(ROOT)['types/physx-pe.d.ts']
        marker = b'// Generated directly from the MIT addon C ABI sources.'
        self.assertEqual(previous.split(marker)[0], generated.split(marker)[0])
        self.assertEqual(generated, addon.generate(ROOT)['types/physx-pe.d.mts'])

    def test_all_contracted_methods_and_opaque_views_are_declared(self):
        declaration = (ROOT / 'addons/flow/flow_host_webgpu.d.mts').read_text()
        contract = json.loads((FIXTURES / 'api-contract-01.json').read_text())
        for method in contract['hostMethods']:
            self.assertRegex(declaration, r'\b' + re.escape(method['name']) + r'(?:<[^;\n]+>)?\(')
        for name in ('setSolidBoundaries', 'setScalarSources', 'solidBoundaryWGSL'):
            self.assertIn(name, declaration)
        for text in ('unique symbol', 'readonly gas:', 'readonly contacts:', 'FlowSynchronousCoordinator',
                     'coordinateEpoch', 'scalarSourceReceipt', 'exactRequestedTimeStep'):
            self.assertIn(text, declaration)
        self.assertNotIn('heatObligationJ:', declaration)
        self.assertIn('not claim Engine thermal deposition', checker.CONSUMER)


if __name__ == '__main__':
    unittest.main()
