# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Synthetic packaging admission vectors; these never execute native physics."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import package_release
from test_endurance_admission import receipt_fixture

ADMISSION_ERRORS = (ValueError, package_release.lab.LabError)


class PackageEnduranceAdmission(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report, self.build, sources = receipt_fixture()
        for name in sources:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            # Provenance-only bytes. No synthetic file is loaded as code.
            data = ('synthetic admission input: ' + name).encode()
            path.write_bytes(data)
            sources[name] = hashlib.sha256(data).hexdigest()
        self.report['sourceSha256Before'] = dict(sources)
        self.report['sourceSha256After'] = dict(sources)
        self.report['functional']['testHarnessSha256'] = {
            name: sources[name] for name in self.report['functional']['testHarnessSha256']}
        from browser_compat_endurance import validate_endurance
        self.admission = validate_endurance(self.report, self.build, sources)
        self.phase = self.root / 'reports/phases/endurance.json'
        self.phase.parent.mkdir(parents=True)
        self.verified = {'startedUtc': self.build['staged_utc'], 'steps': [{
            'name': 'Bounded native CPU endurance and identical-input replay',
            'status': 'PASS', 'testCount': 3, 'reportFile': 'reports/phases/endurance.json',
            'checks': self.admission, 'resultStatus': self.report['status']}]}
        self.write_phase()

    def write_phase(self):
        self.phase.write_text(json.dumps(self.report), encoding='utf-8')
        self.verified['steps'][0]['reportSha256'] = hashlib.sha256(self.phase.read_bytes()).hexdigest()

    def admit(self):
        with patch.object(package_release, 'ROOT', self.root):
            package_release.admit_endurance_phase(self.verified, self.build)

    def test_exact_bounded_phase_can_be_packaged_without_full_leak_claim(self):
        self.admit()
        self.assertFalse(self.admission['fullAllocatorLeakProof'])

    def test_missing_duplicate_skipped_or_incomplete_phase_rejects(self):
        row = copy.deepcopy(self.verified['steps'][0])
        for rows in ([], [row, row], [{**row, 'status': 'SKIP'}], [{**row, 'testCount': 2}]):
            self.verified['steps'] = rows
            with self.subTest(rows=rows), self.assertRaises(ADMISSION_ERRORS): self.admit()

    def test_nonrelease_phase_and_modified_summary_reject(self):
        self.report['releasePhase'] = False
        self.write_phase()
        with self.assertRaisesRegex(ADMISSION_ERRORS, 'release phase'): self.admit()
        self.report['releasePhase'] = True
        self.write_phase()
        self.verified['steps'][0]['checks'] = {}
        with self.assertRaisesRegex(ADMISSION_ERRORS, 'summary'): self.admit()

    def test_changed_or_escaped_raw_phase_rejects(self):
        self.phase.write_bytes(self.phase.read_bytes() + b' ')
        with self.assertRaisesRegex(ADMISSION_ERRORS, 'bytes changed'): self.admit()
        self.write_phase()
        self.verified['steps'][0]['reportFile'] = 'web/endurance-regressions.mjs'
        with self.assertRaisesRegex(ADMISSION_ERRORS, 'escaped'): self.admit()

    def test_changed_source_pair_or_short_replay_rejects(self):
        (self.root / 'release.py').write_bytes(b'changed synthetic admission input')
        with self.assertRaisesRegex(ValueError, 'sources changed'): self.admit()
        self.setUp()
        self.build['artifacts']['physx-pe.wasm']['sha256'] = 'e' * 64
        with self.assertRaisesRegex(ValueError, 'native pair'): self.admit()
        self.setUp()
        self.report['endurance']['tests'][1]['detail']['stepsPerRun'] = 120
        self.write_phase()
        with self.assertRaisesRegex(ValueError, 'duration/coverage'): self.admit()


if __name__ == '__main__':
    unittest.main()
