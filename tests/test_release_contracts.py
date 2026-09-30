# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Admission regression tests; fixtures do not claim native or GPU execution."""
from __future__ import annotations

import contextlib
import hashlib
import io
import importlib.util
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import release
import bootstrap
import prepare_sources
import build
import physx_lab as lab


class PortableBrowserDiscovery(unittest.TestCase):
    def exercise(self, cache: str | None, executable: str | None):
        specification = importlib.util.spec_from_file_location(
            'flow_wgsl_runner_admission', ROOT / 'addons/flow/wgsl_browser_test.py')
        runner = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(runner)
        launch = Mock(side_effect=RuntimeError('Intentional configuration-only stop; no GPU execution'))
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        manager = Mock()
        manager.__enter__ = Mock(return_value=playwright)
        manager.__exit__ = Mock(return_value=False)
        module = SimpleNamespace(sync_playwright=lambda: manager)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'fixture.wgsl').write_text('// Discovery fixture; intentionally never executed.\n')
            report = directory / 'result.json'
            argv = ['wgsl_browser_test.py', '--shader-dir', str(directory), '--report', str(report), '--modules-only']
            if executable:
                argv += ['--browser-executable', executable]
            with patch.dict(os.environ, {}, clear=False), patch.dict(sys.modules, {'playwright.sync_api': module}), \
                    patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
                if cache is None:
                    os.environ.pop('PLAYWRIGHT_BROWSERS_PATH', None)
                else:
                    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = cache
                self.assertEqual(runner.main(), 2)
                self.assertEqual(os.environ.get('PLAYWRIGHT_BROWSERS_PATH'), cache)
                result = json.loads(report.read_text())
                self.assertEqual(result['status'], 'BLOCKED_OR_FAILED')
                self.assertIn('configuration-only stop', result['error'])
            launch.assert_called_once()
            self.assertEqual(launch.call_args.kwargs['executable_path'], executable)
            self.assertIn('--use-angle=swiftshader', launch.call_args.kwargs['args'])

    def test_default_playwright_cache_is_used_without_an_environment_override(self):
        self.exercise(None, None)

    def test_caller_cache_and_explicit_executable_are_preserved(self):
        self.exercise('/caller-selected/browser-cache', str(ROOT / 'work/custom-browser'))


class PackmanPythonAdmission(unittest.TestCase):
    def test_real_isolated_interpreter_can_decompress_and_overrides_stale_python(self):
        before = dict(os.environ)
        with patch.dict(os.environ, {'PM_PYTHON_EXT': 'unusable-stale-interpreter',
                                     'PYTHONHOME': 'unusable-isolated-python-home'}):
            with contextlib.redirect_stdout(io.StringIO()), patch.object(build, 'record') as recorded:
                environment = build.project_generation_env()
            self.assertEqual(environment['PM_PYTHON_EXT'], sys.executable)
            self.assertEqual(environment.get('LD_LIBRARY_PATH'), os.environ.get('LD_LIBRARY_PATH'))
            recorded.assert_called_once()
            report_name, receipt = recorded.call_args.args
            self.assertEqual(report_name, 'packman-python-preflight.json')
            self.assertEqual(receipt['status'], 'EXTERNAL_PYTHON_ZLIB_ZIP_PASSED')
            self.assertEqual(Path(receipt['executable']).resolve(), Path(sys.executable).resolve())
            self.assertTrue(receipt['zlibRuntimeVersion'])
        self.assertEqual(dict(os.environ), before)


class CommittedSourceAdmission(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source.py'
        self.source.write_bytes(b'print("fixture")\n')
        self.git('init')
        self.git('config', 'core.autocrlf', 'false')
        self.git('config', 'user.name', 'Admission test')
        self.git('config', 'user.email', 'admission@example.invalid')
        self.git('add', 'source.py')
        self.git('commit', '-m', 'Fixture')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()

    def revision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return release.source_revision(release.inventory(self.root), self.root)

    def test_clean_bytes_bind_commit_tree_and_canonical_inventory(self):
        result = self.revision()
        self.assertEqual(result['commit'], self.git('rev-parse', 'HEAD'))
        self.assertEqual(result['tree'], self.git('rev-parse', 'HEAD^{tree}'))
        digest = hashlib.sha256(json.dumps(release.inventory(self.root), sort_keys=True,
                                         separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(result['inventorySha256'], digest)

    def test_dirty_source_is_rejected(self):
        self.source.write_bytes(b'changed\n')
        with self.assertRaises(lab.LabError):
            self.revision()

    def test_dirty_index_is_rejected(self):
        self.source.write_bytes(b'changed\n')
        self.git('add', 'source.py')
        with self.assertRaises(lab.LabError):
            self.revision()

    def test_assume_unchanged_cannot_hide_changed_source_bytes(self):
        self.git('update-index', '--assume-unchanged', 'source.py')
        self.source.write_bytes(b'changed\n')
        self.assertEqual(self.git('status', '--porcelain'), '')
        with self.assertRaisesRegex(lab.LabError, 'committed object'):
            self.revision()

    def test_untracked_source_is_rejected(self):
        (self.root / 'untracked.py').write_bytes(b'untracked\n')
        with self.assertRaises(lab.LabError):
            self.revision()


class SlangToolReadmission(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / 'fixture.tar.gz'
        self.tools = self.root / 'tools'
        self.tools.mkdir()
        with tarfile.open(self.archive, 'w:gz') as bundle:
            for name in ('bin/slangc', 'lib/libslang.so'):
                content = ('tool fixture ' + name).encode()
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mode = 0o755
                bundle.addfile(info, io.BytesIO(content))
        with tarfile.open(self.archive) as bundle:
            bundle.extractall(self.tools, filter='data')

    def test_all_compiler_and_library_bytes_are_checked(self):
        self.assertEqual(set(bootstrap.verify_slang_tree(self.archive, self.tools)),
                         {'bin/slangc', 'lib/libslang.so'})

    def test_changed_library_is_rejected(self):
        (self.tools / 'lib/libslang.so').write_bytes(b'changed')
        with self.assertRaisesRegex(lab.LabError, 'file changed'):
            bootstrap.verify_slang_tree(self.archive, self.tools)

    def test_unexpected_tool_is_rejected(self):
        (self.tools / 'lib/unexpected.so').write_bytes(b'extra')
        with self.assertRaisesRegex(lab.LabError, 'inventory differs'):
            bootstrap.verify_slang_tree(self.archive, self.tools)


class ShaderInventoryAdmission(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'dist/flow-wgsl'
        self.output.mkdir(parents=True)
        self.rows = []
        for index in range(97):
            row = {'status': 'PASS'}
            for key, suffix in (('wgsl', '.wgsl'), ('reflection', '.json')):
                path = self.output / f'{index:03d}{suffix}'
                path.write_bytes(b'Inventory fixture; no shader execution claim.\n')
                row[key] = path.name
                row[key + 'Sha256'] = lab.sha256(path)
            self.rows.append(row)
        self.write()

    def write(self):
        (self.output / 'manifest.json').write_text(json.dumps({
            'status': 'FLOW_WGSL_CORPUS_COMPILED', 'shaderCount': 97, 'shaders': self.rows}))

    def check(self):
        with patch.object(release, 'ROOT', self.root):
            return release.shader_inventory()

    def test_exact_97_unique_pairs_are_admitted(self):
        self.assertEqual(len(self.check()), 195)

    def test_declared_count_cannot_hide_duplicate_rows(self):
        self.rows[-1] = dict(self.rows[0])
        self.write()
        with self.assertRaisesRegex(lab.LabError, 'unique'):
            self.check()

    def test_declared_count_cannot_hide_missing_rows(self):
        self.rows.pop()
        self.write()
        with self.assertRaisesRegex(lab.LabError, 'exactly 97'):
            self.check()


class PreparedSourceReadmission(unittest.TestCase):
    def test_observed_eight_sdk_archives_are_admitted_without_a_general_bin_exception(self):
        target = ROOT / 'work/candidate/PhysX'
        if not (target / '.git').is_dir():
            self.skipTest('Requires the separately prepared pinned upstream checkout')
        library_root = target / 'physx/bin/UNKNOWN/release'
        library_root.mkdir(parents=True, exist_ok=True)
        created = []
        try:
            # On CI these are the real SDK build outputs and stay untouched.
            # A source-only checkout uses explicit file-layout fixtures here.
            for name in ('libPhysXCharacterKinematic_static.a', 'libPhysXCommon_static.a',
                         'libPhysXCooking_static.a', 'libPhysXExtensions_static.a',
                         'libPhysXFoundation_static.a', 'libPhysXPvdSDK_static.a',
                         'libPhysXVehicle_static.a', 'libPhysX_static.a'):
                archive = library_root / name
                if not archive.exists():
                    archive.write_bytes(b'Archive-layout fixture; no compiled SDK claim.\n')
                    created.append(archive)
            with contextlib.redirect_stdout(io.StringIO()):
                prepare_sources.prepare()
            for name in ('libUnexpected_static.a', 'unexpected.cpp'):
                unexpected = library_root / name
                previous = unexpected.read_bytes() if unexpected.exists() else None
                try:
                    unexpected.write_bytes(b'Unowned bin input.\n')
                    with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(lab.LabError, 'Unexpected upstream'):
                        prepare_sources.prepare()
                finally:
                    if previous is None:
                        unexpected.unlink()
                    else:
                        unexpected.write_bytes(previous)
        finally:
            for archive in created:
                archive.unlink()

    def test_only_exact_generated_output_trees_are_admitted(self):
        target = ROOT / 'work/candidate/PhysX'
        if not (target / '.git').is_dir():
            self.skipTest('Requires the separately prepared pinned upstream checkout')
        for name in ('release', 'checked', 'debug', 'profile'):
            output = target / 'physx/compiler' / ('emscripten-' + name) / 'CMakeFiles/admission-fixture.txt'
            previous = output.read_bytes() if output.exists() else None
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                output.write_bytes(b'Generated-output admission fixture.\n')
                with contextlib.redirect_stdout(io.StringIO()):
                    prepare_sources.prepare()
            finally:
                if previous is None:
                    output.unlink()
                else:
                    output.write_bytes(previous)
        unexpected = target / 'physx/compiler/unexpected-admission-output.txt'
        previous = unexpected.read_bytes() if unexpected.exists() else None
        try:
            unexpected.write_bytes(b'Unowned compiler-root input.\n')
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(lab.LabError, 'Unexpected upstream'):
                prepare_sources.prepare()
        finally:
            if previous is None:
                unexpected.unlink()
            else:
                unexpected.write_bytes(previous)

    def test_unexpected_sources_and_changed_owned_inputs_are_rejected(self):
        target = ROOT / 'work/candidate/PhysX'
        if not (target / '.git').is_dir():
            self.skipTest('Requires the separately prepared pinned upstream checkout')
        linkage = 'physx/source/compiler/cmake/emscripten/PhysXWasmBindings.cmake'
        base = prepare_sources.added_linkage(ROOT / 'patches/browser-overlay.patch', linkage)
        generated = build.unified_linkage(base, target).encode('utf-8')
        cases = {
            'blast/VERSION.md': b'changed upstream input\n',
            'physx/source/webidlbindings/src/common/PrWasmStreams.h': b'changed stream\n',
            linkage: generated + b'\n# unexpected command\n',
            'physx/source/webidlbindings/pr_rust_bulk/pr_bulk_rust.cpp': b'changed bulk\n',
            'unexpected-source-input.cpp': b'new unreviewed source\n',
        }
        for relative, content in cases.items():
            with self.subTest(path=relative):
                destination = target / relative
                previous = destination.read_bytes() if destination.exists() else None
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    destination.write_bytes(content)
                    with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(lab.LabError):
                        prepare_sources.prepare()
                finally:
                    if previous is None:
                        destination.unlink()
                    else:
                        destination.write_bytes(previous)
        cmake = target / linkage
        previous = cmake.read_bytes()
        try:
            cmake.write_bytes(generated)
            with contextlib.redirect_stdout(io.StringIO()):
                prepare_sources.prepare()
        finally:
            cmake.write_bytes(previous)
        with contextlib.redirect_stdout(io.StringIO()):
            prepare_sources.prepare()


if __name__ == '__main__':
    unittest.main()
