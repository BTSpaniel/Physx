# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Guard source/corpus admission using the actual frozen Flow source fixture."""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import physx_lab as lab
from flow_source_evidence import (CAPABILITIES, member, native_capabilities,
                                  require_final_selection, shader_inventory, upstream_inputs)


def copy_frozen_flow_fixture(root: Path) -> None:
    """Copy the exact sources and corpus that produced the frozen reference."""
    shutil.copytree(ROOT / 'source-inputs/flow', root / 'source-inputs/flow')
    # Fresh source-kit outputs are admitted separately after compilation.
    shutil.copytree(ROOT / 'reference/flow-component-01/addons/flow', root / 'addons/flow', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'reference/flow-component-01/dist/flow-wgsl', root / 'dist/flow-wgsl')


class FlowSourceBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='physx-pe-flow-admission-')
        cls.root = Path(cls.temporary.name)
        copy_frozen_flow_fixture(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def change(self, path, mutate, check):
        before = path.read_bytes()
        try:
            mutate(path)
            with self.assertRaises(lab.LabError):
                check()
        finally:
            path.write_bytes(before)

    def test_cli_flow_admission_imports_from_an_unrelated_directory(self):
        probe = """import inspect, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import physx_lab as lab
assert str(lab.ROOT) not in sys.path
import flow_source_evidence as evidence
from addons.flow.component_evidence import member
assert evidence.ROOT == lab.ROOT
assert evidence.component_member is member
actual = Path(inspect.getfile(member)).resolve()
assert actual == (lab.ROOT / 'addons/flow/component_evidence.py').resolve()
print(json.dumps({'root': str(lab.ROOT), 'componentFile': str(actual)}))
"""
        with tempfile.TemporaryDirectory(prefix='physx-pe-cli-import-') as directory:
            result = subprocess.run([sys.executable, '-I', '-B', '-c', probe, str(ROOT / 'tools')],
                                    cwd=directory, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(Path(observed['root']).resolve(), ROOT.resolve())
        self.assertEqual(Path(observed['componentFile']).resolve(),
                         (ROOT / 'addons/flow/component_evidence.py').resolve())

    def test_upstream_selection_uses_all_265_actual_pinned_files(self):
        hashes = upstream_inputs(self.root)
        self.assertEqual(len(hashes), 265)
        self.assertEqual(hashes['source/nvflow/Sparse.cpp'],
                         '8dc226e0bb55fc819d3cfb4cedc5a934045a65ae2e86969ec2d1ce8ef94ded43')

    def test_modified_upstream_header_is_rejected(self):
        path = self.root / 'source-inputs/flow/include/nvflow/NvFlowContext.h'
        self.change(path, lambda p: p.write_bytes(p.read_bytes() + b'\n'), lambda: upstream_inputs(self.root))

    def test_extra_or_missing_upstream_inputs_are_rejected(self):
        extra = self.root / 'source-inputs/flow/unselected.h'
        try:
            extra.write_text('// Unselected test input\n', encoding='utf-8')
            with self.assertRaises(lab.LabError):
                upstream_inputs(self.root)
        finally:
            extra.unlink()
        path = self.root / 'source-inputs/flow/include/nvflow/NvFlowContext.h'
        preserved = path.with_suffix('.guard-preserved')
        path.rename(preserved)
        try:
            with self.assertRaises(lab.LabError):
                upstream_inputs(self.root)
        finally:
            preserved.rename(path)

    def test_member_cannot_escape_the_declared_source_tree(self):
        for name in ('../source-selection.json', '/absolute', 'C:/outside', 'include\\nvflow'):
            with self.subTest(name=name), self.assertRaises(lab.LabError):
                member(self.root / 'source-inputs/flow', name)

    def test_actual_full_corpus_keeps_195_base_files_and_252_total(self):
        actual = shader_inventory(self.root)
        expected = json.loads((ROOT / 'reference/flow-component-01/dist/flow-component/manifest.json').read_text())['shaderInventory']
        self.assertEqual(len(actual), 252)
        self.assertEqual({('dist/flow-wgsl/' + name): row['sha256'] for name, row in actual.items()}, expected)

    def test_modified_generated_kernel_is_rejected(self):
        path = self.root / 'dist/flow-wgsl/addons/momentum/PrMomentumApplyCS.wgsl'
        self.change(path, lambda p: p.write_bytes(p.read_bytes() + b'\n'), lambda: shader_inventory(self.root))

    def test_missing_or_unknown_reflection_member_is_rejected(self):
        path = self.root / 'dist/flow-wgsl/addons/momentum/PrMomentumApplyCS.reflection.json'
        preserved = path.with_suffix('.guard-preserved')
        path.rename(preserved)
        try:
            with self.assertRaises(lab.LabError):
                shader_inventory(self.root)
        finally:
            preserved.rename(path)
        extra = self.root / 'dist/flow-wgsl/unknown.reflection.json'
        try:
            extra.write_text('{}\n', encoding='utf-8')
            with self.assertRaises(lab.LabError):
                shader_inventory(self.root)
        finally:
            extra.unlink()

    def test_incomplete_extension_kernel_list_is_rejected(self):
        path = self.root / 'dist/flow-wgsl/addons/solid/manifest.json'
        def mutate(p):
            data = json.loads(p.read_text()); data['shaders'].pop()
            p.write_text(json.dumps(data), encoding='utf-8')
        self.change(path, mutate, lambda: shader_inventory(self.root))

    def test_boolean_capability_is_rejected_even_when_equal_to_one(self):
        self.assertEqual(native_capabilities(dict(CAPABILITIES)), CAPABILITIES)
        for key in CAPABILITIES:
            data = dict(CAPABILITIES); data[key] = True
            with self.subTest(key=key), self.assertRaises(lab.LabError):
                native_capabilities(data)

    def test_old_base_only_native_capabilities_cannot_claim_the_full_component(self):
        with self.assertRaises(lab.LabError):
            native_capabilities({'flowHostAbi': 1})

    def test_final_build_and_package_refuse_pending_native_inputs(self):
        import release
        import package_release
        path = self.root / 'source-selection.json'
        selected = json.loads((ROOT / 'source-selection.json').read_text())
        selected.update(finalSelectionStatus='PENDING_NATIVE_INPUTS',
                        pendingNativeInputs=['Owned pending-source admission fixture'])
        path.write_text(json.dumps(selected), encoding='utf-8')
        with self.assertRaisesRegex(lab.LabError, 'Final unified source selection is pending'):
            require_final_selection(self.root)
        for command in ('build', 'verify', 'package', 'all'):
            errors = io.StringIO()
            with patch.object(release, 'ROOT', self.root), patch.object(sys, 'argv', ['release.py', command]), \
                    contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(release.main(), 2)
            self.assertIn('Final unified source selection is pending', errors.getvalue())
        self.assertFalse((self.root / 'dist/candidate/physx-pe.wasm').exists())
        with patch.object(package_release, 'ROOT', self.root), self.assertRaisesRegex(
                lab.LabError, 'Final unified source selection is pending'):
            package_release.package()

    def test_missing_pending_key_or_selection_status_cannot_unlock_final_build(self):
        original = json.loads((ROOT / 'source-selection.json').read_text())
        path = self.root / 'source-selection.json'
        for key in ('schema', 'pendingNativeInputs', 'finalSelectionStatus'):
            data = dict(original); del data[key]
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.subTest(key=key), self.assertRaises(lab.LabError):
                require_final_selection(self.root)

    def test_unknown_selection_schema_or_status_cannot_unlock_final_build(self):
        original = json.loads((ROOT / 'source-selection.json').read_text())
        path = self.root / 'source-selection.json'
        for key, value in (('schema', 'physx-pe.unknown/v2'), ('finalSelectionStatus', 'PASS'),
                           ('pendingNativeInputs', None)):
            data = {**original, key: value}; path.write_text(json.dumps(data), encoding='utf-8')
            with self.subTest(key=key), self.assertRaises(lab.LabError):
                require_final_selection(self.root)

    def test_empty_pending_list_needs_explicit_final_status_and_component_identities(self):
        original = json.loads((ROOT / 'source-selection.json').read_text())
        original.update(finalSelectionStatus='PENDING_NATIVE_INPUTS')
        original.pop('selectedComponents')
        path = self.root / 'source-selection.json'
        for extra in ({}, {'finalSelectionStatus': 'FINAL_SOURCES_SELECTED_FOR_BUILD'},
                      {'finalSelectionStatus': 'FINAL_SOURCES_SELECTED_FOR_BUILD', 'selectedComponents': {}}):
            data = {**original, 'pendingNativeInputs': [], **extra}
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.subTest(extra=extra), self.assertRaises(lab.LabError):
                require_final_selection(self.root)

    def test_source_build_has_unconditional_cpp_compilation_and_complete_cli(self):
        tree = ast.parse((ROOT / 'addons/flow/build_host.py').read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'compile_one')
        self.assertFalse(any(isinstance(node, ast.If) for node in ast.walk(function)), 'Old object cache admission returned')
        self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == 'run' for node in ast.walk(function)))
        result = subprocess.run([sys.executable, str(ROOT / 'release.py'), '--help'],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0)
        self.assertIn('build-flow-component', result.stdout)

    @unittest.skipUnless(sys.platform == 'win32', 'Native Windows path/lock driver guard')
    def test_windows_parent_quotes_compiler_paths_and_imports_the_actual_host_lock(self):
        import shlex
        import source_kit_windows as parent
        existing = ROOT / 'source-inputs/flow/include/nvflow/NvFlowContext.h'
        argv = ['em++', parent.linux_path(existing), '-DNAME=a;echo never', '-I/tmp/a b']
        invocation = parent.wsl_invocation(argv, ROOT / 'work/tool path', 'Ubuntu-24.04')
        self.assertEqual(invocation[:5], ['wsl.exe', '-d', 'Ubuntu-24.04', '--', 'bash'])
        actual_argv = shlex.split(invocation[-1].split('; exec ', 1)[1])
        self.assertEqual(actual_argv, argv)
        tree = ast.parse((ROOT / 'tools/source_kit_windows.py').read_text())
        imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
        self.assertTrue(any(node.module == 'browser_compat_endurance'
                            and any(alias.name == 'host_lock' for alias in node.names) for node in imports))
        source = (ROOT / 'tools/source_kit_windows.py').read_text()
        self.assertFalse(any((isinstance(node, ast.Import) and any(alias.name == 'fcntl' for alias in node.names))
                             or (isinstance(node, ast.ImportFrom) and node.module == 'fcntl') for node in ast.walk(tree)))
        self.assertNotIn("os.environ[", source)

    @unittest.skipUnless(sys.platform == 'win32', 'Native Windows compiler path driver guard')
    def test_full_build_selects_only_the_explicit_existing_rust_sysroot(self):
        import shlex
        import source_kit_windows as parent
        rust = ROOT / 'work/existing Rust; literal/toolchain'
        invocation = parent.wsl_invocation(['rustc', '-Vv'], ROOT / 'work/existing emsdk', 'Ubuntu-24.04', rust)
        rows = shlex.split(invocation[-1])
        assignment = next(row for row in rows if row.startswith('PATH='))
        self.assertTrue(assignment.startswith('PATH=' + parent.linux_path(rust / 'bin') + ':'))
        self.assertEqual(shlex.split(invocation[-1].split('; exec ', 1)[1]), ['rustc', '-Vv'])
        before = dict(os.environ)
        with self.assertRaisesRegex(lab.LabError, 'actual existing Rust toolchain'):
            parent.rust_toolchain_snapshot(ROOT / 'work/nonexistent-rust-toolchain', ROOT, 'Ubuntu-24.04')
        self.assertEqual(dict(os.environ), before)
        source = (ROOT / 'tools/source_kit_windows.py').read_text()
        self.assertIn("receipt['rustCompilerInputsAfter'] != receipt['rustCompilerInputsBefore']", source)
        self.assertIn("fields.get('release')!=expected", source)
        self.assertIn("libcore-*.rlib", source)

    @unittest.skipUnless(sys.platform == 'win32', 'Native Windows compiler alias driver guard')
    def test_full_build_rechecks_aliases_and_rejects_a_target_outside_captured_binaries(self):
        import source_kit_windows as parent
        source = (ROOT / 'tools/source_kit_windows.py').read_text()
        full = source.split("receipt['sourceRevisionBefore'] = source_revision(before, ROOT)", 1)[1]
        self.assertIn("receipt['nativeCompilerAliasesAfter'] = native_aliases(", full)
        self.assertIn("receipt['nativeCompilerAliasesAfter'] != receipt['nativeCompilerAliases']", full)
        self.assertIn('Native compiler aliases changed during full source compilation', full)
        emsdk = ROOT / 'work/owned-alias-fixture'
        for target in ('/outside/toolchain/clang', parent.linux_path(emsdk / 'upstream/bin/uncaptured-clang')):
            completed = subprocess.CompletedProcess([], 0, stdout=target + '\n', stderr='')
            with self.subTest(target=target), patch.object(parent.subprocess, 'run', return_value=completed), \
                    self.assertRaisesRegex(lab.LabError, 'leaves the selected tool tree|was not captured'):
                parent.native_aliases(emsdk, 'Ubuntu-24.04', {})

    def test_fresh_shader_compiler_argv_is_retained_by_every_recipe(self):
        for name in ('compile_wgsl.py', 'compile_solid_wgsl.py', 'compile_scalar_wgsl.py'):
            tree = ast.parse((ROOT / 'addons/flow' / name).read_text())
            keys = {key.value for node in ast.walk(tree) if isinstance(node, ast.Dict)
                    for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}
            with self.subTest(recipe=name):
                self.assertIn('command', keys)
                self.assertIn('compilerOutput', keys)

    def test_compiler_identity_accepts_the_real_output_channel_without_weakening_the_pin(self):
        import source_kit_windows as parent
        self.assertTrue(parent.compiler_version_matches('slang', '2025.6.1', '', '2025.6.1'))
        self.assertTrue(parent.compiler_version_matches('slang', '2025.6.1', '2025.6.1\n', ''))
        for output in ('2025.6.10', 'unrelated warning\n2025.6.1', ''):
            self.assertFalse(parent.compiler_version_matches('slang', '2025.6.1', '', output))
        self.assertTrue(parent.compiler_version_matches('emscripten', '4.0.19', 'emcc tool 4.0.19 (revision)\n', ''))
        self.assertFalse(parent.compiler_version_matches('emscripten', '4.0.19', 'emcc tool 4.0.190\n', ''))


if __name__ == '__main__':
    unittest.main()
