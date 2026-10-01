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
import flow_gpu_probe


class FlowSoftwareBackendAdmission(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.icd = Path(self.temporary.name) / 'lvp_icd.json'
        self.icd.write_text(json.dumps({'file_format_version': '1.0.0', 'ICD': {
            'library_path': '/usr/lib/libvulkan_lvp.so', 'api_version': '1.3.0'}}))

    def software(self, path=None):
        with patch.object(sys, 'platform', 'linux'):
            return flow_gpu_probe.launch_options(None, False, True, path or self.icd)

    def test_explicit_lavapipe_sets_both_loader_variables_without_mutating_parent(self):
        before = dict(os.environ)
        options, metadata = self.software()
        self.assertEqual(options['env']['VK_ICD_FILENAMES'], str(self.icd.resolve()))
        self.assertEqual(options['env']['VK_DRIVER_FILES'], str(self.icd.resolve()))
        self.assertEqual(options['env'].get('PLAYWRIGHT_BROWSERS_PATH'), before.get('PLAYWRIGHT_BROWSERS_PATH'))
        self.assertEqual(options['env'].get('LD_LIBRARY_PATH'), before.get('LD_LIBRARY_PATH'))
        self.assertIn('--use-angle=vulkan', options['args'])
        self.assertIn('--use-vulkan=native', options['args'])
        self.assertIn('--use-webgpu-adapter=default', options['args'])
        self.assertNotIn('--use-angle=swiftshader', options['args'])
        self.assertEqual(metadata['requestedBackend'], 'mesa-lavapipe')
        self.assertEqual(options['channel'], 'chromium')
        self.assertEqual(metadata['browserDistribution'], 'chromium-new-headless')
        self.assertEqual(metadata['gpuMode'], 'software-WebGPU')
        self.assertEqual(metadata['icd']['sha256'], lab.sha256(self.icd))
        self.assertEqual(dict(os.environ), before)

    def test_caller_icd_is_selected_without_a_historical_browser_cache_override(self):
        with patch.dict(os.environ, {'VK_ICD_FILENAMES': str(self.icd)}), patch.object(sys, 'platform', 'linux'):
            options, metadata = flow_gpu_probe.launch_options(None, False, True)
        self.assertEqual(metadata['icd']['path'], str(self.icd.resolve()))
        self.assertIsNone(options['executable_path'])

    def test_hardware_and_explicit_browser_keep_the_existing_launch_path(self):
        executable = Path('/caller-selected/chromium')
        options, metadata = flow_gpu_probe.launch_options(executable, True, False)
        self.assertEqual(options, {'headless': True, 'executable_path': str(executable), 'args': []})
        self.assertEqual(metadata['requestedBackend'], 'hardware')
        self.assertEqual(metadata['gpuMode'], 'hardware')

    def test_explicit_software_browser_is_not_replaced_with_a_distribution_channel(self):
        executable = Path('/caller-selected/chromium')
        with patch.object(sys, 'platform', 'linux'):
            options, metadata = flow_gpu_probe.launch_options(executable, False, True, self.icd)
        self.assertEqual(options['executable_path'], str(executable))
        self.assertNotIn('channel', options)
        self.assertEqual(metadata['browserDistribution'], 'explicit-executable')
        self.assertIn('--use-webgpu-adapter=default', options['args'])

    def test_firefox_uses_supported_vulkan_preferences_and_preserves_explicit_executable(self):
        for executable in (None, Path('/caller-selected/firefox')):
            with self.subTest(executable=executable), patch.object(sys, 'platform', 'linux'):
                options, metadata = flow_gpu_probe.launch_options(executable, False, True, self.icd, 'firefox')
            self.assertEqual(metadata['browserEngine'], 'firefox')
            self.assertEqual(metadata['requestedBackend'], 'mesa-lavapipe')
            self.assertEqual(metadata['gpuMode'], 'WebGPU-backend-unreported')
            self.assertEqual(metadata['requestedGpuMode'], 'software-WebGPU')
            self.assertFalse(metadata['backendIdentityVerified'])
            self.assertEqual(options['args'], [])
            self.assertNotIn('channel', options)
            self.assertEqual(options['executable_path'], str(executable) if executable else None)
            self.assertEqual(options['firefox_user_prefs'], {'dom.webgpu.enabled': True,
                'dom.webgpu.wgpu-backend': 'vulkan', 'gfx.webgpu.ignore-blocklist': True})
            self.assertEqual(options['env']['VK_DRIVER_FILES'], str(self.icd.resolve()))

    def test_unknown_engine_and_firefox_without_explicit_software_mode_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported browser'):
            flow_gpu_probe.launch_options(None, False, True, self.icd, 'unknown-engine')
        with self.assertRaisesRegex(ValueError, 'requires --software-vulkan'):
            flow_gpu_probe.launch_options(None, False, False, browser_engine='firefox')

    def test_firefox_probe_reaches_ordinary_loopback_and_preserves_failed_capability(self):
        # A failing capability fixture tests runner routing; it is not GPU evidence.
        page = Mock()
        page.evaluate.return_value = {'status': 'FAIL', 'backendIdentityVerified': False,
            'observedBackend': 'unknown/redacted', 'error': 'Intentional no-execution fixture'}
        browser = SimpleNamespace(version='Fixture, not Firefox execution')
        context = SimpleNamespace(new_page=Mock(return_value=page), browser=browser, close=Mock())
        launch = Mock(return_value=context)
        manager = Mock()
        manager.__enter__ = Mock(return_value=SimpleNamespace(firefox=SimpleNamespace(launch_persistent_context=launch)))
        manager.__exit__ = Mock(return_value=False)
        module = SimpleNamespace(sync_playwright=lambda: manager)
        report = self.icd.parent / 'firefox-probe.json'
        with patch.object(sys, 'argv', ['flow_gpu_probe.py', '--software-vulkan', '--browser-engine', 'firefox', '--lavapipe-icd', str(self.icd), '--report', str(report)]), \
                patch.object(sys, 'platform', 'linux'), patch.dict(sys.modules, {'playwright.sync_api': module}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(flow_gpu_probe.main(), 1)
        result = json.loads(report.read_text())
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['gpuMode'], 'WebGPU-backend-unreported')
        self.assertEqual(result['requestedGpuMode'], 'software-WebGPU')
        self.assertFalse(result['backendIdentityVerified'])
        self.assertEqual(result['capability'], page.evaluate.return_value)
        self.assertEqual(context.new_page.call_count, 1)
        self.assertEqual(page.goto.call_count, 1)
        self.assertRegex(page.goto.call_args.args[0], r'^http://127\.0\.0\.1:\d+/upstream\.lock\.json$')
        context.close.assert_called_once()
        self.assertTrue(result['startupProfile']['contextClosed'])
        self.assertTrue(result['startupProfile']['profileRemoved'])

    def test_firefox_profile_prefs_exist_before_launch_and_context_closes_before_removal(self):
        options, _ = self.firefox_options()
        report = {}
        observed = {}
        browser = SimpleNamespace(version='Fixture, no browser execution')
        def close():
            self.assertTrue(observed['profile'].is_dir())
            observed['closed'] = True
        context = SimpleNamespace(browser=browser, close=Mock(side_effect=close))
        def launch(user_data_dir, **received):
            profile = observed['profile'] = Path(user_data_dir)
            observed['bytes'] = (profile / 'user.js').read_bytes()
            self.assertEqual(received, options)
            self.assertEqual(set(profile.iterdir()), {profile / 'user.js'})
            for key, value in options['firefox_user_prefs'].items():
                self.assertIn(f'user_pref({json.dumps(key)}, {json.dumps(value)});\n'.encode(), observed['bytes'])
            return context
        firefox = SimpleNamespace(launch_persistent_context=Mock(side_effect=launch))
        with flow_gpu_probe.launch_test_browser(SimpleNamespace(firefox=firefox), 'firefox', options, report) as actual:
            self.assertEqual(actual, (browser, context))
            self.assertFalse(report['startupProfile']['profileRemoved'])
        self.assertTrue(observed['closed'])
        self.assertFalse(observed['profile'].exists())
        self.assertEqual(report['startupProfile']['userJsSha256'], hashlib.sha256(observed['bytes']).hexdigest())
        self.assertTrue(report['startupProfile']['contextClosed'])
        self.assertTrue(report['startupProfile']['profileRemoved'])

    def firefox_options(self):
        with patch.object(sys, 'platform', 'linux'):
            return flow_gpu_probe.launch_options(None, False, True, self.icd, 'firefox')

    def test_firefox_owned_profile_is_removed_after_launch_or_body_failure(self):
        options, _ = self.firefox_options()
        for launch_failure in (True, False):
            with self.subTest(launch_failure=launch_failure):
                report = {}
                observed = {}
                context = SimpleNamespace(browser=SimpleNamespace(version='Fixture'), close=Mock())
                def launch(user_data_dir, **_):
                    observed['profile'] = Path(user_data_dir)
                    if launch_failure:
                        raise RuntimeError('Intentional fixture launch failure')
                    return context
                firefox = SimpleNamespace(launch_persistent_context=Mock(side_effect=launch))
                with self.assertRaisesRegex(RuntimeError, 'Intentional fixture'):
                    with flow_gpu_probe.launch_test_browser(SimpleNamespace(firefox=firefox), 'firefox', options, report):
                        raise RuntimeError('Intentional fixture body failure')
                self.assertFalse(observed['profile'].exists())
                self.assertTrue(report['startupProfile']['profileRemoved'])
                self.assertEqual(report['startupProfile']['contextClosed'], not launch_failure)
                self.assertEqual(context.close.call_count, 0 if launch_failure else 1)

    def test_chromium_shared_launcher_preserves_browser_ownership_and_closes_on_failure(self):
        options, _ = flow_gpu_probe.launch_options(Path('/caller/chromium'), True, False)
        browser = SimpleNamespace(close=Mock())
        launch = Mock(return_value=browser)
        report = {}
        with self.assertRaisesRegex(RuntimeError, 'Intentional fixture'):
            with flow_gpu_probe.launch_test_browser(SimpleNamespace(chromium=SimpleNamespace(launch=launch)), 'chromium', options, report) as actual:
                self.assertEqual(actual, (browser, browser))
                raise RuntimeError('Intentional fixture body failure')
        launch.assert_called_once_with(**options)
        browser.close.assert_called_once()
        self.assertNotIn('startupProfile', report)

    def test_mode_conflicts_and_unsupported_platform_fail_clearly(self):
        with self.assertRaisesRegex(ValueError, 'mutually exclusive'):
            flow_gpu_probe.launch_options(None, True, True)
        with self.assertRaisesRegex(ValueError, 'requires --software-vulkan'):
            flow_gpu_probe.launch_options(None, True, False, self.icd)
        with patch.object(sys, 'platform', 'win32'), self.assertRaisesRegex(ValueError, 'requires Linux'):
            flow_gpu_probe.launch_options(None, False, True, self.icd)

    def test_missing_nonfile_wrong_library_and_nonobject_icds_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'ordinary JSON'):
            self.software(self.icd.parent / 'missing.json')
        self.icd.unlink()
        self.icd.mkdir()
        with self.assertRaisesRegex(ValueError, 'ordinary JSON'):
            self.software()
        self.icd.rmdir()
        for descriptor in ([], {'ICD': []}, {'ICD': {'library_path': 'libvk_swiftshader.so'}}):
            self.icd.write_text(json.dumps(descriptor))
            with self.subTest(descriptor=descriptor), self.assertRaises(ValueError):
                self.software()

    def test_multiple_loader_descriptors_are_rejected(self):
        with patch.dict(os.environ, {'VK_ICD_FILENAMES': str(self.icd) + os.pathsep + str(self.icd)}), \
                patch.object(sys, 'platform', 'linux'), self.assertRaisesRegex(ValueError, 'exactly one'):
            flow_gpu_probe.launch_options(None, False, True)

    def test_failed_launch_receipt_records_backend_without_claiming_gpu_execution(self):
        launch = Mock(side_effect=RuntimeError('Intentional configuration-only stop; no GPU execution'))
        manager = Mock()
        manager.__enter__ = Mock(return_value=SimpleNamespace(chromium=SimpleNamespace(launch=launch)))
        manager.__exit__ = Mock(return_value=False)
        module = SimpleNamespace(sync_playwright=lambda: manager)
        report = self.icd.parent / 'probe.json'
        with patch.object(sys, 'argv', ['flow_gpu_probe.py', '--software-vulkan', '--lavapipe-icd', str(self.icd), '--report', str(report)]), \
                patch.object(sys, 'platform', 'linux'), patch.dict(sys.modules, {'playwright.sync_api': module}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(flow_gpu_probe.main(), 1)
        result = json.loads(report.read_text())
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['requestedBackend'], 'mesa-lavapipe')
        self.assertIn('configuration-only stop', result['error'])
        self.assertNotIn('capability', result)
        launch.assert_called_once()


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

    def test_linked_worktree_metadata_is_not_source_and_dirty_bytes_still_fail(self):
        worktree = self.root.parent / (self.root.name + '-linked')
        self.git('worktree', 'add', '--detach', str(worktree), 'HEAD')
        try:
            self.assertTrue((worktree / '.git').is_file())
            captured = release.inventory(worktree)
            self.assertEqual(set(captured), {'source.py'})
            with contextlib.redirect_stdout(io.StringIO()):
                revision = release.source_revision(captured, worktree)
            self.assertEqual(revision['commit'], self.git('rev-parse', 'HEAD'))
            self.assertEqual(revision['tree'], self.git('rev-parse', 'HEAD^{tree}'))
            (worktree / 'source.py').write_bytes(b'changed\n')
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(lab.LabError):
                release.source_revision(release.inventory(worktree), worktree)
        finally:
            self.git('worktree', 'remove', '--force', str(worktree))

    def test_windows_gitfile_override_is_scoped_and_normal_gitfiles_need_none(self):
        fixture = self.root / 'owned-metadata-fixture'
        fixture.mkdir()
        metadata = fixture / '.git'
        before = dict(os.environ)
        metadata.write_text('gitdir: C:/owned repository/.git/worktrees/linked\n', encoding='utf-8')
        environment = release.windows_worktree_git_environment(fixture)
        self.assertEqual(environment, before | {
            'GIT_DIR': '/mnt/c/owned repository/.git/worktrees/linked',
            'GIT_WORK_TREE': str(fixture.resolve())})
        self.assertEqual(dict(os.environ), before)
        for pointer in ('gitdir: ../repo/.git/worktrees/linked\n',
                        'gitdir: /absolute/unix/repo/.git/worktrees/linked\n'):
            metadata.write_text(pointer, encoding='utf-8')
            self.assertIsNone(release.windows_worktree_git_environment(fixture))
        self.assertIsNone(release.windows_worktree_git_environment(self.root))

    def test_git_environment_passthrough_does_not_change_unrelated_discovery(self):
        environment = dict(os.environ, GIT_DIR='owned-fixture', GIT_WORK_TREE='owned-worktree')
        with patch.object(lab, 'run', return_value=SimpleNamespace(stdout='fixture-result\n')) as ran:
            self.assertEqual(lab.git(self.root, 'rev-parse', 'HEAD', env=environment), 'fixture-result')
            self.assertIs(ran.call_args.kwargs['env'], environment)
            lab.git(self.root, 'rev-parse', 'HEAD')
            self.assertIsNone(ran.call_args.kwargs['env'])

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
