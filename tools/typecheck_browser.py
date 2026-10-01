# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Execute Microsoft's pinned TypeScript checker in a browser, without Node/npm."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

import physx_lab as lab
from generate_addon_types import generate

ROOT = lab.ROOT
CONSUMER = """
import createPhysX from './types/physx-pe.mjs';
import {PhysXBulk} from './bridge/physx-bulk.mjs';
import {PhysXBulkRust} from './bridge/physx-bulk-rust.mjs';
import {FlowHostWebGpu} from './addons/flow/flow_host_webgpu.mjs';
import {FlowWasmWebGpuBridge} from './addons/flow/webgpu_bridge.mjs';
import {packSolidBoundaries, solidBoundaryWGSL} from './addons/flow/flow_solid_boundary.mjs';
import {packScalarSources} from './addons/flow/flow_scalar_sources.mjs';
import type {FlowRebaseToken, FlowMomentumToken, FlowRebaseReceipt, FlowMomentumReceipt} from './addons/flow/flow_host_webgpu.mjs';
const P = await createPhysX();
declare const device: GPUDevice;
declare const actor: InstanceType<typeof P.PxRigidActor>;
declare const scene: InstanceType<typeof P.PxScene>;
const bulk = new PhysXBulkRust(P, {capacity: 32});
bulk.add(1, actor).remove(1); const snapshot: Float32Array = bulk.snapshot({copy:true}).poses;
const plain: PhysXBulk = bulk; plain.dispose();
P._pr_bulk_create(32); P._pr_blast_authoring_abi(); P._pr_flow_host_abi();
const collection = P.PxCollectionExt.prototype.createCollection(scene);
collection.addId(actor, 0x1000000010000001n);
const objectId: bigint = collection.getId(actor);
// Real Vehicle2 binding accessors operate on individual native array entries.
const vehicle = new P.DirectDriveVehicle();
const baseParams = vehicle.get_baseParams();
baseParams.get_axleDescription().set_wheelIdsInAxleOrder(0, 0);
const suspensionDistance: number = baseParams.get_suspensionParams(0).get_suspensionTravelDist();
const brakeTorque: number = baseParams.get_brakeResponseParams(0).get_maxResponse();
const wheelAngularSpeed: number = vehicle.get_baseState().get_wheelRigidBody1dStates(0).get_rotationSpeed();
vehicle.get_commandState().set_brakes(0, 1);
const chassis = P.castObject(vehicle.get_physXState().get_physxActor().get_rigidBody(), P.PxRigidDynamic);
chassis.setSolverIterationCounts(8, 2);
const events = new P.PxSimulationEventCallbackImpl();
events.onContact = (rawHeader, rawPairs, count) => {
  const header = typeof rawHeader === 'number' ? P.wrapPointer(rawHeader, P.PxContactPairHeader) : rawHeader;
  const pairs = typeof rawPairs === 'number' ? P.wrapPointer(rawPairs, P.PxContactPair) : rawPairs;
  const otherActor = header.get_actors(0);
  const contact = P.NativeArrayHelpers.prototype.getContactPairAt(pairs, count - 1);
  const found: boolean = contact.get_events().isSet(P.PxPairFlagEnum.eNOTIFY_TOUCH_FOUND);
};
events.onTrigger = (rawPairs, count) => {
  const pairs = typeof rawPairs === 'number' ? P.wrapPointer(rawPairs, P.PxTriggerPair) : rawPairs;
  const trigger = P.NativeArrayHelpers.prototype.getTriggerPairAt(pairs, count - 1);
  const triggerActor = trigger.get_triggerActor(); const status: number = trigger.get_status();
};
scene.setSimulationEventCallback(events);
const flow = await FlowHostWebGpu.create(P, device, './shaders/');
flow.setScene({layers:[{id:0}], emitters:[{id:1,type:'sphere',radius:.4}]});
flow.setColliders([{id:1,type:'box',halfSize:[1,2,3]}]);
const output = await flow.step(); const samples: Float32Array = await flow.sampleVelocity(new Float32Array([0,0,0]));
// Prospective contract-only consumer. This checker never executes these calls;
// the alpha.2 runtime is not declared to implement the component additions.
flow.setSolidBoundaries([
  {id:1,type:'box',halfSize:[1,2,3],terminalExchange:true},
  {id:2,type:'sphere',radius:0.5}, {id:3,type:'plane'},
  {id:4,type:'convex',planes:[[1,0,0,1],[-1,0,0,1],[0,1,0,1],[0,0,1,1]],bounds:[[-1,-1,-1],[1,1,1]]}
]);
flow.setScalarSources([{id:1,type:'box',halfSize:[1,1,1],totalRates:[0.5,0.2,0.1,0]}]);
flow.setSolidBoundaries(undefined); flow.setScalarSources(undefined);
const scalarPacket: ArrayBuffer = packScalarSources([{id:2,halfSize:[1,1,1],totalRates:[0,0,0,0]}],new Set([0])).packet;
const planes: Float32Array = packSolidBoundaries([{id:2,type:'sphere',radius:1}],new Set([0])).planes;
const boundaryShader: string = solidBoundaryWGSL({group:1,binding:0,name:'boundaries'});
const epoch: number | undefined = flow.output?.coordinateEpoch;
const boundaryBuffer: GPUBuffer | undefined = flow.output?.boundaries?.buffer;
const appliedAmounts: number[] | undefined = flow.scalarSourceReceipt?.sources[0]?.applied;
const rebases: number = flow.stats.rebases; const requestedClock: boolean | undefined = flow.stats.exactRequestedTimeStep;
const period: [number,number,number] = flow.rebasePeriod();
const admitted: true = flow.validateRebase([period[0],0,0]);
const rebaseToken: FlowRebaseToken = await flow.prepareRebase([period[0],0,0]);
flow.validatePreparedRebase(rebaseToken);
const rebaseReceipt: FlowRebaseReceipt = await flow.commitPreparedRebase(rebaseToken);
const noElapsedRebase: 0 = rebaseReceipt.advancedTime;
await flow.commitPreparedRebase(await flow.prepareRebase([0,0,0]),null);
await flow.commitPreparedRebase(await flow.prepareRebase([0,0,0]),undefined);
await flow.commitPreparedRebase(await flow.prepareRebase([0,0,0]),()=>{scene.getGravity();});
await flow.rebase([0,0,0]);
const momentumToken: FlowMomentumToken = await flow.prepareMomentumExchange(new Map([[0,1.225]]),{capacity:393216});
flow.validatePreparedMomentum(momentumToken);
const velocities = momentumToken.gas.map(cell=>cell.velocity);
const momentumReceipt: FlowMomentumReceipt = await flow.commitPreparedMomentum(momentumToken,velocities,null);
const noElapsedExchange: 0 = momentumReceipt.advancedTime;
const nextMomentum = await flow.prepareMomentumExchange(new Map([[0,1.225]]),undefined);
await flow.commitPreparedMomentum(nextMomentum,nextMomentum.gas.map(cell=>cell.velocity),undefined);
const finalMomentum = await flow.prepareMomentumExchange(new Map([[0,1.225]]));
await flow.commitPreparedMomentum(finalMomentum,finalMomentum.gas.map(cell=>cell.velocity),()=>{scene.getGravity();});
P._pr_flow_host_rebase_abi(); P._pr_flow_host_rebase_period(flow.handle,0);
P._pr_flow_host_rebase(flow.handle,0,0,0,0);
P._pr_flow_host_momentum_abi(); P._pr_flow_host_momentum_prepare(flow.handle,393216);
P._pr_flow_host_momentum_data(flow.handle,1); P._pr_flow_host_momentum_commit(flow.handle,1,0,0);
P._pr_flow_host_solid_abi(); P._pr_flow_host_solids(flow.handle,0,0,0,0);
P._pr_flow_host_scalar_abi(); P._pr_flow_host_scalar_sources(flow.handle,0,0);
P._pr_flow_host_scalar_receipt(flow.handle); P._pr_flow_host_scalar_count(flow.handle);
P._pr_flow_host_scalar_frame(flow.handle); P._pr_flow_host_scalar_dt(flow.handle);
// Complete prospective source-build ABI; semantic checks only, never executed.
// Raw wasm32 addresses remain numbers. Callers own and align each heap region.
declare const familyHandle: number, chunkCount: number, bondCount: number, sampleCount: number;
declare const massesKgPtr: number, limitsPaPtr: number, forcesNPtr: number;
declare const stiffnessF64Ptr: number, offsetsU32Ptr: number, samplesF64Ptr: number;
declare const wrenchF32Ptr: number, progressF64Ptr: number, sectionResultsF64Ptr: number, workF64Ptr: number;
const massAbi: number = P._pr_blast_stress_mass_abi();
const preparationAbi: number = P._pr_blast_stress_sections_v3_preparation_abi();
P._pr_blast_stress_physical_abi(); P._pr_blast_stress_sections_abi(); P._pr_blast_stress_sections_v3_abi();
P._pr_blast_stress_set_masses(familyHandle,massesKgPtr,chunkCount);
P._pr_blast_stress_configure_physical(familyHandle,massesKgPtr,limitsPaPtr,200);
P._pr_blast_stress_configure_sections(familyHandle,massesKgPtr,200,bondCount,stiffnessF64Ptr,offsetsU32Ptr,samplesF64Ptr,sampleCount);
P._pr_blast_stress_configure_sections_v3(familyHandle,massesKgPtr,200,bondCount,stiffnessF64Ptr,offsetsU32Ptr,samplesF64Ptr,sampleCount);
P._pr_blast_stress_update_physical(familyHandle,forcesNPtr,chunkCount);
P._pr_blast_stress_physical_revision(familyHandle); P._pr_blast_stress_physical_tolerance(familyHandle);
P._pr_blast_stress_physical_progress(familyHandle,progressF64Ptr,8);
P._pr_blast_stress_section_results(familyHandle,sectionResultsF64Ptr,sampleCount);
P._pr_blast_stress_bond_wrench(familyHandle,0,wrenchF32Ptr,6);
const workStatus: number = P._pr_blast_stress_sections_v3_work(familyHandle,workF64Ptr,12);
declare const arenaPtr: number, arenaBytes: number, workspacePtr: number;
declare const layers: number, edges: number, boundaries: number, cells: number;
const baseThermalAbi: number = P._pr_wood_thermal_abi();
const scratchBytes: number = P._pr_wood_thermal_scratch_bytes(layers,boundaries);
const baseThermalStatus: number = P._pr_wood_thermal_step(arenaPtr,arenaBytes,0.1);
const multirateThermalAbi: number = P._pr_wood_thermal_mr_abi();
const thermalNumerics: number = P._pr_wood_thermal_mr_numerics();
const workspaceBytes: number = P._pr_wood_thermal_mr_workspace_bytes(layers,edges,boundaries,cells);
// arena and workspace must be disjoint and 8-byte aligned at runtime. The ABI
// returns status codes and retains no context or pointer between calls.
const thermalStatus: number = P._pr_wood_thermal_mr_step(arenaPtr,arenaBytes,workspacePtr,workspaceBytes,0.1);
declare const shape: InstanceType<typeof P.PxShape>, shapePtr: number, convexOutputPtr: number;
const convexAbi: number = P._pr_flow_boundary_convex_abi();
// Borrow a live shape after scene.fetchResults; count is planes, capacity floats.
const planeCount: number = P._pr_flow_boundary_convex(shapePtr,0,0);
const convexResult: number = P._pr_flow_boundary_convex(shapePtr,convexOutputPtr,6+4*planeCount);
// @ts-expect-error typed views are not raw pointers into this module's WASM heap
P._pr_blast_stress_set_masses(familyHandle,new Float32Array(4),4);
// @ts-expect-error pure preparation capability getter has no handle argument
P._pr_blast_stress_sections_v3_preparation_abi(familyHandle);
// @ts-expect-error f64 work output requires its allocated numeric pointer
P._pr_blast_stress_sections_v3_work(familyHandle,new Float64Array(12),12);
// @ts-expect-error thermal step requires explicit caller-owned workspace and dt
P._pr_wood_thermal_mr_step(arenaPtr,arenaBytes,workspacePtr,workspaceBytes);
// @ts-expect-error the workspace pointer is an address, not a JS buffer
P._pr_wood_thermal_mr_step(arenaPtr,arenaBytes,new ArrayBuffer(1024),workspaceBytes,0.1);
// @ts-expect-error thermal ABI status is numeric, not a boolean success wrapper
const booleanThermalStatus: boolean = P._pr_wood_thermal_mr_step(arenaPtr,arenaBytes,workspacePtr,workspaceBytes,0.1);
// @ts-expect-error a borrowed native shape wrapper is not its numeric address
P._pr_flow_boundary_convex(shape,convexOutputPtr,1024);
// @ts-expect-error capacity is explicitly required even for a null-output query
P._pr_flow_boundary_convex(shapePtr,0);
// @ts-expect-error opaque rebase tokens cannot be manufactured from their public view
flow.validatePreparedRebase({shift:[0,0,0],coordinateEpoch:0});
// @ts-expect-error opaque momentum tokens cannot be manufactured from their public view
flow.validatePreparedMomentum({gas:[],contacts:[]});
// @ts-expect-error rebase shift is deeply readonly
rebaseToken.shift[0]=0;
// @ts-expect-error admitted gas cells are deeply readonly
momentumToken.gas[0].velocity[0]=0;
// @ts-expect-error contact records are deeply readonly
momentumToken.contacts[0].normal[0]=0;
// @ts-expect-error asynchronous coordinators cannot enter a synchronous native commit
flow.commitPreparedRebase(rebaseToken,async()=>{});
// @ts-expect-error asynchronous body coordinators cannot enter a synchronous native commit
flow.commitPreparedMomentum(momentumToken,velocities,async()=>{});
// @ts-expect-error rebase admission explicitly requires a plain array
flow.validateRebase(new Float32Array([0,0,0]));
// @ts-expect-error calibrated densities require an actual Map
flow.prepareMomentumExchange({0:1.225});
// @ts-expect-error the momentum options object does not admit null
flow.prepareMomentumExchange(new Map([[0,1.225]]),null);
// @ts-expect-error a box boundary requires real half sizes
flow.setSolidBoundaries([{id:5,type:'box'}]);
// @ts-expect-error a source requires integrated channel rates
flow.setScalarSources([{id:6,halfSize:[1,1,1]}]);
// @ts-expect-error the host receipt does not claim Engine thermal deposition or heat ledgers
momentumReceipt.heatObligationJ;
await flow.dispose();
const bridge = await FlowWasmWebGpuBridge.create(P, {device});
const ptr = bridge.allocate(256); bridge.free(ptr); bridge.close();
// @ts-expect-error native capacity must be a number
P._pr_bulk_create('32');
// @ts-expect-error 64-bit IDs must retain bigint precision
collection.addId(actor, 41);
// @ts-expect-error mesh emitters require actual position and index data
flow.setScene({emitters:[{id:2,type:'mesh'}]});
// @ts-expect-error module-specific native actor required
bulk.add(1, {});
// @ts-expect-error native array element getters require an index
baseParams.get_suspensionParams();
// @ts-expect-error native indexed setters cannot consume a JavaScript Array
vehicle.get_commandState().set_brakes([0, 1]);
// @ts-expect-error native array attributes are not exposed as JS Arrays
baseParams.suspensionParams.map(x => x);
// @ts-expect-error an actor pointer wrapper is not a pointer number
P.wrapPointer(actor, P.PxRigidDynamic);
"""
CHECK = """files => {
  const options={strict:true,noEmit:true,skipLibCheck:false,target:ts.ScriptTarget.ES2022,
    module:ts.ModuleKind.NodeNext,moduleResolution:ts.ModuleResolutionKind.NodeNext};
  const normalize=path=>{const parts=[];for(const part of path.replaceAll('\\\\','/').split('/')) {
    if(!part||part==='.')continue;if(part==='..')parts.pop();else parts.push(part);
  } return '/'+parts.join('/');};
  const host={getSourceFile:(name,language)=>{const content=files[normalize(name)];return content===undefined?undefined:ts.createSourceFile(name,content,language,true);},
    getDefaultLibFileName:()=>'/compiler/lib.es2022.full.d.ts',writeFile:()=>{},getCurrentDirectory:()=> '/',
    getDirectories:()=>[],fileExists:name=>Object.hasOwn(files,normalize(name)),readFile:name=>files[normalize(name)],
    getCanonicalFileName:name=>normalize(name),useCaseSensitiveFileNames:()=>true,getNewLine:()=> '\\n'};
  const program=ts.createProgram(['/consumer.mts'],options,host);
  const diagnostics=ts.getPreEmitDiagnostics(program).map(d=>({code:d.code,category:d.category,
    file:d.file?.fileName,line:d.file&&d.start!==undefined?d.file.getLineAndCharacterOfPosition(d.start).line+1:null,
    message:ts.flattenDiagnosticMessageText(d.messageText,'\\n')}));
  return {typescript:ts.version,diagnostics,filesChecked:program.getSourceFiles().map(f=>f.fileName)};
}"""


def compiler_inputs() -> tuple[bytes, dict[str, str]]:
    lock = lab.LOCK
    archive = ROOT / 'work/typecheck-tools/typescript-5.9.3.nupkg'
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        print('Downloading pinned Microsoft TypeScript validation SDK')
        with urllib.request.urlopen(lock['typescript_validation_archive_url'], timeout=60) as response:
            archive.write_bytes(response.read())
    if lab.sha256(archive) != lock['typescript_validation_archive_sha256']:
        raise lab.LabError('TypeScript validation archive changed')
    files = {}
    compiler = None
    with zipfile.ZipFile(io.BytesIO(archive.read_bytes())) as bundle:
        for name in bundle.namelist():
            basename = name.rsplit('/', 1)[-1]
            if basename == 'typescript.js':
                compiler = bundle.read(name)
            elif basename.startswith('lib.') and basename.endswith('.d.ts'):
                files['/compiler/' + basename] = bundle.read(name).decode('utf-8')
    if compiler is None or '/compiler/lib.es2022.full.d.ts' not in files:
        raise lab.LabError('Pinned TypeScript compiler/library closure incomplete')
    for relative, expected in generate().items():
        if (ROOT / relative).read_bytes() != expected:
            raise lab.LabError('Generated declaration inputs changed: ' + relative)
        if relative.endswith(('.d.ts', '.d.mts')):
            files['/' + relative] = expected.decode('utf-8')
    gpu = ROOT / 'types/webgpu.d.ts'
    if lab.sha256(gpu) != lock['webgpu_types_sha256']:
        raise lab.LabError('Pinned WebGPU declaration bytes changed')
    if lab.sha256(ROOT / 'LICENSES/WebGPU-Types-BSD-3-Clause.txt') != lock['webgpu_types_license_sha256']:
        raise lab.LabError('Pinned WebGPU declaration license bytes changed')
    files['/types/webgpu.d.ts'] = gpu.read_text(encoding='utf-8')
    files['/consumer.mts'] = CONSUMER
    return compiler, files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium')
    args = parser.parse_args()
    report = {'status': 'RUNNING', 'scope': 'Actual TypeScript declaration semantics in Chromium; no physics/GPU execution', 'pageErrors': []}
    try:
        compiler, files = compiler_inputs()
        hashes = {name: hashlib.sha256(content.encode()).hexdigest() for name, content in files.items()}
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            options = {'headless': True}
            if args.chromium:
                options['executable_path'] = args.chromium
            browser = playwright.chromium.launch(**options)
            try:
                page = browser.new_page()
                page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
                page.add_script_tag(content=compiler.decode('utf-8'))
                report.update(page.evaluate(CHECK, files), browser=browser.version,
                              compilerSha256=hashlib.sha256(compiler).hexdigest(), inputSha256=hashes)
            finally:
                browser.close()
        if report['pageErrors'] or report['diagnostics']:
            raise lab.LabError('TypeScript declaration diagnostics remain')
        after_compiler, after_files = compiler_inputs()
        report['inputSha256After'] = {name: hashlib.sha256(content.encode()).hexdigest() for name, content in after_files.items()}
        if compiler != after_compiler or hashes != report['inputSha256After']:
            raise lab.LabError('TypeScript inputs changed during browser execution')
        report['status'] = 'TYPESCRIPT_DECLARATIONS_PASSED'
    except Exception as exc:
        report.update(status='FAILED', error=str(exc))
    lab.write_json(ROOT / 'reports/addon-types-browser.json', report)
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'TYPESCRIPT_DECLARATIONS_PASSED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
