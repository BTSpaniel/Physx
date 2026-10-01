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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import browser_compat_endurance as runner
import evidence


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
