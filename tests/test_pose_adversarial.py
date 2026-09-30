"""Adversarial tests of actual Python validation/ownership, not native physics.

No fake library is loaded. Callback-unit tests instantiate only the Python
object fields used by that code and pass live ctypes output arrays.
"""
from __future__ import annotations
import ctypes as C
import itertools
import math
import random
import struct
import sys
import threading
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import pr_pose as p

class InputAdversarial(unittest.TestCase):
    def test_infinite_yielding_iterable_is_bounded(self):
        with self.assertRaises(ValueError):
            p.pose7(itertools.repeat(0.0))
    def test_ninth_value_is_never_consumed(self):
        reads=[]
        def stream():
            for i in range(9):
                reads.append(i)
                if i == 8: raise AssertionError('Ninth item must not be requested')
                yield 0
        with self.assertRaises(ValueError): p.pose7(stream())
        self.assertEqual(reads, list(range(8)))
    def test_exactly_seven_generator_values(self):
        self.assertEqual(p.pose7(float(i) for i in range(7)), tuple(range(7)))
    def test_zero_and_negative_zero_preserved(self):
        values=p.pose7([-0.0,0,0,0,0,0,1])
        self.assertEqual(math.copysign(1,values[0]), -1)
    def test_largest_float32_roundtrip(self):
        f=struct.unpack('<f',bytes.fromhex('ffff7f7f'))[0]
        self.assertEqual(p.pose7([f,-f,0,0,0,0,1])[:2], (f,-f))
    def test_subnormal_not_promised_flush_to_zero(self):
        f=struct.unpack('<f',bytes.fromhex('01000000'))[0]
        self.assertEqual(p.pose7([f,0,0,0,0,0,1])[0], f)
    def test_input_is_not_modified(self):
        source=[0.1,2,3,4,5,6,7];before=source.copy();p.pose7(source)
        self.assertEqual(source,before)
    def test_buffer_layer_does_not_silently_normalize_quaternion(self):
        self.assertEqual(p.pose7([1,2,3,2,3,4,5])[3:], (2,3,4,5))
    def test_iterator_exception_is_not_hidden(self):
        def broken():
            yield 0
            raise LookupError('source error')
        with self.assertRaisesRegex(LookupError,'source error'):p.pose7(broken())
    def test_random_f32_roundtrip_bits(self):
        rng=random.Random(0x50525234)
        for case in range(4096):
            bits=rng.getrandbits(32)
            if bits & 0x7f800000 == 0x7f800000: continue
            x=struct.unpack('<f',struct.pack('<I',bits))[0]
            actual=p.pose7([x,0,0,0,0,0,1])[0]
            self.assertEqual(struct.pack('<f',actual),struct.pack('<I',bits),f'case={case} bits={bits:x}')
    def test_random_uint32_exact(self):
        rng=random.Random(701)
        for _ in range(4096):
            n=rng.randrange(1,2**32);self.assertEqual(p.uint32(n),n)

# Each boundary pair is reported as a separately named test rather than hidden
# behind a headline that incorrectly counts loop iterations as unit tests.
def nonfinite_test(component,value):
    def test(self):
        data=[0.,0.,0.,0.,0.,0.,1.];data[component]=value
        with self.assertRaises(ValueError):p.pose7(data)
    return test
for i in range(7):
    for label,value in [('nan',math.nan),('positive_inf',math.inf),('negative_inf',-math.inf),('overflow',1e39)]:
        setattr(InputAdversarial,f'test_component_{i}_{label}',nonfinite_test(i,value))

class CallbackAdversarial(unittest.TestCase):
    def fixture(self, fn):
        b=p.PoseBatch.__new__(p.PoseBatch)
        b._readers={123:fn};b._error=None;b._owner=threading.current_thread()
        b._closed=False;b._busy=True
        out=(p.F32*9)(*([42]*9))
        ptr=C.cast(C.byref(out,C.sizeof(p.F32)),C.POINTER(p.F32))
        return b,out,ptr
    def test_reader_writes_exact_seven_components_and_no_canaries(self):
        b,out,ptr=self.fixture(lambda:range(7))
        self.assertEqual(b._read(123,ptr),0)
        self.assertEqual(list(out),[42,0,1,2,3,4,5,6,42])
    def test_unknown_actor_caught_before_writing(self):
        b,out,ptr=self.fixture(lambda:range(7))
        self.assertEqual(b._read(999,ptr),-5)
        self.assertIsInstance(b._error,KeyError);self.assertEqual(list(out),[42]*9)
    def test_validation_finishes_before_writing_any_output(self):
        b,out,ptr=self.fixture(lambda:[1,2,3,4,5,6,math.inf])
        self.assertEqual(b._read(123,ptr),-5);self.assertEqual(list(out),[42]*9)
    def test_baseexception_is_contained_at_ffi_boundary(self):
        for error in (KeyboardInterrupt('stop'),SystemExit(9),MemoryError('allocation')):
            def fail(e=error):raise e
            b,out,ptr=self.fixture(fail)
            self.assertEqual(b._read(123,ptr),-5);self.assertIs(b._error,error)
            self.assertEqual(list(out),[42]*9)
    def test_infinite_reader_returns_error_without_buffer_write(self):
        b,out,ptr=self.fixture(lambda:itertools.repeat(1))
        self.assertEqual(b._read(123,ptr),-5);self.assertEqual(list(out),[42]*9)
    def test_reentrant_close_is_contained(self):
        b,out,ptr=self.fixture(lambda:None);b._readers[123]=lambda:b.close()
        self.assertEqual(b._read(123,ptr),-5)
        self.assertFalse(b._closed);self.assertIsInstance(b._error,p.PoseError)
    def test_reentrant_remove_is_contained(self):
        b,out,ptr=self.fixture(lambda:None);b._readers[123]=lambda:b.remove(1)
        self.assertEqual(b._read(123,ptr),-5);self.assertEqual(list(out),[42]*9)
    def test_invalid_pose_does_not_escape_ctypes_callback(self):
        b,out,ptr=self.fixture(lambda:[1,2])
        callback=p.READER(b._read)
        self.assertEqual(callback(123,ptr),-5);self.assertIsInstance(b._error,ValueError)

if __name__=='__main__':unittest.main()
