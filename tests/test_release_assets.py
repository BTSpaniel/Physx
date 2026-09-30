# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Release asset/visibility contracts using explicit non-runtime fixture bytes."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import package_release
import publish_release


class ReleaseFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.version = '5.11.0-alpha.2'
        self.tag = 'v' + self.version
        self.revision = {'commit': 'a' * 40, 'tree': 'b' * 40, 'inventorySha256': 'c' * 64}
        self.archive = self.directory / f'physx-pe-{self.version}-runtime.zip'
        self.bytes = {'dist/candidate/physx-pe.wasm': b'Uncompiled WASM fixture, never executed.',
                      'dist/candidate/physx-pe.mjs': b'Loader contract fixture, never executed.',
                      'dist/candidate/physx-pe.d.ts': b'Declaration contract fixture.'}
        self.verification = {'sourceRevision': self.revision, 'sourceInventorySha256': self.revision['inventorySha256'],
                             'status': 'BUILD_AND_BROWSER_SMOKE_PASSED_ALPHA',
                             'scope': 'Explicit test fixture; no build/browser/GPU execution.'}
        self.bytes['reports/verification.json'] = json.dumps(self.verification).encode()
        self.manifest = {'version': self.version, 'sdkVersion': '5.11.0', 'sourceRevision': self.revision,
                         'files': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                                   for name, data in self.bytes.items()}}
        self.write_archive()
        self.metadata = package_release.write_release_assets(self.directory, self.archive, self.version,
            self.revision, self.verification['scope'], 'd' * 64)

    def write_archive(self):
        with zipfile.ZipFile(self.archive, 'w') as bundle:
            for name, data in self.bytes.items():
                bundle.writestr(name, data)
            bundle.writestr('runtime-manifest.json', json.dumps(self.manifest))

    def write_metadata(self):
        (self.directory / 'release-artifacts.json').write_text(json.dumps(self.metadata))

    def admitted(self):
        return publish_release.checked_payload(self.directory, self.tag, self.revision['commit'])


class ReleaseAssets(ReleaseFixture):
    def test_local_completed_output_is_atomic_and_an_identical_retry_is_idempotent(self):
        output = self.directory / 'installed'
        staged = self.directory / 'staged'
        staged.mkdir()
        (staged / 'physx-pe.wasm').write_bytes(b'Immutable output fixture')
        package_release.install_release_output(staged, output)
        self.assertFalse(staged.exists())
        self.assertEqual((output / 'physx-pe.wasm').read_bytes(), b'Immutable output fixture')
        staged.mkdir()
        (staged / 'physx-pe.wasm').write_bytes(b'Immutable output fixture')
        package_release.install_release_output(staged, output)
        self.assertTrue(staged.exists())
        self.assertEqual((output / 'physx-pe.wasm').read_bytes(), b'Immutable output fixture')

    def test_changed_or_extra_local_output_preserves_all_previous_bytes(self):
        output = self.directory / 'installed'
        staged = self.directory / 'staged'
        output.mkdir()
        staged.mkdir()
        (output / 'physx-pe.wasm').write_bytes(b'Original output fixture')
        (staged / 'physx-pe.wasm').write_bytes(b'Different output fixture')
        with self.assertRaisesRegex(package_release.lab.LabError, 'refusing to overwrite'):
            package_release.install_release_output(staged, output)
        self.assertEqual((output / 'physx-pe.wasm').read_bytes(), b'Original output fixture')
        (staged / 'physx-pe.wasm').write_bytes(b'Original output fixture')
        (output / 'unowned-directory').mkdir()
        with self.assertRaisesRegex(package_release.lab.LabError, 'refusing to overwrite'):
            package_release.install_release_output(staged, output)
        self.assertTrue((output / 'unowned-directory').is_dir())
        self.assertEqual((output / 'physx-pe.wasm').read_bytes(), b'Original output fixture')

    def test_direct_files_are_exact_archive_bytes_and_all_seven_assets_are_admitted(self):
        metadata, files = self.admitted()
        self.assertEqual(metadata['schema'], 'physx-pe.release-artifacts/v2')
        self.assertEqual({path.name for path in files}, {self.archive.name, self.archive.name + '.sha256',
            'release-artifacts.json', 'SHA256SUMS', 'physx-pe.wasm', 'physx-pe.mjs', 'physx-pe.d.ts'})
        for name, entry in metadata['directAssets'].items():
            self.assertEqual((self.directory / name).read_bytes(), self.bytes[entry['archivePath']])

    def test_changed_direct_asset_is_rejected_before_any_remote_request(self):
        (self.directory / 'physx-pe.wasm').write_bytes(b'Changed direct fixture')
        with patch.object(publish_release, 'request') as request, self.assertRaisesRegex(ValueError, 'Direct asset differs'):
            self.admitted()
        request.assert_not_called()

    def test_direct_asset_metadata_cannot_select_another_archive_entry(self):
        self.metadata['directAssets']['physx-pe.wasm']['archivePath'] = 'dist/candidate/physx-pe.mjs'
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, 'Direct asset identity'):
            self.admitted()

    def test_outer_runtime_manifest_hash_binds_the_complete_shader_and_receipt_inventory(self):
        self.metadata['runtimeManifest']['sha256'] = 'f' * 64
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, 'runtime manifest binding'):
            self.admitted()

    def test_direct_asset_inventory_cannot_omit_types_or_add_an_unverified_file(self):
        for additional in (False, True):
            with self.subTest(additional=additional):
                original = dict(self.metadata['directAssets'])
                if additional:
                    self.metadata['directAssets']['unverified.txt'] = dict(original['physx-pe.d.ts'])
                else:
                    del self.metadata['directAssets']['physx-pe.d.ts']
                self.write_metadata()
                with self.assertRaisesRegex(ValueError, 'matched WASM'):
                    self.admitted()
                self.metadata['directAssets'] = original

    def test_rehashed_wrong_checksums_still_fail_the_exact_asset_set(self):
        path = self.directory / 'SHA256SUMS'
        path.write_bytes(b'Unrelated checksum fixture\n')
        self.metadata['checksums'].update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, 'SHA256SUMS disagrees'):
            self.admitted()

    def test_archive_and_commit_mismatches_remain_rejected(self):
        with self.assertRaisesRegex(ValueError, 'source revision'):
            publish_release.checked_payload(self.directory, self.tag, 'f' * 40)
        with self.archive.open('ab') as archive:
            archive.write(b'Changed archive fixture')
        with self.assertRaisesRegex(ValueError, 'archive differs'):
            self.admitted()

    def test_legacy_v1_archive_admission_preserves_the_original_three_asset_contract(self):
        self.version = '5.11.0-alpha.1'
        self.tag = 'v' + self.version
        self.archive = self.directory / f'physx-pe-{self.version}.zip'
        self.manifest['version'] = self.version
        self.write_archive()
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        (self.directory / (self.archive.name + '.sha256')).write_text(digest + '  ' + self.archive.name + '\n')
        self.metadata = {'schema': 'physx-pe.release-artifacts/v1', 'version': self.version,
            'status': 'VERIFIED_ALPHA_ARCHIVE', 'sourceRevision': self.revision,
            'archive': {'name': self.archive.name, 'bytes': self.archive.stat().st_size, 'sha256': digest}}
        self.write_metadata()
        _, files = self.admitted()
        self.assertEqual([path.name for path in files], [self.archive.name, self.archive.name + '.sha256', 'release-artifacts.json'])

    def test_asset_names_and_nonfiles_cannot_escape_the_payload(self):
        for name in ('../physx-pe.wasm', '/physx-pe.wasm', 'folder\\file.wasm', '.', 'missing.wasm'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                publish_release.ordinary_asset(self.directory, name)


class ReleasePublication(ReleaseFixture):
    def remote(self, private=True, draft=True, existing=None):
        metadata, files = self.admitted()
        payload = {path.name: path.read_bytes() for path in files}
        release = {'id': 1, 'tag_name': self.tag, 'target_commitish': self.revision['commit'],
                   'name': 'PhysX PE ' + self.version, 'prerelease': True, 'draft': draft,
                   'upload_url': 'https://uploads.github.com/repos/BTSpaniel/Physx/releases/1/assets{?name,label}',
                   'html_url': 'https://github.com/BTSpaniel/Physx/releases/tag/' + self.tag}
        assets = [{'name': name, 'size': len(data), 'state': 'uploaded',
                   'digest': 'sha256:' + hashlib.sha256(data).hexdigest()} for name, data in payload.items()]
        if existing is not None:
            assets = existing(assets)
        def request(url, token, *, method='GET', data=None, content_type=None):
            self.assertEqual(token, 'Fixture token, not credentials')
            if url == 'https://api.github.com/repos/BTSpaniel/Physx':
                return {'private': private}
            if '/releases?per_page=' in url:
                return [release]
            if '/assets?per_page=' in url:
                return assets
            if method == 'POST' and url.startswith('https://uploads.github.com/'):
                from urllib.parse import parse_qs, urlsplit
                name = parse_qs(urlsplit(url).query)['name'][0]
                self.assertEqual(data, payload[name])
                self.assertEqual(content_type, {self.archive.name: 'application/zip',
                    self.archive.name + '.sha256': 'text/plain', 'release-artifacts.json': 'application/json',
                    'SHA256SUMS': 'text/plain', 'physx-pe.wasm': 'application/wasm',
                    'physx-pe.mjs': 'text/javascript', 'physx-pe.d.ts': 'text/plain'}[name])
                return {'name': name, 'size': len(data), 'state': 'uploaded',
                        'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}
            if method == 'PATCH':
                self.assertEqual(json.loads(data), {'draft': False, 'prerelease': True})
                return {**release, 'draft': False}
            self.fail('Unexpected fixture API call: ' + method + ' ' + url)
        return Mock(side_effect=request)

    def publish(self, request, visibility=None):
        environment = {'GITHUB_REPOSITORY': 'BTSpaniel/Physx', 'GITHUB_SHA': self.revision['commit'],
                       'GITHUB_TOKEN': 'Fixture token, not credentials'}
        with patch.dict(os.environ, environment), patch.object(publish_release, 'request', request), \
                contextlib.redirect_stdout(io.StringIO()):
            if visibility is None:
                publish_release.publish(self.directory, self.tag)
            else:
                publish_release.publish(self.directory, self.tag, visibility)

    def test_public_mode_is_explicit_and_default_private_fails_closed(self):
        request = self.remote(private=False, draft=False)
        with self.assertRaisesRegex(ValueError, 'visibility does not match'):
            self.publish(request)
        self.assertTrue(all(call.kwargs.get('method', 'GET') == 'GET' for call in request.call_args_list))
        request.reset_mock()
        self.publish(request, 'public')
        self.assertTrue(all(call.kwargs.get('method', 'GET') == 'GET' for call in request.call_args_list))

    def test_public_mode_refuses_a_private_repository_without_settings_mutation(self):
        request = self.remote(private=True, draft=False)
        with self.assertRaisesRegex(ValueError, 'visibility does not match'):
            self.publish(request, 'public')
        self.assertEqual(request.call_count, 1)

    def test_matching_published_release_is_idempotent_in_private_default_mode(self):
        request = self.remote(draft=False)
        self.publish(request)
        self.assertTrue(all(call.kwargs.get('method', 'GET') == 'GET' for call in request.call_args_list))

    def test_mismatched_existing_draft_asset_prevents_all_uploads(self):
        def existing(assets):
            # Archive is missing, but a later named asset is mismatched: no upload may occur.
            return [{**asset, 'digest': 'sha256:' + 'e' * 64} for asset in assets if asset['name'] == 'physx-pe.wasm']
        request = self.remote(existing=existing)
        with self.assertRaisesRegex(ValueError, 'digest disagrees'):
            self.publish(request)
        self.assertTrue(all(call.kwargs.get('method', 'GET') == 'GET' for call in request.call_args_list))

    def test_missing_published_asset_is_refused_without_upload_or_patch(self):
        request = self.remote(draft=False, existing=lambda assets: assets[:-1])
        with self.assertRaisesRegex(ValueError, 'missing an asset'):
            self.publish(request)
        self.assertTrue(all(call.kwargs.get('method', 'GET') == 'GET' for call in request.call_args_list))

    def test_matching_partial_draft_uploads_only_missing_bytes_then_publishes(self):
        request = self.remote(existing=lambda assets: assets[:1])
        self.publish(request)
        self.assertEqual(sum(call.kwargs.get('method') == 'POST' for call in request.call_args_list), 6)
        self.assertEqual(sum(call.kwargs.get('method') == 'PATCH' for call in request.call_args_list), 1)

    def test_empty_draft_uploads_all_seven_matched_assets_with_their_correct_mime_types(self):
        request = self.remote(existing=lambda assets: [])
        self.publish(request)
        self.assertEqual(sum(call.kwargs.get('method') == 'POST' for call in request.call_args_list), 7)
        self.assertEqual(sum(call.kwargs.get('method') == 'PATCH' for call in request.call_args_list), 1)

    def test_duplicate_or_unexpected_remote_asset_inventory_is_refused(self):
        for existing in (lambda assets: assets + [assets[0]],
                         lambda assets: assets + [{**assets[0], 'name': 'unverified.bin'}]):
            with self.subTest(existing=existing):
                request = self.remote(existing=existing)
                with self.assertRaisesRegex(ValueError, 'ambiguous or unexpected'):
                    self.publish(request)
                self.assertTrue(all(call.kwargs.get('method', 'GET') == 'GET' for call in request.call_args_list))

    def test_inventory_pagination_includes_assets_past_the_first_page(self):
        rows = [{'name': str(index)} for index in range(100)]
        request = Mock(side_effect=[rows, [{'name': 'last'}]])
        with patch.object(publish_release, 'request', request):
            actual = publish_release.paginated('https://api.github.com/repos/BTSpaniel/Physx/releases', 'Fixture')
        self.assertEqual(len(actual), 101)
        self.assertIn('page=2', request.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
