"""ctypes client for the real native Rust pose-buffer library, not a simulator.

PoseBatch owns arrays, actor-token storage and callbacks. Readers return copied
seven-component poses; no Python objects or native PhysX pointers cross as f32.
No pip dependencies. All operations, including close, stay on the creating thread.
"""
from __future__ import annotations
import ctypes as C
import math
from itertools import islice
import struct
import threading
from pathlib import Path
from typing import Callable, Iterable

U32 = C.c_uint32
F32 = C.c_float
PTR = C.c_void_p

class Entry(C.Structure):
    _fields_ = [('id', U32), ('reserved', U32), ('actor', PTR)]

class Cache(C.Structure):
    _fields_ = [('entries', C.POINTER(Entry)), ('ids', C.POINTER(U32)),
                ('poses', C.POINTER(F32)), ('scratch_ids', C.POINTER(U32)),
                ('scratch_poses', C.POINTER(F32)), ('capacity', U32),
                ('count', U32), ('published', U32), ('initialized', U32)]

READER = C.CFUNCTYPE(C.c_int32, PTR, C.POINTER(F32))
ERRORS = {-1: 'invalid argument', -2: 'capacity exhausted', -3: 'duplicate ID or actor',
          -4: 'unknown entity ID', -5: 'pose reader failed or returned a non-finite pose'}

class PoseError(RuntimeError):
    pass

def uint32(value: int, name: str = 'value') -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 0xFFFFFFFF:
        raise ValueError(f'{name} must be a nonzero uint32')
    return value

def pose7(values: Iterable[float]) -> tuple[float, ...]:
    # A pose is exactly seven values. Read at most eight so an oversized or
    # infinite *yielding* iterable cannot monopolize the owning simulation thread.
    # A reader that blocks inside __next__ still needs process/worker timeout.
    data = tuple(float(v) for v in islice(iter(values), 8))
    if len(data) != 7 or not all(math.isfinite(v) for v in data):
        raise ValueError('Expected seven finite pose components: px,py,pz,qx,qy,qz,qw')
    try:
        # Reject overflow before casting to ctypes float; preserve f32 rounding.
        rounded = struct.unpack('<7f', struct.pack('<7f', *data))
    except (OverflowError, struct.error) as e:
        raise ValueError('Pose component exceeds float32 range') from e
    if not all(math.isfinite(v) for v in rounded):
        raise ValueError('Pose component exceeds float32 range')
    return rounded

def load_library(path: str | Path) -> C.CDLL:
    path = Path(path).expanduser().resolve(strict=True)
    lib = C.CDLL(str(path))
    for name in ('abi', 'pointer_size', 'entry_size', 'cache_size'):
        f = getattr(lib, f'prr_{name}'); f.argtypes = []; f.restype = U32
    checks = [(lib.prr_abi(), 2), (lib.prr_pointer_size(), C.sizeof(PTR)),
              (lib.prr_entry_size(), C.sizeof(Entry)), (lib.prr_cache_size(), C.sizeof(Cache))]
    if any(actual != expected for actual, expected in checks):
        raise PoseError('Rust/C/Python ABI or pointer-width mismatch')
    p = C.POINTER(Cache)
    signatures = {
        'capacity_valid': ([U32], U32),
        'init': ([p, U32, C.POINTER(Entry), C.POINTER(U32), C.POINTER(F32),
                  C.POINTER(U32), C.POINTER(F32)], C.c_int32),
        'add': ([p, U32, PTR], C.c_int32), 'remove': ([p, U32], C.c_int32),
        'snapshot': ([p, READER], C.c_int32), 'clear': ([p], C.c_int32),
    }
    for name, (args, result) in signatures.items():
        f = getattr(lib, f'prr_{name}'); f.argtypes = args; f.restype = result
    return lib

class PoseBatch:
    """Read entity poses into a Rust-validated batch using Python callbacks.

    Native test/tooling facility. For the browser, use the existing PhysXBulk
    module, which invokes the C++ reader instead of a Python callback.
    IDs are never converted to floats. snapshot() always returns owned copies.
    """
    def __init__(self, library: str | Path, capacity: int = 4096):
        uint32(capacity, 'capacity')
        self._owner = threading.current_thread()
        self._closed = True
        self._busy = False
        self._readers: dict[int, Callable[[], Iterable[float]]] = {}
        self._tokens: dict[int, C.c_uint32] = {}
        self._error: BaseException | None = None
        self._lib = load_library(library)
        if not self._lib.prr_capacity_valid(capacity):
            raise ValueError('Capacity overflows the native address space')
        self._entries = (Entry * capacity)()
        self._ids = (U32 * capacity)()
        self._poses = (F32 * (capacity * 7))()
        self._scratch_ids = (U32 * capacity)()
        self._scratch_poses = (F32 * (capacity * 7))()
        self._cache = Cache()
        self._reader = READER(self._read)
        self._check(self._lib.prr_init(C.byref(self._cache), capacity, self._entries,
            self._ids, self._poses, self._scratch_ids, self._scratch_poses))
        self._closed = False

    def _live(self) -> None:
        if threading.current_thread() is not self._owner:
            raise PoseError('Use PoseBatch only from its creating thread')
        if self._closed:
            raise PoseError('PoseBatch is closed')
        if self._busy:
            raise PoseError('Reentrant access from a pose reader is prohibited')

    @staticmethod
    def _check(status: int) -> None:
        if status:
            raise PoseError(ERRORS.get(status, f'Native error {status}'))

    def _read(self, actor: int, out: C.POINTER(F32)) -> int:
        # Exception handling is mandatory: exceptions must not escape ctypes
        # callbacks, and callbacks must not recursively access the same batch.
        try:
            values = pose7(self._readers[int(actor)]())
            for i, value in enumerate(values):
                out[i] = value
            return 0
        except BaseException as e:
            self._error = e
            return -5

    def add(self, entity_id: int, reader: Callable[[], Iterable[float]]) -> None:
        self._live(); uint32(entity_id, 'entity_id')
        if not callable(reader):
            raise TypeError('reader must be callable')
        if entity_id in self._tokens:
            raise PoseError('duplicate ID')
        token = U32(entity_id)
        address = C.addressof(token)
        # Install Python ownership first. Roll back if native registration fails.
        try:
            self._tokens[entity_id] = token
            self._readers[address] = reader
            self._check(self._lib.prr_add(C.byref(self._cache), entity_id, address))
        except BaseException:
            self._tokens.pop(entity_id, None); self._readers.pop(address, None)
            raise

    def remove(self, entity_id: int) -> None:
        self._live(); uint32(entity_id, 'entity_id')
        self._check(self._lib.prr_remove(C.byref(self._cache), entity_id))
        token = self._tokens.pop(entity_id)
        self._readers.pop(C.addressof(token))

    def snapshot(self) -> tuple[list[int], list[tuple[float, ...]]]:
        self._live()
        self._error = None
        self._busy = True
        try:
            status = self._lib.prr_snapshot(C.byref(self._cache), self._reader)
        finally:
            self._busy = False
        if self._error is not None:
            raise PoseError('Pose reader failed; previous published buffer retained') from self._error
        self._check(status)
        n = self._cache.published
        if n > self._cache.capacity:
            raise PoseError('Invalid native published length')
        return list(self._ids[:n]), [tuple(self._poses[i*7:i*7+7]) for i in range(n)]

    def close(self) -> None:
        if self._closed:
            return
        self._live()
        self._check(self._lib.prr_clear(C.byref(self._cache)))
        self._closed = True
        self._readers.clear(); self._tokens.clear()

    def __enter__(self) -> 'PoseBatch':
        self._live(); return self

    def __exit__(self, *_: object) -> None:
        self.close()
