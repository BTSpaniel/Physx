"""Additional tests for the ACTUAL Rust shared library; skip when it is absent.

Uses synthetic seven-float data readers, never a mock PhysX solver. Stable seeds
are part of each failure's context so a future failure can be replayed exactly.
"""
from __future__ import annotations
import gc
import math
import os
import random
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from pr_pose import PoseBatch, PoseError

@unittest.skipUnless(os.environ.get('PR_RUST_LIB'), 'Actual Rust library absent; FFI stress NOT RUN')
class RustFFIStress(unittest.TestCase):
    def make(self,n=8):
        b=PoseBatch(os.environ['PR_RUST_LIB'],capacity=n);self.addCleanup(b.close);return b
    def test_failed_snapshot_at_every_row_and_component_is_transactional(self):
        b=self.make(8);data=[[i,2,3,0,0,0,1] for i in range(8)]
        for i,row in enumerate(data):b.add(i+1,lambda r=row:r)
        expected=b.snapshot()
        for row in range(8):
            for component in range(7):
                old=data[row][component];data[row][component]=math.nan
                with self.subTest(row=row,component=component),self.assertRaises(PoseError):b.snapshot()
                self.assertEqual(list(b._ids[:8]),expected[0])
                self.assertEqual(list(b._poses[:56]),[v for p in expected[1] for v in p])
                self.assertEqual(b._cache.published,8)
                data[row][component]=old
                self.assertEqual(b.snapshot(),expected)
    def test_failed_callback_after_success_recovers(self):
        b=self.make();state={'fail':False}
        def reader():
            if state['fail']:raise ValueError('injected')
            return [1,2,3,0,0,0,1]
        b.add(1,reader);expected=b.snapshot();state['fail']=True
        with self.assertRaises(PoseError):b.snapshot()
        state['fail']=False;self.assertEqual(b.snapshot(),expected);self.assertIsNone(b._error)
    def test_mutating_returned_copies_does_not_touch_native_buffers(self):
        b=self.make();b.add(1,lambda:[0,2,0,0,0,0,1]);ids,poses=b.snapshot()
        ids[0]=100;poses[0]=(99,)*7
        self.assertEqual(b.snapshot(),([1],[(0,2,0,0,0,0,1)]))
    def test_reuse_id_after_unregister_does_not_use_old_callback(self):
        b=self.make(1);b.add(0xffffffff,lambda:[0,2,0,0,0,0,1]);b.remove(0xffffffff)
        b.add(0xffffffff,lambda:[0,9,0,0,0,0,1]);gc.collect()
        self.assertEqual(b.snapshot()[1][0][1],9)
    def test_two_caches_remain_independent(self):
        a=self.make(1);b=self.make(1)
        a.add(1,lambda:[0,2,0,0,0,0,1]);b.add(1,lambda:[0,8,0,0,0,0,1]);a.close()
        self.assertEqual(b.snapshot()[1][0][1],8)
    def test_remove_all_then_refill_capacity(self):
        b=self.make(16)
        for cycle in range(64):
            for i in range(1,17):b.add(i,lambda v=cycle:[v,0,0,0,0,0,1])
            self.assertEqual(b.snapshot()[0],list(range(1,17)))
            for i in range(16,0,-1):b.remove(i)
            self.assertEqual(b.snapshot(),([],[]))
    def test_invalid_add_and_remove_leave_snapshot_intact(self):
        b=self.make();b.add(1,lambda:[0,2,0,0,0,0,1]);expected=b.snapshot()
        for bad in (0,-1,True,1.5,2**32,None):
            for action in (lambda:b.add(bad,lambda:[]),lambda:b.remove(bad)):
                with self.assertRaises((ValueError,TypeError)):action()
                self.assertEqual(b.snapshot(),expected)
    def test_close_inside_reader_fails_without_closing_batch(self):
        b=self.make();b.add(1,lambda:b.close())
        with self.assertRaises(PoseError):b.snapshot()
        self.assertFalse(b._closed);b.remove(1);self.assertEqual(b.snapshot(),([],[]))
    def test_captured_reader_references_are_released(self):
        import weakref
        class Source:
            def __call__(self):return [0,2,0,0,0,0,1]
        source=Source();ref=weakref.ref(source);b=self.make();b.add(1,source);del source;gc.collect()
        self.assertIsNotNone(ref());b.remove(1);gc.collect();self.assertIsNone(ref())

    def _state_machine(self,seed):
        rng=random.Random(seed);b=self.make(8);model={};trace=[]
        try:
            for step in range(2048):
                action=rng.randrange(4);entity=rng.choice([1,2,3,4,5,6,7,8,9,16777217,0xffffffff])
                trace.append((action,entity));trace=trace[-20:]
                if action==0:
                    row=[float(step),float(entity%31),0,0,0,0,1]
                    if entity in model or len(model)==8:
                        with self.assertRaises(PoseError):b.add(entity,lambda:row)
                    else:b.add(entity,lambda r=row:r);model[entity]=row
                elif action==1:
                    if entity not in model:
                        with self.assertRaises(PoseError):b.remove(entity)
                    else:b.remove(entity);del model[entity]
                elif action==2 and entity in model:model[entity][1]=float(step%97)
                else:gc.collect() if step%127==0 else None
                actual=b.snapshot();self.assertEqual(actual[0],list(model));self.assertEqual(actual[1],[tuple(v) for v in model.values()])
        except Exception as e:
            raise AssertionError(f'seed={seed} step={step} last_actions={trace}') from e

for seed in (1,7,42,4096,65537,0x50525234,0xdeadbeef,0xffffffff):
    def case(self,s=seed):self._state_machine(s)
    setattr(RustFFIStress,f'test_state_machine_seed_{seed}',case)
if __name__=='__main__':unittest.main()
