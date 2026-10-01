# SPDX-License-Identifier: MIT
"""Offline admission vectors only. These tests do not execute/admit native physics."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from component_evidence import CAPABILITIES, sha, input_inventory, shader_inventory, validate_component, validate_unified

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT.parent / 'momentum-06'

class ComponentAdmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='flow-admission-vectors-')
        cls.root = Path(cls.temp.name)
        host = json.loads((BASE / 'dist/flow-host/build-manifest.json').read_text())
        for folder in ('addons/flow', 'dist/flow-wgsl', 'dist/flow-host', 'work/candidate/PhysX/flow'):
            shutil.copytree(BASE / folder, cls.root / folder)
        for name in host['sourceHashes']:
            dst = cls.root / name
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(BASE / name, dst)
        (cls.root / 'baseline').mkdir()
        shutil.copy2(BASE / 'dist/flow-host/build-manifest.json', cls.root / 'baseline/flow-host-build-manifest.json')
        # Real existing object and pair bytes are reused solely as byte-level
        # parser vectors. This is not a final unified link or a native proof.
        shutil.copy2(BASE / 'work/flow-host-objects/pr_flow_host.o', cls.root / 'work/pr_flow_host.o')
        record = lambda p: {'path': p.relative_to(cls.root).as_posix(), 'sha256': sha(p), 'size': p.stat().st_size}
        cls.component = {'schema':'flow-component-build-v1','status':'COMPILED_NOT_RUNTIME_TESTED',
            'capabilities':CAPABILITIES,'compiler':host['compiler'],
            'hostBuildManifest':record(cls.root/'dist/flow-host/build-manifest.json'),
            'object':record(cls.root/'work/pr_flow_host.o'),
            'inputs':input_inventory(cls.root,host),'shaderInventory':shader_inventory(cls.root)}
        cls.component_path=cls.root/'dist/flow-component/manifest.json';cls.component_path.parent.mkdir()
        cls.component_path.write_text(json.dumps(cls.component))
        output=cls.root/'final';output.mkdir()
        cls.loader=output/'physx-pe.mjs'
        for suffix in ('.mjs','.wasm'):shutil.copy2(BASE/('dist/flow-host/flow-host'+suffix),cls.loader.with_suffix(suffix))
        linked=str(cls.root/'work/pr_flow_host.o')
        cls.final={'status':'COMPILED_NOT_RUNTIME_TESTED','compiler':host['compiler'],
            'artifacts':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in (cls.loader,cls.loader.with_suffix('.wasm'))},
            'commands':[['em++',linked,'-o',str(cls.loader)]],
            'flowComponent':{'schema':'flow-component-link-v1','manifestSha256':sha(cls.component_path),
                'objectSha256':cls.component['object']['sha256'],'objectBytes':cls.component['object']['size'],
                'linkInput':linked,'linkCommandIndex':0,'capabilities':CAPABILITIES},
            'bridge_sources':{k:v for k,v in cls.component['inputs'].items() if k.startswith('addons/flow/')}}
        cls.final_path=output/'build-manifest.json'
        cls.final_path.write_text(json.dumps(cls.final))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def tearDown(self):
        self.component_path.write_text(json.dumps(self.component))
        self.final_path.write_text(json.dumps(self.final))

    def test_exact_vector_is_admitted_without_executing_it(self):
        result=validate_unified(self.root,self.loader)
        self.assertEqual(result['componentObjectSha256'],self.component['object']['sha256'])

    def test_loader_and_engine_routes_serve_the_same_exact_pair(self):
        from solid_browser_test import ProofHandler
        server=ThreadingHTTPServer(('127.0.0.1',0),partial(ProofHandler,directory=str(self.root)))
        server.served_runtime_hashes={};server.runtime_aliases={}
        for prefix in ('/dist/candidate/','/engine/sim/physics/'):
            for suffix in ('.mjs','.wasm'):server.runtime_aliases[prefix+'physx-pe'+suffix]=self.loader.with_suffix(suffix)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            for name,path in server.runtime_aliases.items():
                with urlopen(f'http://127.0.0.1:{server.server_port}'+name) as response:
                    self.assertEqual(response.read(),path.read_bytes())
                    self.assertEqual(response.headers['Content-Type'],'application/wasm' if name.endswith('.wasm') else 'text/javascript')
            self.assertEqual(server.served_runtime_hashes,{name:sha(path) for name,path in server.runtime_aliases.items()})
        finally:server.shutdown();server.server_close()

    def test_wrong_final_pair_link_or_claim_is_rejected(self):
        changes={
            'stale-loader':lambda d:d['artifacts']['physx-pe.mjs'].update(sha256='0'*64),
            'stale-wasm':lambda d:d['artifacts']['physx-pe.wasm'].update(sha256='0'*64),
            'wrong-size':lambda d:d['artifacts']['physx-pe.wasm'].update(bytes=True),
            'missing-component':lambda d:d.pop('flowComponent'),
            'wrong-manifest':lambda d:d['flowComponent'].update(manifestSha256='0'*64),
            'wrong-object':lambda d:d['flowComponent'].update(objectSha256='0'*64),
            'pending-build':lambda d:d.update(status='RUNNING'),
            'compiler-drift':lambda d:d.update(compiler='different compiler'),
            'unknown-capability':lambda d:d['flowComponent']['capabilities'].update(unprovenGpuPhysics=1),
            'boolean-abi':lambda d:d['flowComponent']['capabilities'].update(flowRebaseAbi=True),
            'object-not-linked':lambda d:d['commands'][0].remove(d['flowComponent']['linkInput']),
            'different-link-output':lambda d:d['commands'][0].__setitem__(-1,str(self.loader.with_name('other.mjs'))),
            'duplicate-object':lambda d:d['commands'][0].insert(1,d['flowComponent']['linkInput']),
            'wrong-bridge':lambda d:d['bridge_sources'].update({'addons/flow/rebase_host.h':'0'*64}),
        }
        for name,change in changes.items():
            with self.subTest(name=name):
                d=deepcopy(self.final);change(d);self.final_path.write_text(json.dumps(d))
                with self.assertRaises((ValueError,KeyError)):validate_unified(self.root,self.loader)

    def test_changed_runtime_source_header_shader_or_object_is_rejected(self):
        names=['addons/flow/flow_host_webgpu.mjs','addons/flow/rebase_summary.h',
               'work/candidate/PhysX/flow/source/nvflow/Summary.cpp',
               'work/flow-host-headers/shaders/PrMomentumGatherCS.hlsl.h',
               'dist/flow-wgsl/addons/momentum/PrMomentumGatherCS.wgsl',
               'dist/flow-wgsl/addons/momentum/PrMomentumGatherCS.reflection.json',
               'work/pr_flow_host.o','dist/flow-host/flow-host.wasm']
        for name in names:
            with self.subTest(name=name):
                path=self.root/name;original=path.read_bytes()
                try:
                    path.write_bytes(original+b' changed')
                    with self.assertRaises((ValueError,json.JSONDecodeError)):validate_component(self.root)
                finally:path.write_bytes(original)

    def test_missing_unknown_and_path_escape_closures_are_rejected(self):
        d=deepcopy(self.component);d['inputs'].pop('addons/flow/rebase_summary.h')
        self.component_path.write_text(json.dumps(d))
        with self.assertRaises(ValueError):validate_component(self.root)
        self.component_path.write_text(json.dumps(self.component))
        extra=self.root/'dist/flow-wgsl/unadmitted.wgsl';extra.write_text('invalid')
        try:
            with self.assertRaises(ValueError):validate_component(self.root)
        finally:extra.unlink()
        d=deepcopy(self.component);d['object']['path']='../../outside.o'
        self.component_path.write_text(json.dumps(d))
        with self.assertRaises(ValueError):validate_component(self.root)

if __name__=='__main__':unittest.main(verbosity=2)
