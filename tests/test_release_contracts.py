# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Admission regression tests; fixtures do not claim native or GPU execution."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import release
import bootstrap
import prepare_sources
import build
import physx_lab as lab


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
