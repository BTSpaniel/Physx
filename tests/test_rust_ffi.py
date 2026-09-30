"""Tests against the ACTUAL compiled Rust dynamic library, never a fake backend.

Skipped visibly when PR_RUST_LIB isn't set. build.py native/test sets it only
AFTER compiling the Rust source during that invocation. No PhysX solver used.
"""
import ctypes as C
import os
import sys
import threading
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from pr_pose import PoseBatch, PoseError

@unittest.skipUnless(os.environ.get('PR_RUST_LIB'), 'Rust library not compiled; native FFI tests NOT RUN')
class NativeRustFFI(unittest.TestCase):
    def setUp(self): self.batch = PoseBatch(os.environ['PR_RUST_LIB'], capacity=3)
    def tearDown(self): self.batch.close()
    def add(self, n, y=2): self.batch.add(n, lambda: [0,y,0,0,0,0,1])
    def test_real_abi(self): self.assertEqual(self.batch._lib.prr_abi(), 2)
    def test_empty(self): self.assertEqual(self.batch.snapshot(), ([], []))
    def test_id_and_pose_roundtrip(self):
        self.add(0xffffffff)
        ids, poses = self.batch.snapshot()
        self.assertEqual(ids, [0xffffffff]); self.assertEqual(poses[0][1], 2.0)
    def test_large_id_precision(self):
        self.add(16777217); self.assertEqual(self.batch.snapshot()[0], [16777217])
    def test_duplicate_id(self):
        self.add(1)
        with self.assertRaises(PoseError): self.add(1)
    def test_capacity_full(self):
        for i in range(1,4): self.add(i)
        with self.assertRaises(PoseError): self.add(4)
        self.assertEqual(self.batch.snapshot()[0], [1,2,3])
    def test_remove_preserves_order(self):
        for i in range(1,4): self.add(i)
        self.batch.remove(2); self.assertEqual(self.batch.snapshot()[0], [1,3])
    def test_remove_unknown(self):
        with self.assertRaises(PoseError): self.batch.remove(99)
    def test_publication_preserved_on_failure(self):
        pose = [0,2,0,0,0,0,1]
        self.batch.add(1, lambda: pose)
        self.batch.snapshot(); old = list(self.batch._poses[:7])
        pose[1] = float('nan')
        with self.assertRaises(PoseError): self.batch.snapshot()
        self.assertEqual(list(self.batch._poses[:7]), old)
    def test_callback_exception_caught(self):
        def broken(): raise ValueError('reader failure')
        self.batch.add(1, broken)
        with self.assertRaises(PoseError) as context: self.batch.snapshot()
        self.assertIsInstance(context.exception.__cause__, ValueError)
    def test_reentrant_reader_rejected(self):
        self.batch.add(1, lambda: self.batch.snapshot())
        with self.assertRaises(PoseError): self.batch.snapshot()
    def test_snapshot_is_copy(self):
        values = [0,2,0,0,0,0,1]; self.batch.add(1, lambda: values)
        old = self.batch.snapshot(); values[1] = 9
        self.assertEqual(self.batch.snapshot()[1][0][1], 9)
        self.assertEqual(old[1][0][1], 2)
    def test_thread_rejected_before_ffi(self):
        errors = []
        def other():
            try: self.batch.snapshot()
            except PoseError as e: errors.append(str(e))
        thread = threading.Thread(target=other); thread.start(); thread.join()
        self.assertEqual(len(errors), 1)
    def test_close_idempotent(self):
        self.batch.close(); self.batch.close()
        with self.assertRaises(PoseError): self.batch.snapshot()
    def test_overflowing_float_rejected(self):
        self.add(1, 1e39)
        with self.assertRaises(PoseError): self.batch.snapshot()
    def test_callback_lifetime_kept(self):
        import gc
        for i in range(1,4): self.add(i)
        gc.collect(); self.assertEqual(self.batch.snapshot()[0], [1,2,3])

if __name__ == '__main__': unittest.main()
