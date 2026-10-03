<!-- SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) -->
<!-- SPDX-License-Identifier: MIT -->

# PhysX PE alpha.4 source build kit

This source checkout prepares **5.11.0-alpha.4**. It is **unpublished and pending
a complete fresh build, verification, packaging and Engine admission**. The
reviewed selection contains PhysX 5.11.0, Blast 5.0.6, Flow and experimental
CPU-WASM wood-thermal sources with 96 source-derived addon declarations.
`FINAL_SOURCES_SELECTED_FOR_BUILD` permits building the selected sources; it is
not a compilation or execution result.

The historical public runtime is
[5.11.0-alpha.3](https://github.com/BTSpaniel/Physx/releases/tag/v5.11.0-alpha.3),
published 2026-10-01 from source
[`53c0cd99d0695a23b89047bde0f000fdecaaa363`](https://github.com/BTSpaniel/Physx/commit/53c0cd99d0695a23b89047bde0f000fdecaaa363).
Its downloads and receipts remain attached to that source and binary pair.
This kit does not replace those assets, an installed private development pair
or an Engine runtime. Public download verification and local source verification
are different evidence.

## Selected changes and numerical scope

The base explicit thermal implementation adds one finite-intermediate check:
`if (!isFinite(charRate)) return NONFINITE;` before the positive char-rate
timestep bound. The existing nonfinite status is `6`. This prevents an invalid
char reaction rate from being ignored by a `> 0` test; every finite equation and
its order remain unchanged. The source gate reconstructs the exact licensed
origin plus this recorded derivation rather than accepting arbitrary edits.
Caller-owned canonical state must be committed only after native success and
full output, readonly-input and ledger validation.

The accurate physical-section processor retains revision 3's own binary64
geometry, physical weights, section factors and solve path while avoiding
unused inherited base-processor preparation. Legacy processors remain separate.
The original physical tolerance is **1e-6**; the optimization does not change
material laws, damage controls, timestep or iteration budgets. Fresh same-pair
physical checks are still required; selected source is not a speed claim.

The multirate translation unit includes the base implementation and exports
both interfaces:

| Interface | Numerical method and ownership |
| --- | --- |
| ABI1 (`pr_wood_thermal_step`) | Existing explicit adaptive method and caller-owned arena. The selected stable-depletion flag uses `log1p`/`expm1` for frozen-temperature loss; compatibility uses declared f64 tolerances, not assumed bit-exact JavaScript arithmetic. |
| ABI2, numerical version 9 | Adaptive implicit SDIRK transport with separate controls, caller-owned workspace, error budgets and diagnostics. It does not call ABI1 or automatically select an Engine integrator. |

Keeping the ABI1 exports does not establish ABI1 compatibility for a new pair.
ABI2 is a different numerical method, not a transparent replacement for the
explicit solver. Both run on the CPU in WebAssembly. PhysX and Blast likewise
run on CPU-WASM; Flow's sparse compute uses WebGPU through its WASM host graph.

## Source selection, licenses and generated outputs

`source-selection.json` records the legacy sixteen-source selection, displaced
inputs, current identities and recorded derivations. `reference/` preserves
earlier component, SDK and production provenance. Frozen reference generated
source, WGSL and build manifests are identity data; historical objects and
runtime pairs are not substitutes for a fresh build. Only the kit's top-level
`work/`, `dist/` and `reports/` are generated output directories.

The kit retains 265 upstream Flow source/header/shader/project inputs and
selected first-party helpers. NVIDIA's per-file notices remain intact. Original
PhysX PE contributions use the root MIT license within its stated scope;
NVIDIA, OpenVDB, fabmax and other dependencies retain their own licenses and
attribution. The authorized thermal MIT distribution retains its original
Alpha source and license provenance under `reference/`; selected changes are
checked as explicit derivations. No Alpha orchestration helper executes here.
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [AUTHORS.md](AUTHORS.md),
[PROVENANCE.md](PROVENANCE.md) and `LICENSES/`.

The Flow build compiles four corpora: **97 original**, **21 solid-boundary**,
**four scalar-source** and **two momentum** kernels. It requires 124 generated
host headers, six rebase include wrappers and 252 shader/reflection/manifest
files. The original 195-file corpus remains separately accounted for. Every
native Flow translation unit is recompiled; cached component objects are not
admitted as a source build.

Blast's retained 203-input component manifest contains 178 pinned raw NVIDIA
files. Regeneration additionally binds three required raw transform inputs and
the real version file to pinned upstream Git blobs, giving a 182-file raw source
inventory. The historical manifest remains intact. The native recipe verifies
the selected derived sources, compiles all **27 Blast translation units**, and
performs a fresh relocatable link without a historical object cache.

The CMake recipe links fresh PhysX/WebIDL, Rust, convex bulk, Blast, Flow and
thermal code into one shared WASM heap with explicit final flags:
`-O3 -fno-fast-math -fno-associative-math -ffp-contract=off -msimd128`.
The inherited IDL declarations and upstream notices remain intact. Source
signature and generated declaration checks must bind the selected source hashes
even when a source-only correction leaves function signatures unchanged.

## Build with an existing pinned toolchain

Use Python 3.12 and the versions pinned by [upstream.lock.json](upstream.lock.json):
Emscripten 4.0.19, Rust 1.90.0 and Slang 2025.6.1. The ordinary build requires a
clean committed source tree, final source selection, contained inputs and exact
upstream/overlay identities. Clearing a pending list cannot bypass those checks.

For Windows/WSL, the Windows parent holds the host's shared execution lock
throughout each WSL command. A WSL `fcntl` lock does not establish ownership of
the Windows `msvcrt` lock. `tools/source_kit_windows.py` reuses the existing host
helper and records source, generated-type, compiler and reference identities
before and after execution. Native command arguments, compiler versions and
output are retained; WSL processes perform their additional source checks.

Run from the selected checkout with real existing toolchain paths and a fresh,
contained report name for every invocation:

```text
python tools/source_kit_windows.py typecheck --lock-workspace C:/Coding/game --report reports/semantic-types-01.json
python tools/source_kit_windows.py flow-build --lock-workspace C:/Coding/game --emsdk C:/path/to/existing/emsdk --slangc C:/path/to/existing/slangc --report reports/fresh-flow-build-01.json
python tools/source_kit_windows.py full-build --lock-workspace C:/Coding/game --emsdk C:/path/to/existing/emsdk --slangc C:/path/to/existing/slangc --rust-toolchain C:/path/to/existing/rustup/toolchains/1.90.0-x86_64-unknown-linux-gnu --report reports/full-source-build-01.json
```

These commands do not install toolchains or change power settings. New outputs
may differ from historical components; preserve their receipts and compare their
identities rather than replacing an admitted runtime automatically.

On an actual isolated GitHub-hosted Actions runner, the existing Flow component
route is:

```text
python release.py build-flow-component --ci-isolated --slangc /path/to/existing/slangc
```

`--ci-isolated` validates the real hosted CI environment and cannot bypass a
local execution window. This phase retains fresh host artifacts, objects and
receipts under unique component output directories. Compilation alone establishes
neither GPU behavior nor a final unified runtime.

## Verification and remaining limits

Fresh compiled bytes need their own native, browser, GPU, extracted-package and
Engine integration results. A successful final link alone does not make the
full producer pass: generated declaration/source-identity checks and after-guards
must also pass. Failed or partial receipts cannot become completed-build proof.
Static ABI inspection, runtime smoke tests and Engine admission remain distinct.

The current declaration inventory includes 96 source-derived native exports.
The browser TypeScript consumer checks 25 additional non-Flow APIs alongside
the existing 71; incorrect pointers, arity and return types must fail. Numeric
wasm32 addresses do not encode alignment, allocation size, lifetime or ownership.
Thermal arenas and workspaces are caller-owned, aligned and disjoint; convex
queries borrow a live shape after completed scene simulation. These declarations
do not certify a new runtime pair or imply that historical downloads implement
every currently selected source API.

A separate local standalone guarded ABI1 probe passed the original eight-case
compatibility suite, including status `6`, canonical-state protection and native
allocation cleanup. One captured 0.1-second input required 1,095 explicit adaptive
substeps and approximately 269 ms median total native cost versus 593 ms for
JavaScript on that Chrome/Windows fixture. Preparation, heap transfers and commit
were included. This is not a unified alpha.4 or complete-scene result. Refined
internal peaks near 9,260–10,060 K remain an explicit-integration concern;
matching JavaScript state and conservation does not admit physical realism or
transient peak convergence. ABI2 cannot silently replace ABI1 for performance.

Momentum exchange covers terminal normal exchange only; heat remains an explicit
unapplied obligation. Historical thermal, section or Flow receipts do not admit
new bytes. All 124 WGSL modules require their retained checks, without implying
that every pipeline or device is supported. No realtime thermal, unlimited leak
absence, cross-build replay, complete upstream SDK or device-matrix claim follows.

Delivered-archive checks must receive the actual intended release version and
that version's anonymous public-download proof. For a future published alpha.4
ZIP this means `--expected-version 5.11.0-alpha.4`; it is not a command to assert
that such a download already exists. A local ZIP cannot supply publication
evidence. The historical published alpha.3 assets and their receipts stay intact.
