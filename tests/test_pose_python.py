"""Python-only validation/layout tests. These do not execute Rust or PhysX."""
import ctypes as C
import importlib.util
import math
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pose_python', ROOT / 'python/pr_pose.py')
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)

class PythonPose(unittest.TestCase):
    def test_uint32_max_exact(self): self.assertEqual(p.uint32(0xffffffff), 4294967295)
    def test_uint32_above_float32_precision_exact(self): self.assertEqual(p.uint32(16777217), 16777217)
    def test_zero_id_rejected(self):
        with self.assertRaises(ValueError): p.uint32(0)
    def test_negative_id_rejected(self):
        with self.assertRaises(ValueError): p.uint32(-1)
    def test_oversized_id_rejected(self):
        with self.assertRaises(ValueError): p.uint32(0x100000000)
    def test_float_id_rejected(self):
        with self.assertRaises(ValueError): p.uint32(1.0)
    def test_bool_id_rejected(self):
        with self.assertRaises(ValueError): p.uint32(True)
    def test_pose_layout(self): self.assertEqual(p.pose7([1,2,3,0,0,0,1]), (1,2,3,0,0,0,1))
    def test_pose_wrong_length(self):
        for size in [0, 6, 8]:
            with self.subTest(size=size), self.assertRaises(ValueError): p.pose7([0]*size)
    def test_nan_rejected(self):
        with self.assertRaises(ValueError): p.pose7([float('nan'),0,0,0,0,0,1])
    def test_infinity_rejected(self):
        for n in [float('inf'), float('-inf')]:
            with self.subTest(n=n), self.assertRaises(ValueError): p.pose7([n,0,0,0,0,0,1])
    def test_float32_overflow_rejected(self):
        with self.assertRaises(ValueError): p.pose7([1e39,0,0,0,0,0,1])
    def test_float32_rounding(self): self.assertEqual(p.pose7([0.1,0,0,0,0,0,1])[0], C.c_float(0.1).value)
    def test_missing_library_rejected(self):
        with self.assertRaises(FileNotFoundError): p.load_library(ROOT / 'deliberately-missing-library.so')
    def test_entry_layout(self):
        self.assertEqual(C.sizeof(p.Entry), 8 + C.sizeof(C.c_void_p))
        self.assertEqual(p.Entry.actor.offset, 8)
    def test_cache_layout(self):
        self.assertEqual(C.sizeof(p.Cache), 16 + 5*C.sizeof(C.c_void_p))
        self.assertEqual(p.Cache.capacity.offset, 5*C.sizeof(C.c_void_p))
    def test_no_solver_in_python(self):
        self.assertNotIn('simulate', p.PoseBatch.__dict__)
    def test_invalid_capacity_before_library_load(self):
        with self.assertRaises(ValueError): p.PoseBatch('missing.dll', capacity=0)

if __name__ == '__main__': unittest.main()
