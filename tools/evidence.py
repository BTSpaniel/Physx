#!/usr/bin/env python3
"""Fail-closed consistency checks for local PhysX regression evidence.

NOT a signature verifier, trust boundary, or production release authorization.
Reports must refer to the current sources and the exact staged loader/WASM pair.
A person able to modify both code and reports can forge local evidence.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
import physx_lab as lab
ROOT=Path(__file__).resolve().parents[1]
ARTIFACTS=('physx-js-webidl.mjs','physx-js-webidl.wasm')
HARNESS=('web/suite.mjs','web/regressions.mjs','web/worker.mjs','web/app.mjs',
         'bridge/physx-bulk.mjs','bridge/physx-bulk-rust.mjs','tools/browser_test.py','tools/evidence.py')
BRIDGE=('rust/src/lib.rs','bridge/pr_bulk_rust.cpp','bridge/pr_rust_core.h')
BLAST_STRESS_LEGACY_BRIDGE=('addons/blast/pr_blast_wasm.cpp','addons/blast/emscripten_nv_compat.h',
              'addons/blast/prepare_stress_sources.py','addons/blast/emscripten_stress_device.h',
              'addons/blast/emscripten_stress_intrinsics.h','addons/blast/tr1/type_traits')
BLAST_STRESS_BRIDGE=BLAST_STRESS_LEGACY_BRIDGE+('addons/blast/pr_blast_memory.h',)
BLAST_AUTHORING_BRIDGE=('addons/blast/pr_blast_authoring.cpp','addons/blast/prepare_authoring_sources.py')
BLAST_BRIDGE=BLAST_STRESS_BRIDGE+BLAST_AUTHORING_BRIDGE
FLOW_BRIDGE=('addons/flow/pr_flow_host.cpp','addons/flow/generate_host_headers.py','addons/flow/build_host.py','addons/flow/flow_host_webgpu.mjs')
CORE_TESTS=(
    'Matched loader/WASM hashes','Runtime version and required WebIDL API',
    'Foundation, CPU scene and rigid bodies','Free fall: one second without contact',
    'Ground collision and resting height','Scene raycast hits the resting box',
    'Rust backend identity','Bulk addon: IDs, pose equality, removal',
    'Regression API surface','Zero-gravity constant velocity','Impulse follows inverse mass',
    'Force accumulator clears after one step','Angular rotation remains normalized',
    'Sleep and explicit wake','Kinematic target and return to dynamic mode',
    'Raycast miss has no blocking hit','Static actor remains fixed',
    'Bulk seven-component parity during motion','Bulk contexts remain independent',
    'Bulk capacity and unregister reuse','Bulk context lifecycle churn',
    'Actor, scene and SDK teardown','No SDK stderr diagnostics',
)
UNPROVEN=(
    'D6/ragdolls and articulation behavior','vehicles and character controllers',
    'mesh cooking and serialization stream-width migration','contact/trigger callbacks',
    'long-duration memory endurance','browser/device matrix including Android',
    'rollback/replay and full Particle Realms integration',
)
class EvidenceError(ValueError):pass

def require(ok: bool, message: str) -> None:
    if not ok:raise EvidenceError(message)

def source_hashes(root: Path=ROOT, names: tuple[str,...]=HARNESS) -> dict[str,str]:
    return {name:lab.sha256(root/name) for name in names}

def object_json(path: Path) -> dict[str,Any]:
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out:raise EvidenceError('Duplicate JSON key: '+k)
            out[k]=v
        return out
    def invalid(value):raise EvidenceError('Non-finite JSON constant: '+value)
    value=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=unique,parse_constant=invalid)
    require(isinstance(value,dict),'Expected a JSON object: '+str(path))
    return value

def instant(value: Any, name: str) -> datetime:
    require(isinstance(value,str),name+' must be an ISO timestamp')
    try:result=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc:raise EvidenceError('Invalid timestamp: '+name) from exc
    require(result.tzinfo is not None,name+' must include a timezone')
    return result.astimezone(timezone.utc)

def verify_artifacts(manifest: dict, directory: Path, profile: str, root: Path=ROOT) -> dict:
    require(profile in ('baseline','candidate'),'Unknown build profile')
    require(isinstance(manifest,dict),'Manifest must be an object')
    expected=lab.LOCK[profile+'_sdk']
    for name in ('source_version','expected_runtime_version'):
        require(manifest.get(name)==expected,f'{name}: expected {expected}')
    require(manifest.get('profile')==profile,'Manifest profile mismatch')
    require(manifest.get('locally_compiled') is True,'A locally compiled build is required')
    require(manifest.get('bulk_addon_requested') is True,'Rust bulk addon was not requested')
    require(manifest.get('bridge_backend')=='rust','Expected the actual Rust-backed addon')
    require(type(manifest.get('rust_abi')) is int and manifest['rust_abi']==2,'Rust ABI mismatch')
    require(type(manifest.get('js_bulk_abi')) is int and manifest['js_bulk_abi']==1,'Public bulk ABI mismatch')
    bridge_sources=BRIDGE+BLAST_BRIDGE+FLOW_BRIDGE if profile=='candidate' else BRIDGE
    require(manifest.get('bridge_sources')==source_hashes(root,bridge_sources),
            'Bridge sources changed or source hashes missing; rebuild')
    if profile=='candidate':
        require(manifest.get('blast_core_version')=='5.0.6','Unified Blast core version mismatch')
        require(manifest.get('flow_webgpu_stage_abi')==1,'Unified Flow staging ABI mismatch')
        require(type(manifest.get('blast_authoring_abi')) is int and manifest['blast_authoring_abi']==1,
                'Unified Blast authoring ABI mismatch')
        require(type(manifest.get('flow_collision_abi')) is int and manifest['flow_collision_abi']==1,
                'Unified Flow collision ABI mismatch')
    metadata=manifest.get('artifacts')
    artifacts=('physx-pe.mjs','physx-pe.wasm') if profile=='candidate' else ARTIFACTS
    require(isinstance(metadata,dict) and set(metadata)==set(artifacts),'Expected exactly the matched loader and WASM artifacts')
    for name in artifacts:
        item=metadata[name]
        require(isinstance(item,dict),'Malformed artifact metadata: '+name)
        require(type(item.get('bytes')) is int and item['bytes']>8,'Invalid artifact byte length: '+name)
        require(isinstance(item.get('sha256'),str) and re.fullmatch(r'[0-9a-f]{64}',item['sha256']) is not None,'Invalid SHA-256: '+name)
        path=directory/name
        require(path.resolve().is_relative_to(directory.resolve()),'Artifact escapes staging directory: '+name)
        require(path.is_file(),'Artifact missing: '+name)
        require(path.stat().st_size==item['bytes'],'Artifact size changed: '+name)
        require(lab.sha256(path)==item['sha256'],'Artifact SHA-256 changed: '+name)
    lab.verify_wasm(directory/artifacts[1])
    return metadata

def validate_report(report: dict, manifest: dict, harness: dict, profile: str,
                    now: datetime | None=None, not_before: datetime | None=None) -> dict:
    require(isinstance(report,dict),'Report must be an object')
    require(report.get('profile')==profile,'Report profile mismatch')
    require(report.get('status')=='SMOKE_PASSED_NOT_RELEASE_CERTIFIED','Real regression run did not pass')
    require(report.get('physicsExecuted') is True,'No real simulation was recorded')
    require(report.get('releaseApproved') is False,'Smoke results must not claim release approval')
    require(report.get('engineIntegrationVerified') is False,'This suite cannot certify full-engine integration')
    for name in ('stderr','pageErrors'):
        require(type(report.get(name)) is list and not report[name],name+' is missing or contains errors')
    require(report.get('artifactHashes')==manifest['artifacts'],'Report belongs to a different artifact pair')
    require(report.get('testHarnessSha256')==harness,'Test harness changed or evidence is from an older suite')
    started=instant(report.get('started'),'started');finished=instant(report.get('finished'),'finished')
    staged=instant(manifest.get('staged_utc'),'staged_utc')
    require(finished>=started,'Report finished before it started')
    require(started>=staged,'Report predates the staged build')
    require(finished <= (now or datetime.now(timezone.utc))+timedelta(minutes=5),'Report timestamp is in the future')
    if not_before is not None:require(started>=not_before,'Stale report from before this pipeline invocation')
    rows=report.get('tests');require(isinstance(rows,list) and bool(rows),'Empty/missing tests never pass')
    names=set()
    for row in rows:
        require(isinstance(row,dict),'Malformed test row')
        name=row.get('name');require(isinstance(name,str) and bool(name.strip()),'Test name missing')
        require(name not in names,'Duplicate test: '+name);names.add(name)
        require(row.get('status')=='PASS','Skipped, failed or unknown test status: '+name)
        ms=row.get('ms');require(type(ms) in (int,float) and math.isfinite(ms) and ms>=0,'Invalid test duration: '+name)
    missing=set(CORE_TESTS)-names
    require(not missing,'Required scenarios missing: '+', '.join(sorted(missing)))
    runtime=next(r for r in rows if r['name']=='Runtime version and required WebIDL API')
    require(isinstance(runtime.get('detail'),dict) and runtime['detail'].get('runtimeVersion')==manifest['expected_runtime_version'],'Runtime SDK version mismatch')
    return {'status':'REGRESSION_EVIDENCE_ACCEPTED_NOT_RELEASE_CERTIFIED','profile':profile,
            'physicsExecuted':True,'releaseApproved':False,'engineIntegrationVerified':False,
            'testsAccepted':len(rows),'stillUnproven':list(UNPROVEN)}

def check_profile(profile: str, root: Path=ROOT, not_before: datetime | None=None) -> dict:
    directory=root/'dist'/profile
    manifest=object_json(directory/'build-manifest.json')
    verify_artifacts(manifest,directory,profile,root)
    report=object_json(root/'reports'/f'{profile}-browser.json')
    return validate_report(report,manifest,source_hashes(root),profile,not_before=not_before)

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',choices=['baseline','candidate'],required=True)
    args=parser.parse_args()
    try:result=check_profile(args.profile);code=0
    except (OSError,ValueError,KeyError,lab.LabError) as exc:
        result={'status':'BLOCKED_OR_FAILED','profile':args.profile,'error':str(exc),
                'releaseApproved':False,'physicsExecuted':False};code=2
    lab.write_json(ROOT/'reports'/f'{args.profile}-evidence-gate.json',result)
    print(json.dumps(result,indent=2));return code
if __name__=='__main__':raise SystemExit(main())
