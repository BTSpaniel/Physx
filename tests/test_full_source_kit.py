# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Actual source/declaration guards; no compiler, native or GPU execution."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import physx_lab as lab
import generate_addon_types as addon
import native_components as native
import build
import release
import typecheck_browser as checker


class FullSourceKit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='physx-pe-full-source-')
        cls.root = Path(cls.temp.name)
        selected = json.loads((ROOT / 'source-selection.json').read_text())
        paths = set(selected['prospectiveNativeSelection']['sourceHashes']) | set(addon.NATIVE_SOURCES)
        paths.add('source-selection.json')
        for name in paths:
            target = cls.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_actual_96_signatures_match_frozen_component_contract(self):
        expected = json.loads((ROOT / 'reference/combined-native-06/expected-addon-exports.json').read_text())
        generated = json.loads(addon.generate(ROOT)['types/addon-abi.json'])
        self.assertEqual(expected['exports'], generated['exports'])
        self.assertEqual(len(generated['exports']), 96)
        original = json.loads((ROOT / 'tests/fixtures/flow-sdk-component-01/alpha2-addon-abi.json').read_text())['exports']
        self.assertEqual({name: generated['exports'][name] for name in original}, original)
        self.assertEqual(len(original), 56)

    def test_selected_native_inventory_is_exact_and_source_only(self):
        inputs = native.selected_source_inputs(ROOT)
        self.assertEqual(len(inputs), 215)
        pin = json.loads((ROOT / 'source-inputs/blast/source-pins.json').read_text())
        self.assertEqual(len(pin['sourceHashes']), 182)
        self.assertFalse(any(Path(name).suffix in ('.o', '.a', '.wasm') for name in inputs))
        actual_outputs = [name for name in release.inventory(ROOT)
                          if Path(name).suffix in ('.o', '.a', '.wasm')
                          or Path(name).name in ('physx-pe.mjs', 'flow-host.mjs')]
        self.assertEqual(actual_outputs, [])

    def mutation(self, name, change, check):
        path = self.root / name
        before = path.read_bytes()
        try:
            path.write_bytes(change(before))
            with self.assertRaises((lab.LabError, ValueError)):
                check()
        finally:
            path.write_bytes(before)

    def test_native_source_geometry_or_material_changes_are_rejected(self):
        for name in ('bridge/pr_bulk_rust.cpp', 'addons/blast/pr_blast_wasm.cpp',
                     'addons/thermal/pr_wood_thermal_multirate.cpp'):
            with self.subTest(path=name):
                self.mutation(name, lambda b: b + b'\n', lambda: native.selected_source_inputs(self.root))

    def test_extra_raw_upstream_input_is_not_admitted(self):
        path = self.root / 'source-inputs/blast/unselected.h'
        try:
            path.write_text('// Unselected test input\n')
            with self.assertRaisesRegex(lab.LabError, 'inventory changed'):
                native.selected_source_inputs(self.root)
        finally:
            path.unlink()

    def test_macro_recognition_is_bounded_to_the_actual_thermal_definition(self):
        self.mutation('addons/thermal/pr_wood_thermal.cpp',
            lambda b: b.replace(b'#define PR_EXPORT EMSCRIPTEN_KEEPALIVE', b'#define PR_EXPORT unknown'),
            lambda: addon.native_signatures(self.root))
        self.mutation('addons/thermal/pr_wood_thermal_multirate.cpp',
            lambda b: b.replace(b'#include "pr_wood_thermal.cpp"', b'#include "different.cpp"'),
            lambda: addon.native_signatures(self.root))

    def test_thermal_originals_reconstruct_selected_bytes_using_only_spdx_change(self):
        receipt = json.loads((ROOT / 'reference/combined-native-06/provenance/thermal-license-transform.json').read_text())
        self.assertEqual(len(receipt['files']), 4)
        for name, item in receipt['files'].items():
            original = (ROOT / 'reference/combined-native-06' / item['originalPath']).read_bytes()
            selected = (ROOT / name).read_bytes()
            self.assertEqual(hashlib.sha256(original).hexdigest(), item['originalSha256'])
            self.assertEqual(original.replace(b'SPDX-License-Identifier: LicenseRef-ParticleRealms-Alpha',
                                             b'SPDX-License-Identifier: MIT'), selected)

    def test_fresh_generated_blast_sources_equal_selected_f64_component(self):
        generated = native.prepare_blast_sources(self.root / 'source-inputs/blast', self.root)
        self.assertEqual(len(generated['generated']), 4)
        self.assertEqual(generated['physicalTolerance'], 1e-6)
        self.assertEqual(generated['massAbi'], 1)
        self.assertEqual(generated['preparationAbi'], 1)

    def test_27_translation_unit_order_and_flags_match_selected_original_recipe(self):
        native.prepare_blast_sources(self.root / 'source-inputs/blast', self.root)
        sources, flags = native.blast_recipe(self.root / 'source-inputs/blast', self.root)
        origin = json.loads((ROOT / 'reference/combined-native-06/provenance/selected-blast-build.json').read_text())
        stems = [Path(name).stem.rsplit('-', 1)[0] for name in origin['objects']]
        self.assertEqual([p.stem for p in sources], stems)
        self.assertTrue(all(path.is_file() for path in sources))
        source_root = origin['sourceRoot']
        adapted = [value.replace(source_root + '/work/physical-generated/sections-v3-f64prep-02',
                                 str(self.root / 'work/blast-stress-generated'))
                         .replace(source_root + '/upstream/blast', str(self.root / 'source-inputs/blast'))
                         .replace(source_root + '/addons/blast', str(self.root / 'addons/blast'))
                   for value in origin['compileFlags']]
        self.assertEqual([value.replace('\\', '/') for value in flags],
                         [value.replace('\\', '/') for value in adapted])
        self.assertNotIn('-ffast-math', flags)
        self.assertNotIn('-mfma', flags)

    def test_source_compiler_has_no_object_existence_cache_admission(self):
        tree = ast.parse((ROOT / 'tools/native_components.py').read_text())
        top = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'compile_native_components')
        inner = next(n for n in top.body if isinstance(n, ast.FunctionDef) and n.name == 'compile_one')
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                             and n.func.attr == 'exists' for n in ast.walk(inner)))
        self.assertTrue(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                            and n.func.attr == 'run' for n in ast.walk(inner)))
        self.assertIn('-DPR_THERMAL_STABLE_DEPLETION=1', native.THERMAL_FLAGS)
        self.assertTrue(set(native.STRICT_FLAGS) <= set(native.THERMAL_FLAGS))

    def test_fresh_component_linkage_is_exact_and_idempotent(self):
        import prepare_sources
        base = prepare_sources.added_linkage(ROOT / 'patches/browser-overlay.patch',
                   'physx/source/compiler/cmake/emscripten/PhysXWasmBindings.cmake')
        repo = ROOT / 'work/candidate/PhysX'
        rendered = build.unified_linkage(base, repo)
        self.assertEqual(rendered, build.unified_linkage(rendered, repo))
        final = next(row for row in rendered.splitlines() if ' -o physx-pe.mjs' in row)
        for path in ('work/pr_blast.o', 'work/pr_flow_host.o', 'work/pr_wood_thermal_multirate.o'):
            self.assertIn(build.cmake_literal(ROOT / path), final)
        for flag in native.STRICT_FLAGS:
            self.assertEqual(final.count(flag), 1)
        for wrong in (rendered.replace('-ffp-contract=off', '-ffp-contract=fast'),
                      rendered.replace('pr_wood_thermal_multirate.o', 'unselected.o')):
            with self.assertRaises(lab.LabError):
                build.unified_linkage(wrong, repo)

    def test_prospective_consumer_covers_all_25_new_non_flow_api_signatures(self):
        current = json.loads(addon.generate(ROOT)['types/addon-abi.json'])['exports']
        old = json.loads((ROOT / 'tests/fixtures/flow-sdk-component-01/alpha2-addon-abi.json').read_text())['exports']
        new = {name for name in current if name not in old and not name.startswith('_pr_flow_host')}
        self.assertEqual(len(new), 25)
        for name in new:
            self.assertIn(name + '(', checker.CONSUMER)
        for contract in ('8-byte aligned', 'capacity floats', 'no handle argument', 'not a JS buffer',
                         'numeric address', 'returns status codes'):
            # Lifetime/range rules stay prose; raw pointer declarations cannot
            # claim TypeScript validation of the physical buffer contract.
            self.assertIn(contract, checker.CONSUMER)

    def test_reference_metadata_is_bound_to_the_committed_source_inventory(self):
        inputs = release.inventory(ROOT)
        for name in ('reference/flow-component-01/dist/flow-component/manifest.json',
                     'reference/combined-native-06/dist/candidate/build-manifest.json',
                     'reference/combined-native-06/work/physical-generated/sections-v3-f64prep-02/NvBlastExtStressSolver.cpp'):
            self.assertIn(name, inputs)
        self.assertFalse(any(name.startswith(('reports/', 'dist/', 'work/')) for name in inputs))

    def test_postbuild_outputs_do_not_hide_committed_reference_inputs(self):
        with tempfile.TemporaryDirectory(prefix='physx-pe-postbuild-layout-') as directory:
            root = Path(directory)
            paths = ('source.cpp', 'reference/work/derived.cpp', 'reference/dist/manifest.json',
                     'work/fresh.o', 'dist/candidate/physx-pe.wasm', 'reports/build.json')
            for name in paths:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'Owned inventory fixture, not a native build\n')
            actual = release.inventory(root)
            self.assertEqual(set(actual), set(paths[:3]))

    def test_runtime_package_recipe_preserves_flow_host_import_closure(self):
        host = (ROOT / 'addons/flow/flow_host_webgpu.mjs').read_text()
        imports = re.findall(r"from '\./([^']+\.mjs)'", host)
        recipe = (ROOT / 'tools/package_release.py').read_text()
        self.assertEqual(set(imports), {'flow_solid_boundary.mjs', 'flow_scalar_sources.mjs'})
        for name in imports:
            self.assertIn("'addons/flow/" + name + "'", recipe)

    def test_actual_full_build_stays_held_and_clean_source_revision_is_required(self):
        path = self.root / 'source-selection.json'
        original = path.read_bytes()
        try:
            selected = json.loads(original)
            selected.update(finalSelectionStatus='PENDING_NATIVE_INPUTS',
                            pendingNativeInputs=['Owned pending-source admission fixture'])
            path.write_text(json.dumps(selected), encoding='utf-8')
            with patch.object(release, 'ROOT', self.root), self.assertRaisesRegex(
                    lab.LabError, 'Final unified source selection is pending'):
                release.build()
        finally:
            path.write_bytes(original)
        source = (ROOT / 'release.py').read_text()
        self.assertIn("'status', '--porcelain', '--untracked-files=all'", source)
        self.assertIn('source_revision(before)', source)


if __name__ == '__main__':
    unittest.main()
