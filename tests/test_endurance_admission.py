# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Synthetic receipt admission guards; these tests never claim native execution."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import browser_compat_endurance as runner
import evidence
import test_release_assets as release_asset_tests


def receipt_fixture():
    now = datetime.now(timezone.utc)
    artifacts = {name: {'bytes': 128, 'sha256': 'a' * 64} for name in ('physx-pe.mjs', 'physx-pe.wasm')}
    manifest = {'artifacts': artifacts, 'expected_runtime_version': '5.11.0',
                'staged_utc': (now - timedelta(seconds=2)).isoformat()}
    sources = {name: 'b' * 64 for name in runner.ENDURANCE_SOURCES}
    input_data = {'seed': 1337, 'bodies': 8, 'dt': 1 / 120, 'steps': 72000, 'sampleEvery': 30,
        'initialLayout': '1.5 m four-column grid', 'impulseEverySteps': 120,
        'impulses': [[.4, 2.5, .2], [-.4, 2.5, -.2]], 'impulseBody': 'floor(step/120) mod 8',
        'impulseChoice': 'floor(step/120) mod 2'}
    lifecycle = {'cycles': 256, 'warmupCycles': 32, 'scenesCreatedAndReleased': 288,
        'dynamicActorsCreatedAndReleased': 4608, 'bulkContextsCreatedAndReleased': 576,
        'blastFractureSmokeRuns': 288, 'maximumActiveActors': 17,
        'allocationProbeSamples': 256, 'tailSamples': 128,
        'activeActorsAfterEveryClose': [0], 'retiredBulkStatus': [-1],
        'liveBefore': dict(runner.ZERO_ADDONS), 'liveAfter': dict(runner.ZERO_ADDONS),
        'wasmHeapBytesTail': [67108864],
        'allocationReuse': [{'bytes': size, 'addresses': [size + 4096]} for size in (16, 256, 4096, 65536, 1048576)]}
    replay = {'input': input_data,
        'inputSha256': hashlib.sha256(json.dumps(input_data, separators=(',', ':')).encode()).hexdigest(),
        'physicalSecondsPerRun': 600, 'stepsPerRun': 72000, 'sampleCountPerRun': 2400,
        'sampledComponents': 134400, 'trajectoryBytes': 537600,
        'differentBytes': 0, 'maxPoseDelta': 0, 'remainingActors': [0, 0],
        'trajectorySha256': 'c' * 64, 'maxQuaternionError': .000001,
        'allocationAfterReplay': {'heapBytes': 67108864}}
    cleanup = {'ownedScenes': 0, 'addonLiveCounters': dict(runner.ZERO_ADDONS), 'sdkReleased': True, 'sdkStderr': 0}
    result = {'status': 'CPU_BROWSER_AND_BOUNDED_ENDURANCE_PASSED', 'releasePhase': True,
        'releaseApproved': False, 'pageErrors': [], 'cpuBrowserArgs': ['--disable-gpu'], 'browserClosed': True,
        'artifactHashes': artifacts, 'sourceSha256Before': sources, 'sourceSha256After': dict(sources),
        'requestedConfiguration': {'cycles': 256, 'physicalSeconds': 600},
        'endurance': {'status': 'BOUNDED_CPU_ENDURANCE_PASSED', 'physicsExecuted': True,
            'releaseApproved': False, 'engineIntegrationVerified': False, 'artifactHashes': artifacts, 'stderr': [],
            'startedUtc': (now - timedelta(seconds=1)).isoformat(), 'finishedUtc': now.isoformat(),
            'driverWorkerCleanup': {'terminationInvoked': True, 'blobUrlRevoked': True},
            'tests': [{'name': name, 'status': 'PASS', 'ms': 1, 'detail': detail}
                      for name, detail in zip(runner.ENDURANCE_CASES, (lifecycle, replay, cleanup))]}}
    shared = {'sha256': 'd' * 64, 'environmentOverridden': False}
    result['sharedPerformanceLock'] = shared
    result['executionIsolation'] = {'mode': 'shared-host-lock', 'sharedPerformanceLock': shared}
    result['functional'] = {'profile': 'candidate', 'status': 'SMOKE_PASSED_NOT_RELEASE_CERTIFIED',
        'physicsExecuted': True, 'releaseApproved': False, 'engineIntegrationVerified': False,
        'stderr': [], 'pageErrors': [], 'artifactHashes': artifacts,
        'testHarnessSha256': {name: sources[name] for name in evidence.HARNESS},
        'started': (now - timedelta(seconds=1)).isoformat(), 'finished': now.isoformat(),
        'tests': [{'name': name, 'status': 'PASS', 'ms': 1,
                   'detail': {'runtimeVersion': '5.11.0'} if name == 'Runtime version and required WebIDL API' else {}}
                  for name in evidence.CORE_TESTS]}
    return result, manifest, sources


class BoundedEnduranceAdmission(unittest.TestCase):
    def setUp(self):
        self.report, self.manifest, self.sources = receipt_fixture()

    def admit(self):
        return runner.validate_endurance(self.report, self.manifest, self.sources)

    def test_exact_bounded_fixture_accepts_without_release_or_leak_certification(self):
        accepted = self.admit()
        self.assertEqual(accepted['testsAccepted'], 3)
        self.assertFalse(accepted['releaseApproved'])
        self.assertFalse(accepted['fullAllocatorLeakProof'])
        self.assertIn('full native allocator accounting', accepted['stillUnproven'])

    def test_missing_duplicate_failed_and_export_only_cases_reject(self):
        original = copy.deepcopy(self.report)
        variants = [[], original['endurance']['tests'][:-1],
                    [original['endurance']['tests'][0]] * 3,
                    [{'name': 'Native exports exist', 'status': 'PASS', 'ms': 1, 'detail': {}}]]
        for rows in variants:
            with self.subTest(rows=rows):
                self.report = copy.deepcopy(original)
                self.report['endurance']['tests'] = rows
                with self.assertRaises(ValueError): self.admit()
        self.report = original
        self.report['endurance']['tests'][0]['status'] = 'SKIP'
        with self.assertRaises(ValueError): self.admit()

    def test_modified_sources_and_artifact_pair_reject(self):
        self.report['sourceSha256After']['release.py'] = 'd' * 64
        with self.assertRaisesRegex(ValueError, 'sources changed'): self.admit()
        self.setUp()
        self.report['artifactHashes'] = {}
        with self.assertRaisesRegex(ValueError, 'native pair'): self.admit()

    def test_active_actor_retired_context_and_native_addon_ownership_reject(self):
        life = self.report['endurance']['tests'][0]['detail']
        for field, value in (('activeActorsAfterEveryClose', [1]), ('retiredBulkStatus', [0]),
                             ('liveAfter', {**runner.ZERO_ADDONS, 'blastFamilies': 1}),
                             ('liveAfter', {**runner.ZERO_ADDONS, 'blastFamilies': False})):
            with self.subTest(field=field, value=value):
                original = copy.deepcopy(life[field]); life[field] = value
                with self.assertRaises(ValueError): self.admit()
                life[field] = original

    def test_partial_cycle_counts_and_missing_blast_lifecycle_reject(self):
        life = self.report['endurance']['tests'][0]['detail']
        for field in ('scenesCreatedAndReleased', 'bulkContextsCreatedAndReleased', 'blastFractureSmokeRuns'):
            with self.subTest(field=field):
                life[field] -= 1
                with self.assertRaises(ValueError): self.admit()
                life[field] += 1

    def test_unbounded_heap_or_unreused_probe_addresses_reject(self):
        life = self.report['endurance']['tests'][0]['detail']
        life['wasmHeapBytesTail'].append(134217728)
        with self.assertRaisesRegex(ValueError, 'high-water'): self.admit()
        life['wasmHeapBytesTail'].pop()
        life['allocationReuse'][0]['addresses'] = [10, 20, 30, 40, 50]
        with self.assertRaisesRegex(ValueError, 'address reuse'): self.admit()

    def test_shorter_physical_clock_or_modified_impulse_inputs_reject(self):
        replay = self.report['endurance']['tests'][1]['detail']
        replay['stepsPerRun'] = 120
        with self.assertRaisesRegex(ValueError, 'duration/coverage'): self.admit()
        replay['stepsPerRun'] = 72000
        replay['input']['dt'] = 1 / 60
        with self.assertRaisesRegex(ValueError, 'physical inputs'): self.admit()

    def test_drift_nonfinite_orientation_and_replay_memory_growth_reject(self):
        replay = self.report['endurance']['tests'][1]['detail']
        for field, value in (('differentBytes', 1), ('maxPoseDelta', .001),
                             ('maxQuaternionError', float('nan')), ('maxQuaternionError', .02),
                             ('allocationAfterReplay', {'heapBytes': 134217728})):
            with self.subTest(field=field):
                old = replay[field]; replay[field] = value
                with self.assertRaises(ValueError): self.admit()
                replay[field] = old

    def test_worker_browser_sdk_teardown_and_diagnostics_are_required(self):
        self.report['browserClosed'] = False
        with self.assertRaises(ValueError): self.admit()
        self.setUp(); self.report['endurance']['driverWorkerCleanup']['terminationInvoked'] = False
        with self.assertRaises(ValueError): self.admit()
        self.setUp(); self.report['endurance']['tests'][2]['detail']['sdkReleased'] = False
        with self.assertRaises(ValueError): self.admit()
        self.setUp(); self.report['endurance']['stderr'] = ['Native error']
        with self.assertRaises(ValueError): self.admit()

    def test_stale_future_and_earlier_invocation_receipts_reject(self):
        self.report['endurance']['startedUtc'] = '2020-01-01T00:00:00+00:00'
        with self.assertRaisesRegex(ValueError, 'stale'): self.admit()
        self.setUp(); self.report['endurance']['finishedUtc'] = '2999-01-01T00:00:00+00:00'
        with self.assertRaisesRegex(ValueError, 'future'): self.admit()
        self.setUp()
        with self.assertRaisesRegex(ValueError, 'pipeline invocation|predates'):
            runner.validate_endurance(self.report, self.manifest, self.sources, not_before=datetime.now(timezone.utc))

    def test_release_minimum_cannot_be_reduced_or_replaced_with_boolean(self):
        for cycles, seconds in ((64, 600), (256, 60), (True, 600), (256, True)):
            with self.subTest(cycles=cycles, seconds=seconds), self.assertRaises(ValueError):
                runner.validate_configuration(cycles, seconds, True)

    def test_functional_native_execution_and_explicit_isolation_remain_required(self):
        self.report['functional']['physicsExecuted'] = False
        with self.assertRaisesRegex(ValueError, 'real simulation'): self.admit()
        self.setUp(); self.report['executionIsolation'] = {}
        with self.assertRaisesRegex(ValueError, 'isolation'): self.admit()


class DeliveredArchiveAdmission(release_asset_tests.ReleaseFixture):
    """Exercise actual snapshot admission with non-runtime archive/proof fixtures."""
    def setUp(self):
        super().setUp()
        self.root = self.directory / 'owned-source'
        self.root.mkdir()
        native = 'addons/flow/pr_flow_host.cpp'
        for name in runner.DELIVERED_TEST_HELPERS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'// Metadata fixture; never compiled or executed.\n')
        build = {'bridge_sources': {native: runner.sha(self.root / native)}}
        self.bytes['dist/candidate/build-manifest.json'] = json.dumps(build).encode()
        self.verification['sourceInventorySha256'] = self.revision['inventorySha256']
        self.manifest['schema'] = 'physx-pe.package-manifest/v1'
        self.args = SimpleNamespace(runtime_zip=self.archive, release_proof=self.directory / 'anonymous.json')
        self.proof = {'schema': 'physx-pe.anonymous-download-check/v1', 'status': 'PASS',
            'repository': 'https://github.com/BTSpaniel/Physx', 'private': False,
            'sourceRevision': self.revision, 'assets': []}
        for name in ('physx-pe.wasm', 'physx-pe.mjs'):
            path = self.root / 'dist/candidate' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.bytes['dist/candidate/' + name])
        self.refresh()

    def refresh(self):
        self.verification['sourceRevision'] = self.revision
        verification = json.dumps(self.verification).encode()
        self.bytes['reports/verification.json'] = verification
        self.manifest['files'] = {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                                 for name, data in self.bytes.items()}
        self.write_archive()
        self.args.runtime_zip = self.archive
        self.proof['assets'] = [{'name': self.archive.name, 'bytes': self.archive.stat().st_size,
            'digest': 'sha256:' + runner.sha(self.archive), 'status': 'PASS', 'authentication': 'none',
            'url': 'https://github.com/BTSpaniel/Physx/releases/download/v' + self.version + '/' + self.archive.name}]
        self.write_proof()

    def write_proof(self):
        self.args.release_proof.write_text(json.dumps(self.proof), encoding='utf-8')

    def snapshot(self):
        report = {}
        with patch.object(runner, 'ROOT', self.root):
            directory = runner.delivered_snapshot(self.args, report)
        return directory, report

    def test_historical_default_remains_alpha2_and_does_not_claim_publication(self):
        directory, report = self.snapshot()
        self.assertEqual(report['expectedDeliveredVersion'], '5.11.0-alpha.2')
        self.assertEqual(report['deliveredSourceRevision'], self.revision)
        self.assertEqual((directory / 'dist/candidate/physx-pe.wasm').read_bytes(),
                         self.bytes['dist/candidate/physx-pe.wasm'])
        self.assertNotIn('releaseApproved', report)

    def test_new_delivered_archive_needs_an_explicit_alpha3_version(self):
        self.version = '5.11.0-alpha.3'
        self.archive = self.directory / f'physx-pe-{self.version}-runtime.zip'
        self.manifest['version'] = self.version
        self.verification['version'] = self.version
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'archive name differs'):
            self.snapshot()
        self.args.expected_version = self.version
        _, report = self.snapshot()
        self.assertEqual(report['expectedDeliveredVersion'], self.version)

    def test_wrong_or_duplicate_anonymous_asset_is_rejected(self):
        original = copy.deepcopy(self.proof)
        for assets in ([], original['assets'] * 2, [{**original['assets'][0], 'name': 'wrong.zip'}]):
            with self.subTest(assets=assets):
                self.proof = copy.deepcopy(original)
                self.proof['assets'] = assets
                self.write_proof()
                with self.assertRaisesRegex(ValueError, 'exactly one anonymous'):
                    self.snapshot()
        self.proof = original
        self.write_proof()

    def test_local_zip_cannot_replace_anonymous_public_versioned_evidence(self):
        original = copy.deepcopy(self.proof)
        for key, value in (('schema', 'local.zip-check/v1'), ('status', 'FAILED'),
                           ('private', True), ('repository', 'https://github.com/another/Physx')):
            with self.subTest(key=key):
                self.proof = copy.deepcopy(original)
                self.proof[key] = value
                self.write_proof()
                with self.assertRaisesRegex(ValueError, 'Anonymous public download'):
                    self.snapshot()
        for key, value in (('authentication', 'token'), ('status', 'FAILED'),
                ('url', original['assets'][0]['url'].replace('alpha.2/', 'alpha.3/'))):
            with self.subTest(assetField=key):
                self.proof = copy.deepcopy(original)
                self.proof['assets'][0][key] = value
                self.write_proof()
                with self.assertRaisesRegex(ValueError, 'release tag or authentication'):
                    self.snapshot()
        self.proof = original
        self.write_proof()

    def test_hash_size_and_current_pair_mismatches_are_rejected(self):
        original = copy.deepcopy(self.proof)
        for key, value in (('bytes', self.archive.stat().st_size + 1), ('bytes', True),
                           ('digest', 'sha256:' + 'f' * 64)):
            with self.subTest(key=key, value=value):
                self.proof = copy.deepcopy(original)
                self.proof['assets'][0][key] = value
                self.write_proof()
                with self.assertRaisesRegex(ValueError, 'ZIP differs from anonymous'):
                    self.snapshot()
        self.proof = original
        self.write_proof()
        (self.root / 'dist/candidate/physx-pe.wasm').write_bytes(b'Changed uncompiled fixture')
        with self.assertRaisesRegex(ValueError, 'pair differs'):
            self.snapshot()

    def test_rehashed_manifest_or_verification_cannot_change_the_proved_revision_or_version(self):
        original_manifest = copy.deepcopy(self.manifest)
        original_verification = copy.deepcopy(self.verification)
        variants = (('manifest', 'version', '5.11.0-alpha.3'),
                    ('manifest', 'sourceRevision', {**self.revision, 'commit': 'f' * 40}),
                    ('verification', 'version', '5.11.0-alpha.3'),
                    ('verification', 'sourceInventorySha256', 'f' * 64))
        for target, key, value in variants:
            with self.subTest(target=target, key=key):
                self.manifest = copy.deepcopy(original_manifest)
                self.verification = copy.deepcopy(original_verification)
                (self.manifest if target == 'manifest' else self.verification)[key] = value
                self.refresh()
                with self.assertRaisesRegex(ValueError, 'runtime version, source revision|verification version or source revision'):
                    self.snapshot()
        self.manifest = original_manifest
        self.verification = original_verification
        self.refresh()

    def test_invalid_expected_version_and_missing_source_identity_reject(self):
        for version in ('5.11.0-alpha.0', '../alpha.3', '5.11.0', True):
            with self.subTest(version=version):
                self.args.expected_version = version
                with self.assertRaisesRegex(ValueError, 'Invalid expected'):
                    self.snapshot()
        del self.args.expected_version
        self.proof['sourceRevision'] = {**self.revision, 'tree': 'not-a-commit'}
        self.write_proof()
        with self.assertRaisesRegex(ValueError, 'source revision is invalid'):
            self.snapshot()


class IsolationAdmission(unittest.TestCase):
    def test_ci_requires_both_actual_flags_and_github_hosted_runner(self):
        valid = {'GITHUB_ACTIONS': 'true', 'CI': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'GITHUB_RUN_ID': '123'}
        admitted = runner.github_ci_isolation(valid)
        self.assertEqual(admitted['mode'], 'github-hosted-ci-isolation')
        self.assertFalse(admitted['sharedLocalPerformanceClaim'])
        for values in ({}, {**valid, 'CI': 'false'}, {**valid, 'GITHUB_ACTIONS': 'false'},
                       {**valid, 'RUNNER_ENVIRONMENT': 'self-hosted'}):
            with self.subTest(values=values), self.assertRaises(ValueError): runner.github_ci_isolation(values)

    def test_no_implicit_unlocked_or_ambiguous_execution_window(self):
        for workspace, isolated in ((None, False), (Path('.'), True)):
            with self.subTest(workspace=workspace), self.assertRaises(ValueError):
                runner.execution_window(workspace, isolated)


if __name__ == '__main__':
    unittest.main()
