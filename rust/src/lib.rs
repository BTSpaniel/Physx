// SPDX-License-Identifier: MIT
//! Fixed-buffer batching for a PhysX adapter. This crate contains NO solver.
//!
//! Storage is owned by the C++/Python caller. The Emscripten build deliberately
//! uses only core: no Rust std/allocator, threads, filesystem, or unwinding.
//! C ABI functions take only scalars and pointers; no Rust types cross the ABI.
//! All unsafe contracts below still apply to foreign callers. Rust cannot
//! validate a stale PhysX pointer supplied by another language.
#![cfg_attr(target_os = "emscripten", no_std)]
#![deny(unsafe_op_in_unsafe_fn)]

use core::{ffi::c_void, mem, ptr};

pub const OK: i32 = 0;
pub const INVALID: i32 = -1;
pub const FULL: i32 = -2;
pub const DUPLICATE: i32 = -3;
pub const NOT_FOUND: i32 = -4;
pub const READ_FAILED: i32 = -5;
pub const STRIDE: usize = 7;

#[cfg(target_os = "emscripten")]
#[panic_handler]
fn panic(_: &core::panic::PanicInfo<'_>) -> ! {
    core::arch::wasm32::unreachable()
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct PrEntry {
    pub id: u32,
    pub reserved: u32,
    pub actor: *mut c_void,
}

/// Layout shared with bridge/pr_rust_core.h. Do not edit from foreign code.
#[repr(C)]
pub struct PrCache {
    pub entries: *mut PrEntry,
    pub ids: *mut u32,
    pub poses: *mut f32,
    pub scratch_ids: *mut u32,
    pub scratch_poses: *mut f32,
    pub capacity: u32,
    pub count: u32,
    pub published: u32,
    pub initialized: u32,
}

pub type PoseReader = unsafe extern "C" fn(*mut c_void, *mut f32) -> i32;
const INIT: u32 = 0x50525232;

fn region<T>(p: *const T, len: usize) -> Option<(usize, usize)> {
    let start = p as usize;
    if p.is_null() || start % mem::align_of::<T>() != 0 {
        return None;
    }
    let bytes = len.checked_mul(mem::size_of::<T>())?;
    if bytes == 0 || bytes > isize::MAX as usize {
        return None;
    }
    Some((start, start.checked_add(bytes)?))
}

/// Return whether capacity arithmetic is representable. This is not an
/// allocation attempt, and does not prove that supplied pointers are valid.
#[no_mangle]
pub extern "C" fn prr_capacity_valid(capacity: u32) -> u32 {
    let n = capacity as usize;
    let max = isize::MAX as usize;
    let fits = capacity > 0
        && n.checked_mul(mem::size_of::<PrEntry>()).map_or(false, |v| v <= max)
        && n.checked_mul(STRIDE * mem::size_of::<f32>()).map_or(false, |v| v <= max);
    fits as u32
}

#[no_mangle]
pub extern "C" fn prr_abi() -> u32 { 2 }
#[no_mangle]
pub extern "C" fn prr_pointer_size() -> u32 { mem::size_of::<usize>() as u32 }
#[no_mangle]
pub extern "C" fn prr_entry_size() -> u32 { mem::size_of::<PrEntry>() as u32 }
#[no_mangle]
pub extern "C" fn prr_cache_size() -> u32 { mem::size_of::<PrCache>() as u32 }
#[no_mangle]
pub extern "C" fn prr_simd128_enabled() -> u32 { cfg!(target_feature = "simd128") as u32 }

/// Fill caller-owned staging memory with a deterministic sequence before a
/// WebGPU upload. The optimized WASM build enables simd128 for this loop.
///
/// # Safety
/// values must denote `count` writable, aligned u32 values with exclusive
/// access for the duration of this call.
#[no_mangle]
pub unsafe extern "C" fn prr_flow_seed(values: *mut u32, count: u32) -> i32 {
    if region(values, count as usize).is_none() { return INVALID; }
    for index in 0..count as usize {
        let value = (index as u32).wrapping_mul(3).wrapping_add(1);
        unsafe { values.add(index).write(value); }
    }
    OK
}

/// Verify the WGSL smoke transform `value = value * 2 + 5` after readback.
///
/// # Safety
/// values must denote `count` readable, aligned u32 values and remain live for
/// the duration of this call.
#[no_mangle]
pub unsafe extern "C" fn prr_flow_verify(values: *const u32, count: u32) -> i32 {
    if region(values, count as usize).is_none() { return INVALID; }
    for index in 0..count as usize {
        let seeded = (index as u32).wrapping_mul(3).wrapping_add(1);
        let expected = seeded.wrapping_mul(2).wrapping_add(5);
        if unsafe { values.add(index).read() } != expected { return READ_FAILED; }
    }
    OK
}

/// Initialize caller-owned, mutually disjoint buffers. Entry capacity is n;
/// ID capacities are n; pose capacities are n*7 floats. No allocations occur.
///
/// # Safety
/// Every non-null pointer must denote its stated, writable allocation. The
/// context and all five buffers must outlive every later call. Do not initialize
/// a live cache concurrently. No buffer may overlap any other or the context.
#[no_mangle]
pub unsafe extern "C" fn prr_init(
    cache: *mut PrCache, capacity: u32, entries: *mut PrEntry,
    ids: *mut u32, poses: *mut f32, scratch_ids: *mut u32, scratch_poses: *mut f32,
) -> i32 {
    if prr_capacity_valid(capacity) == 0 { return INVALID; }
    let n = capacity as usize;
    let regions = [region(cache, 1), region(entries, n), region(ids, n),
                   region(poses, n * STRIDE), region(scratch_ids, n),
                   region(scratch_poses, n * STRIDE)];
    for i in 0..regions.len() {
        let (a, b) = match regions[i] { Some(v) => v, None => return INVALID };
        for other in &regions[..i] {
            if let Some((c, d)) = *other {
                if a < d && c < b { return INVALID; }
            }
        }
    }
    // SAFETY: allocation/lifetime comes from caller; disjointness/alignment and
    // representable lengths were checked above. No buffer contents read here.
    unsafe { ptr::write(cache, PrCache {
        entries, ids, poses, scratch_ids, scratch_poses,
        capacity, count: 0, published: 0, initialized: INIT,
    }); }
    OK
}

unsafe fn cache_mut<'a>(raw: *mut PrCache) -> Option<&'a mut PrCache> {
    region(raw, 1)?;
    // SAFETY: all exported functions require live, exclusive cache storage.
    let c = unsafe { &mut *raw };
    if c.initialized != INIT || c.count > c.capacity || c.published > c.capacity {
        None
    } else { Some(c) }
}

/// # Safety
/// cache must be live and exclusively accessed. actor must remain a valid
/// object for the eventual reader until removal; it is borrowed, never freed.
#[no_mangle]
pub unsafe extern "C" fn prr_add(raw: *mut PrCache, id: u32, actor: *mut c_void) -> i32 {
    if id == 0 || actor.is_null() { return INVALID; }
    let c = match unsafe { cache_mut(raw) } { Some(c) => c, None => return INVALID };
    for i in 0..c.count as usize {
        let e = unsafe { *c.entries.add(i) };
        if e.id == id || e.actor == actor { return DUPLICATE; }
    }
    if c.count == c.capacity { return FULL; }
    unsafe { c.entries.add(c.count as usize).write(PrEntry { id, reserved: 0, actor }); }
    c.count += 1;
    // Registration changes invalidate the published view until a new snapshot.
    c.published = 0;
    OK
}

/// # Safety
/// raw must be a live, exclusively owned cache. Remove BEFORE releasing actor.
#[no_mangle]
pub unsafe extern "C" fn prr_remove(raw: *mut PrCache, id: u32) -> i32 {
    let c = match unsafe { cache_mut(raw) } { Some(c) => c, None => return INVALID };
    if id == 0 { return INVALID; }
    for i in 0..c.count as usize {
        if unsafe { (*c.entries.add(i)).id } == id {
            let remaining = c.count as usize - i - 1;
            // ptr::copy intentionally permits overlap. Preserve insertion order.
            unsafe { ptr::copy(c.entries.add(i + 1), c.entries.add(i), remaining); }
            c.count -= 1;
            c.published = 0;
            return OK;
        }
    }
    NOT_FOUND
}

/// Capture all poses transactionally. On callback failure or a non-finite
/// component the previous published snapshot is unchanged. IDs never use f32.
///
/// # Safety
/// raw/buffers must be live and exclusively accessed. reader must return 0
/// on success, write exactly 7 floats and not unwind, retain pointers, mutate
/// this cache, call back into it, or destroy any registered object. Invoke only
/// after the owning scene's fetchResults() completes, not during simulation.
#[no_mangle]
pub unsafe extern "C" fn prr_snapshot(raw: *mut PrCache, reader: Option<PoseReader>) -> i32 {
    let read = match reader { Some(r) => r, None => return INVALID };
    let c = match unsafe { cache_mut(raw) } { Some(c) => c, None => return INVALID };
    for i in 0..c.count as usize {
        let entry = unsafe { *c.entries.add(i) };
        let out = unsafe { c.scratch_poses.add(i * STRIDE) };
        // Initialize before the foreign call so an incomplete reader cannot
        // leave uninitialized bytes that Rust might subsequently read.
        for j in 0..STRIDE { unsafe { out.add(j).write(f32::NAN); } }
        if unsafe { read(entry.actor, out) } != OK { return READ_FAILED; }
        for j in 0..STRIDE {
            if !unsafe { *out.add(j) }.is_finite() { return READ_FAILED; }
        }
        unsafe { c.scratch_ids.add(i).write(entry.id); }
    }
    // Addresses stay stable. This is transactional error handling, NOT an atomic
    // cross-thread publication mechanism. Native callers must serialize access.
    unsafe {
        ptr::copy_nonoverlapping(c.scratch_ids, c.ids, c.count as usize);
        ptr::copy_nonoverlapping(c.scratch_poses, c.poses, c.count as usize * STRIDE);
    }
    c.published = c.count;
    OK
}

/// # Safety
/// raw must be null or a live, exclusively accessed cache. Buffers are not freed.
#[no_mangle]
pub unsafe extern "C" fn prr_clear(raw: *mut PrCache) -> i32 {
    let c = match unsafe { cache_mut(raw) } { Some(c) => c, None => return INVALID };
    c.count = 0;
    c.published = 0;
    OK
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Owned {
        context: Box<PrCache>,
        _entries: Vec<PrEntry>,
        ids: Vec<u32>,
        poses: Vec<f32>,
        _scratch_ids: Vec<u32>,
        _scratch_poses: Vec<f32>,
    }
    impl Owned {
        fn new(capacity: u32) -> Self {
            let n = capacity as usize;
            let mut e = vec![PrEntry { id: 0, reserved: 0, actor: ptr::null_mut() }; n];
            let mut ids = vec![0; n];
            let mut poses = vec![0.0; n * STRIDE];
            let mut si = vec![0; n];
            let mut sp = vec![0.0; n * STRIDE];
            let mut ctx = Box::<PrCache>::new(unsafe { mem::zeroed() });
            assert_eq!(unsafe { prr_init(&mut *ctx, capacity, e.as_mut_ptr(),
                ids.as_mut_ptr(), poses.as_mut_ptr(), si.as_mut_ptr(), sp.as_mut_ptr()) }, OK);
            Self { context: ctx, _entries: e, ids, poses, _scratch_ids: si, _scratch_poses: sp }
        }
        fn add(&mut self, id: u32, pose: &mut [f32; 7]) -> i32 {
            unsafe { prr_add(&mut *self.context, id, pose.as_mut_ptr().cast()) }
        }
        fn snap(&mut self) -> i32 { unsafe { prr_snapshot(&mut *self.context, Some(reader)) } }
    }
    // Synthetic poses for buffer tests, deliberately NOT PhysX actor stand-ins.
    unsafe extern "C" fn reader(actor: *mut c_void, out: *mut f32) -> i32 {
        unsafe { ptr::copy_nonoverlapping(actor.cast::<f32>(), out, 7); }
        OK
    }
    fn pose(y: f32) -> [f32; 7] { [0.0, y, 0.0, 0.0, 0.0, 0.0, 1.0] }
    #[test] fn abi_layout() {
        assert_eq!(prr_abi(), 2);
        assert_eq!(prr_pointer_size() as usize, mem::size_of::<usize>());
        assert_eq!(prr_entry_size() as usize, mem::size_of::<PrEntry>());
        assert_eq!(prr_cache_size() as usize, mem::size_of::<PrCache>());
    }
    #[test] fn flow_webgpu_staging_contract() {
        let mut values = vec![0u32; 257];
        assert_eq!(unsafe { prr_flow_seed(values.as_mut_ptr(), values.len() as u32) }, OK);
        for value in &mut values { *value = value.wrapping_mul(2).wrapping_add(5); }
        assert_eq!(unsafe { prr_flow_verify(values.as_ptr(), values.len() as u32) }, OK);
        values[128] ^= 1;
        assert_eq!(unsafe { prr_flow_verify(values.as_ptr(), values.len() as u32) }, READ_FAILED);
    }
    #[test] fn zero_capacity_rejected() {
        assert_eq!(prr_capacity_valid(0), 0);
        assert_eq!(unsafe { prr_init(ptr::null_mut(), 0, ptr::null_mut(), ptr::null_mut(),
            ptr::null_mut(), ptr::null_mut(), ptr::null_mut()) }, INVALID);
    }
    #[test] fn empty_snapshot() {
        let mut c = Owned::new(1);
        assert_eq!(c.snap(), OK);
        assert_eq!(c.context.published, 0);
    }
    #[test] fn full_uint32_ids_exact() {
        let mut c = Owned::new(2);
        let mut a = pose(1.0); let mut b = pose(2.0);
        assert_eq!(c.add(u32::MAX, &mut a), OK);
        assert_eq!(c.add(16_777_217, &mut b), OK);
        assert_eq!(c.snap(), OK);
        assert_eq!(c.ids, vec![u32::MAX, 16_777_217]);
    }
    #[test] fn duplicate_id_and_actor_rejected() {
        let mut c = Owned::new(2);
        let mut a = pose(1.0); let mut b = pose(2.0);
        assert_eq!(c.add(1, &mut a), OK);
        assert_eq!(c.add(1, &mut b), DUPLICATE);
        assert_eq!(c.add(2, &mut a), DUPLICATE);
    }
    #[test] fn capacity_enforced() {
        let mut c = Owned::new(1); let mut a = pose(1.0); let mut b = pose(2.0);
        assert_eq!(c.add(1, &mut a), OK);
        assert_eq!(c.add(2, &mut b), FULL);
    }
    #[test] fn removal_preserves_order() {
        let mut c = Owned::new(3);
        let mut a = pose(1.0); let mut b = pose(2.0); let mut d = pose(3.0);
        c.add(1, &mut a); c.add(2, &mut b); c.add(3, &mut d);
        assert_eq!(unsafe { prr_remove(&mut *c.context, 2) }, OK);
        assert_eq!(c.snap(), OK);
        assert_eq!(&c.ids[..2], &[1, 3]);
    }
    #[test] fn unknown_removal() {
        let mut c = Owned::new(1);
        assert_eq!(unsafe { prr_remove(&mut *c.context, 99) }, NOT_FOUND);
    }
    #[test] fn failed_snapshot_preserves_publication() {
        let mut c = Owned::new(2); let mut a = pose(1.0); let mut b = pose(2.0);
        c.add(1, &mut a); c.add(2, &mut b); assert_eq!(c.snap(), OK);
        let old = c.poses.clone(); a[1] = 9.0; b[3] = f32::NAN;
        assert_eq!(c.snap(), READ_FAILED);
        assert_eq!(c.poses, old);
        assert_eq!(c.context.published, 2);
    }
    #[test] fn registration_invalidates_publication() {
        let mut c = Owned::new(2); let mut a = pose(1.0); let mut b = pose(2.0);
        c.add(1, &mut a); c.snap(); assert_eq!(c.context.published, 1);
        c.add(2, &mut b); assert_eq!(c.context.published, 0);
    }
    #[test] fn missing_reader_rejected() {
        let mut c = Owned::new(1);
        assert_eq!(unsafe { prr_snapshot(&mut *c.context, None) }, INVALID);
    }
    #[test] fn overlapping_buffers_rejected() {
        let mut c = Owned::new(1);
        let p = &mut *c.context;
        let (entries, ids, poses, scratch_poses) = (p.entries, p.ids, p.poses, p.scratch_poses);
        assert_eq!(unsafe { prr_init(p, 1, entries, ids, poses, ids, scratch_poses) }, INVALID);
    }
    #[test] fn clear_reuses_storage() {
        let mut c = Owned::new(1); let mut a = pose(1.0); let mut b = pose(2.0);
        c.add(1, &mut a); c.snap();
        assert_eq!(unsafe { prr_clear(&mut *c.context) }, OK);
        assert_eq!(c.context.published, 0);
        assert_eq!(c.add(2, &mut b), OK);
    }
    #[test] fn null_arguments_rejected() {
        assert_eq!(unsafe { prr_add(ptr::null_mut(), 1, ptr::null_mut()) }, INVALID);
        assert_eq!(unsafe { prr_remove(ptr::null_mut(), 1) }, INVALID);
        assert_eq!(unsafe { prr_clear(ptr::null_mut()) }, INVALID);
    }
    #[test] fn infinite_pose_rejected() {
        let mut c = Owned::new(1); let mut a = pose(f32::INFINITY);
        c.add(1, &mut a); assert_eq!(c.snap(), READ_FAILED);
    }
    #[test] fn incomplete_callback_rejected() {
        unsafe extern "C" fn incomplete(_: *mut c_void, _: *mut f32) -> i32 { OK }
        let mut c = Owned::new(1); let mut a = pose(1.0); c.add(1, &mut a);
        assert_eq!(unsafe { prr_snapshot(&mut *c.context, Some(incomplete)) }, READ_FAILED);
    }
}

#[cfg(test)]
mod regression_tests;
