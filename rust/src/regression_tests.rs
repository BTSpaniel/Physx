// SPDX-License-Identifier: MIT
//! Tests of the actual Rust buffer core, not PhysX. No additional crates.
//! Fixtures own every allocation; no stale/freed pointer is dereferenced.
use super::*;

struct Owned {
    cache: Box<PrCache>,
    entries: Vec<PrEntry>,
    ids: Vec<u32>,
    poses: Vec<f32>,
    scratch_ids: Vec<u32>,
    scratch_poses: Vec<f32>,
    n: usize,
}
const GUARD_ID: u32 = 0xa5c35a3c;
const GUARD_POSE: f32 = 12345.25;
impl Owned {
    fn new(n: usize) -> Self {
        let mut value = Self {
            cache: Box::new(unsafe { mem::zeroed() }),
            entries: vec![PrEntry {id: GUARD_ID, reserved: GUARD_ID, actor: ptr::null_mut()}; n+2],
            ids: vec![GUARD_ID; n+2], poses: vec![GUARD_POSE; n*7+2],
            scratch_ids: vec![GUARD_ID; n+2], scratch_poses: vec![GUARD_POSE; n*7+2], n,
        };
        assert_eq!(unsafe { prr_init(&mut *value.cache, n as u32,
            value.entries.as_mut_ptr().add(1), value.ids.as_mut_ptr().add(1),
            value.poses.as_mut_ptr().add(1), value.scratch_ids.as_mut_ptr().add(1),
            value.scratch_poses.as_mut_ptr().add(1)) }, OK);
        value
    }
    fn add(&mut self, id:u32, data:&mut [f32;7]) -> i32 {
        unsafe {prr_add(&mut *self.cache,id,data.as_mut_ptr().cast())}
    }
    fn remove(&mut self,id:u32)->i32 {unsafe{prr_remove(&mut *self.cache,id)}}
    fn snap(&mut self)->i32 {unsafe{prr_snapshot(&mut *self.cache,Some(read_pose))}}
    fn guards(&self) {
        for i in [0,self.n+1] {
            assert_eq!(self.entries[i].id, GUARD_ID);
            assert_eq!(self.entries[i].reserved, GUARD_ID);
            assert!(self.entries[i].actor.is_null());
            assert_eq!(self.ids[i], GUARD_ID);
            assert_eq!(self.scratch_ids[i], GUARD_ID);
        }
        for i in [0,self.n*7+1] {
            assert_eq!(self.poses[i],GUARD_POSE);
            assert_eq!(self.scratch_poses[i],GUARD_POSE);
        }
    }
    fn published(&self)->(u32,Vec<u32>,Vec<f32>) {
        (self.cache.published,self.ids.clone(),self.poses.clone())
    }
}
unsafe extern "C" fn read_pose(actor:*mut c_void,out:*mut f32)->i32 {
    unsafe{ptr::copy_nonoverlapping(actor.cast::<f32>(),out,7)};
    OK
}
fn pose(v:f32)->[f32;7] {[v,v+1.0,v+2.0,0.0,0.0,0.0,1.0]}

#[test]
fn every_row_component_nonfinite_is_transactional() {
    let mut c=Owned::new(8);
    let mut actors:Vec<Box<[f32;7]>>=(0..8).map(|i|Box::new(pose(i as f32))).collect();
    for (i,a) in actors.iter_mut().enumerate(){assert_eq!(c.add(i as u32+1,a),OK);}
    assert_eq!(c.snap(),OK);
    let before=c.published();
    for row in 0..8 {
        for component in 0..7 {
            for bad in [f32::NAN,f32::INFINITY,f32::NEG_INFINITY] {
                let old=actors[row][component];actors[row][component]=bad;
                assert_eq!(c.snap(),READ_FAILED,"row={row} component={component}");
                assert_eq!(c.published(),before);c.guards();
                actors[row][component]=old;assert_eq!(c.snap(),OK);
            }
        }
    }
}
#[repr(C)]
struct Partial { values:[f32;7], writes:usize, status:i32 }
unsafe extern "C" fn partial_read(actor:*mut c_void,out:*mut f32)->i32 {
    let source=unsafe{&*actor.cast::<Partial>()};
    for i in 0..source.writes {unsafe{out.add(i).write(source.values[i]);}}
    source.status
}
#[test]
fn every_partial_write_length_rejected_without_publishing() {
    let mut c=Owned::new(1);
    let mut source=Partial{values:pose(1.0),writes:7,status:0};
    assert_eq!(unsafe{prr_add(&mut *c.cache,1,(&mut source as *mut Partial).cast())},OK);
    assert_eq!(unsafe{prr_snapshot(&mut *c.cache,Some(partial_read))},OK);
    let before=c.published();
    for writes in 0..7 {
        source.writes=writes;
        assert_eq!(unsafe{prr_snapshot(&mut *c.cache,Some(partial_read))},READ_FAILED);
        assert_eq!(c.published(),before);c.guards();
    }
}
#[test]
fn callback_status_not_just_minus_five_is_failure() {
    let mut c=Owned::new(1);
    let mut source=Partial{values:pose(1.0),writes:7,status:0};
    assert_eq!(unsafe{prr_add(&mut *c.cache,1,(&mut source as *mut Partial).cast())},OK);
    assert_eq!(unsafe{prr_snapshot(&mut *c.cache,Some(partial_read))},OK);
    let before=c.published();
    for status in [1,-1,2,i32::MIN,i32::MAX] {
        source.status=status;
        assert_eq!(unsafe{prr_snapshot(&mut *c.cache,Some(partial_read))},READ_FAILED);
        assert_eq!(c.published(),before);c.guards();
    }
}
#[test]
fn rejection_does_not_invalidate_existing_publication() {
    let mut c=Owned::new(1);let mut a=pose(1.0);let mut b=pose(2.0);
    assert_eq!(c.add(1,&mut a),OK);assert_eq!(c.snap(),OK);let before=c.published();
    assert_eq!(c.add(1,&mut b),DUPLICATE);assert_eq!(c.published(),before);
    assert_eq!(c.add(2,&mut a),DUPLICATE);assert_eq!(c.published(),before);
    assert_eq!(c.add(2,&mut b),FULL);assert_eq!(c.published(),before);
    assert_eq!(c.remove(0),INVALID);assert_eq!(c.published(),before);
    assert_eq!(c.remove(9),NOT_FOUND);assert_eq!(c.published(),before);c.guards();
}
#[test]
fn every_pair_of_buffer_aliases_rejected_before_context_changes() {
    let mut c=Owned::new(2);let mut a=pose(1.0);c.add(1,&mut a);c.snap();
    let before=c.published();
    // Equal-size backing allocations are larger than any requested buffer.
    // Cast addresses only; invalid aliasing is rejected without dereferencing.
    let mut allocations:Vec<Vec<u64>>=(0..5).map(|_|vec![0u64;32]).collect();
    let starts:Vec<*mut u8>=allocations.iter_mut().map(|v|v.as_mut_ptr().cast()).collect();
    for i in 0..5 {for j in 0..i {
        let mut p=starts.clone();p[i]=p[j];
        assert_eq!(unsafe{prr_init(&mut *c.cache,2,p[0].cast(),p[1].cast(),p[2].cast(),p[3].cast(),p[4].cast())},INVALID);
        assert_eq!(c.published(),before);assert_eq!(c.cache.count,1);
    }}
}
#[test]
fn null_init_inputs_do_not_mutate_context() {
    let mut c=Owned::new(1);let mut a=pose(1.0);c.add(1,&mut a);c.snap();
    let before=c.published();
    let starts=[c.cache.entries.cast::<u8>(),c.cache.ids.cast(),c.cache.poses.cast(),
                c.cache.scratch_ids.cast(),c.cache.scratch_poses.cast()];
    for i in 0..5 {
        let mut p=starts;p[i]=ptr::null_mut();
        assert_eq!(unsafe{prr_init(&mut *c.cache,1,p[0].cast(),p[1].cast(),p[2].cast(),p[3].cast(),p[4].cast())},INVALID);
        assert_eq!(c.published(),before);c.guards();
    }
}
#[test]
fn context_overlapping_ids_rejected() {
    let mut c=Owned::new(1);
    let context=&mut *c.cache as *mut PrCache;
    assert_eq!(unsafe{prr_init(context,1,c.entries.as_mut_ptr().add(1),context.cast(),
        c.poses.as_mut_ptr().add(1),c.scratch_ids.as_mut_ptr().add(1),c.scratch_poses.as_mut_ptr().add(1))},INVALID);
    assert_eq!(c.cache.initialized,INIT);c.guards();
}
#[test]
fn corrupted_counts_rejected_without_reading_entries() {
    let mut c=Owned::new(1);c.cache.count=2;
    assert_eq!(c.snap(),INVALID);c.cache.count=0;c.cache.published=2;
    assert_eq!(c.snap(),INVALID);c.cache.published=0;c.cache.initialized=0;
    assert_eq!(c.snap(),INVALID);c.guards();
}
#[test]
fn capacity_arithmetic_matches_pointer_width() {
    assert_eq!(prr_capacity_valid(0),0);
    for n in [1,8,65536,76_695_844,76_695_845,u32::MAX] {
        let fits=(n as u64)*28<=isize::MAX as u64 &&
            (n as u64)*(mem::size_of::<PrEntry>() as u64)<=isize::MAX as u64;
        assert_eq!(prr_capacity_valid(n),u32::from(fits));
    }
}
#[test]
fn repeated_clear_refill_preserves_guards() {
    let mut c=Owned::new(3);let mut actors=[pose(1.0),pose(2.0),pose(3.0)];
    for _ in 0..256 {
        for (i,a) in actors.iter_mut().enumerate(){assert_eq!(c.add(i as u32+1,a),OK);}
        assert_eq!(c.snap(),OK);c.guards();
        assert_eq!(unsafe{prr_clear(&mut *c.cache)},OK);
        assert_eq!(c.cache.count,0);assert_eq!(c.cache.published,0);c.guards();
    }
}
#[test]
fn moving_owner_keeps_allocations_and_actor_tokens_stable() {
    let mut c=Owned::new(1);let mut a=Box::new(pose(9.0));c.add(u32::MAX,&mut a);
    let mut moved=c;assert_eq!(moved.snap(),OK);
    assert_eq!(moved.ids[1],u32::MAX);assert_eq!(moved.poses[1],9.0);moved.guards();
}
#[test]
fn published_and_scratch_addresses_stay_stable() {
    let mut c=Owned::new(1);let mut a=pose(1.0);c.add(1,&mut a);
    let ids=c.cache.ids;let poses=c.cache.poses;
    for i in 0..64 {a[0]=i as f32;assert_eq!(c.snap(),OK);
        assert_eq!(c.cache.ids,ids);assert_eq!(c.cache.poses,poses);c.guards();}
}
#[test]
fn deterministic_model_based_churn_65536_operations() {
    // Independent ordered model; stable seeds, operations, and integer poses.
    for seed in 1u32..=32 {
        let mut rng=seed;let mut c=Owned::new(8);
        let mut actors:Vec<Box<[f32;7]>>=(0..16).map(|i|Box::new(pose(i as f32))).collect();
        let mut model:Vec<(u32,usize)>=Vec::new();
        for step in 0..2048 {
            rng=rng.wrapping_mul(1664525).wrapping_add(1013904223);
            let slot=((rng>>8)%16) as usize;
            let id=if slot==15{u32::MAX}else if slot==14{16_777_217}else{slot as u32+1};
            match (rng>>24)%4 {
                0=>{
                    let expected=if model.iter().any(|x|x.0==id){DUPLICATE}else if model.len()==8{FULL}else{OK};
                    assert_eq!(c.add(id,&mut actors[slot]),expected,"seed={seed} step={step}");
                    if expected==OK{model.push((id,slot));}
                },
                1=>{
                    if let Some(i)=model.iter().position(|x|x.0==id){assert_eq!(c.remove(id),OK);model.remove(i);}
                    else{assert_eq!(c.remove(id),NOT_FOUND);}
                },
                2=>{actors[slot][0]=step as f32;},
                _=>{},
            }
            assert_eq!(c.snap(),OK,"seed={seed} step={step}");
            assert_eq!(c.cache.count as usize,model.len());assert_eq!(c.cache.published as usize,model.len());
            for (i,(entity,index)) in model.iter().enumerate(){
                assert_eq!(c.ids[i+1],*entity,"seed={seed} step={step}");
                assert_eq!(&c.poses[i*7+1..i*7+8],&actors[*index][..],"seed={seed} step={step}");
            }
            c.guards();
        }
    }
}
