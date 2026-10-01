<!-- SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) -->
<!-- SPDX-License-Identifier: MIT -->

# PhysX PE alpha.3 source build kit

The exact **5.11.0-alpha.3** runtime source is commit **53c0cd99d0695a23b89047bde0f000fdecaaa363**, tagged **v5.11.0-alpha.3** on **2026-10-01**. The tagged build passed all ten standalone SDK verification phases and the extracted-runtime browser checks before publication. Its seven public assets have been anonymously downloaded and checked against that actual build. This source contains the reviewed PhysX 5.11, Blast, Flow and experimental wood-thermal selection with 96 source-derived addon declarations. `source-selection.json` is marked **FINAL_SOURCES_SELECTED_FOR_BUILD** with no pending native inputs. Source selection alone is not an execution result. Engine installation and performance qualification remain separate; this SDK release does not replace the Engine runtime automatically.

Use a genuine clone of `v5.11.0-alpha.3` for rebuilding. GitHub's automatic source archives omit Git history; unzipping one alone cannot satisfy the existing clean committed-source guard. Keep any outer portable-kit documentation or checksum outside the cloned repository so its source inventory remains the exact 1,608 committed files.

The [published portable source-build kit](https://github.com/BTSpaniel/Physx/releases/download/source-kit-v5.11.0-alpha.3/physx-pe-5.11.0-alpha.3-source-build-kit.zip) uses a genuine Git bundle,
not synthetic repository metadata. Its actual bundle clone reproduced the exact
1,608-file inventory and passed the unchanged committed-source, final-selection
and stable-selection guards. Extract the outer kit, then run:

```text
git clone --branch v5.11.0-alpha.3 physx-pe-source.bundle Physx-PE
cd Physx-PE
git rev-parse HEAD
```

The result must be `53c0cd99d0695a23b89047bde0f000fdecaaa363`.
The bundle is a source-delivery option; it contains no compiled runtime and
was published separately from the seven runtime assets. Both source-kit assets
passed anonymous byte-for-byte download checks, followed by another real bundle
clone and the unchanged committed-source and selection guards. Bootstrapping
still requires the pinned upstream/toolchain downloads and the original local
or genuine hosted execution window. No compiler or browser was run merely to
verify that source-kit reconstruction.

The kit contains the exact 265 upstream Flow source/header/shader/project inputs and the admitted first-party component helpers. NVIDIA's original per-file notices remain intact. The root MIT license covers the custom contributions within its stated scope; NVIDIA, OpenVDB, fabmax and other dependencies retain their own licenses and attribution. This is a source port with provenance, not a clean-room rewrite.

`source-selection.json` records the legacy sixteen-source selection and every displaced selected input. `reference/` preserves the original component, SDK and production selection records. No native object, loader or WASM pair was copied into this kit. Reference generated source/WGSL and native build manifests are identity data only. Frozen references belong to the committed source inventory; only this kit's top-level work, dist and reports are generated outputs.

The Flow build compiles all four corpora: 97 original kernels, 21 solid-boundary kernels, four scalar-source kernels and two momentum kernels. It requires exactly 124 generated host headers, six rebase include wrappers and 252 shader/reflection/manifest files. The original 195-file corpus remains independently accounted for. Every native Flow translation unit is recompiled; a cached component object is not an admitted source build.

From an activated, pinned toolchain on a verified isolated GitHub-hosted Actions runner:

```text
python release.py build-flow-component --ci-isolated --slangc /path/to/existing/slangc
```

The command uses the existing execution-window validation. It requires the real Actions/CI/hosted-runner environment. It compiles from the local pinned sources, creates a fresh `reports/flow-source-build-*.json`, and preserves that phase's host pair and partial object under a unique `dist/flow-component-*` directory. Compilation alone does not establish GPU behavior or a final unified runtime.

An ordinary local build must use the existing host's shared execution window. On Windows, the Windows parent must hold the native shared lock across any WSL command. A direct WSL `fcntl` lock is explicitly rejected as evidence of the Windows `msvcrt` lock. No toolchain installers or environment power overrides are included.

The local `tools/source_kit_windows.py` route imports that existing host helper and keeps the lock while Windows records the source, type and compiler inputs before and after execution. Its `typecheck` phase runs the original browser TypeScript consumer. Its `flow-build` phase runs the four selected compiler recipes and native component build in WSL; those processes perform additional source checks in WSL. Compiler versions, bytes, actual argv and output are retained. This distinction is explicit in the receipt.

```text
python tools/source_kit_windows.py typecheck --lock-workspace C:/Coding/game --report reports/semantic-types-01.json
python tools/source_kit_windows.py flow-build --lock-workspace C:/Coding/game --emsdk C:/path/to/existing/emsdk --slangc C:/path/to/existing/slangc --report reports/fresh-flow-build-01.json
```

Every invocation needs a fresh contained report name. Actual build outputs may differ from the older component; they are retained for comparison and cannot replace the separately admitted runtime automatically.

The final unified build, verify and package commands fail closed while native inputs are pending. Clearing or deleting the pending list cannot unlock them. They also require the explicit final-selection status, the pinned upstream identity, and exact contained Flow, Blast and wood-thermal manifest/source identities.

The selected native sources come from immutable combined06. Its convex bridge restores the reviewed live-shape plane query. Blast's 203-input component contains 178 pinned raw NVIDIA files and the f64 physical preparation, section revision 3 and stress-mass adapters. Actual regeneration checks exposed three raw transform inputs absent from that compiled-input manifest. This kit adds those three originals and the real version file, checking all four bytes against the exact pinned upstream Git blobs; its raw source inventory is 182 files. The original 203-input manifest stays unchanged. `tools/native_components.py` regenerates the exact four derived sources/headers, checks their hashes against the original component, unconditionally compiles all 27 translation units and performs a fresh relocatable link. It never reads a historical object cache.

The four selected wood-thermal files use the owner's authorized MIT distribution. Their Alpha originals and original license text remain under reference provenance. The source gate checks that replacing only each SPDX license identifier reconstructs every selected byte; algorithms, line endings and copyrights remain unchanged. No Alpha orchestration helper executes in this kit. The MRI9 translation unit includes the unchanged base implementation and freshly compiles all seven actual exports. The selected thermal capability is ABI2/numerical9; retained ABI1 exports do not certify a different thermal solver.

The CMake recipe links fresh PhysX/WebIDL, Rust, the convex bulk adapter, Blast, Flow and MRI9 into one shared WASM heap with explicit `-O3 -fno-fast-math -fno-associative-math -ffp-contract=off -msimd128` final flags. The default physical tolerance stays 1e-6. Material laws, dt, damage controls and trial budgets remain unchanged. The existing `release.py build` still requires an exact clean committed source tree and all pinned upstream/overlay inputs. Native Windows Python can drive a clean committed source build under the real host lock:

```text
python tools/source_kit_windows.py full-build --lock-workspace C:/Coding/game --emsdk C:/path/to/existing/emsdk --slangc C:/path/to/existing/slangc --rust-toolchain C:/path/to/existing/rustup/toolchains/1.90.0-x86_64-unknown-linux-gnu --report reports/full-source-build-01.json
```

The published alpha.3 runtime was freshly compiled and verified at the exact tagged source revision in [CI run 36867355569](https://github.com/BTSpaniel/Physx/actions/runs/36867355569). Its receipts bind that run's compiled loader/WASM pair. A rebuild requires a fresh invocation at its own clean committed source revision; historical receipts cannot admit different bytes. Standalone SDK publication requires the original ten SDK phases and the actual extracted-package checks. Engine installation additionally requires the Engine's original numerical, impact, completed-clock, live-view and platform gates. These requirements are separate, and none are weakened by SDK publication. Runtime package code includes both imported Flow boundary/scalar helpers and checks all 124 WGSL modules; this does not claim every pipeline or platform is supported.

The thermal solver is experimental and can be expensive: the historical captured 0.1-second case required roughly 1.6 seconds of CPU-WASM kernel work. No real-time thermal claim follows. Momentum exchange currently covers terminal normal exchange only; heat remains an explicit unapplied obligation. The historical component receipts do not admit newly compiled bytes.

The inherited IDL declarations and notices stay byte-identical. The complete raw declaration inventory matches `reference/combined-native-06/expected-addon-exports.json` exactly. Bounded recognition of the actual `PR_EXPORT` macro scans the two thermal definition files once. The TypeScript consumer checks all 25 additional non-Flow APIs alongside the existing 71; wrong pointer representations, arity and return types must fail. Numeric wasm32 addresses do not encode alignment, allocation, lifetime or ownership. Thermal arenas/workspaces are caller owned, aligned and disjoint; convex queries borrow a live shape after completed scene simulation. These declarations do not imply that published alpha.2 implements the selected alpha.3 APIs. Delivered-archive compatibility tests retain alpha.2 as their historical default; a new delivered ZIP must explicitly pass `--expected-version 5.11.0-alpha.3` with that release's actual anonymous-download proof. A local ZIP cannot supply anonymous-publication evidence.

## Package and source map

| Path | Purpose |
| --- | --- |
| `dist/candidate/` | Matched loader, WASM, declarations and build manifest inside the runtime ZIP. |
| `dist/flow-wgsl/` | Generated Flow shaders, reflection files and manifest inside the runtime ZIP. |
| `addons/flow/` | Flow host adapter, shared-heap WebGPU bridge and source build tools. |
| `addons/blast/` | Blast bridge and source build tools. |
| `bridge/` · `rust/` | Native transfer interfaces, JavaScript batching adapters and Rust implementation. |
| `web/` | Browser example, validation page and simulation suite. |
| `types/` | Generated PhysX/addon declarations and pinned WebGPU types in the source repository. |
| `reports/` | Verification receipts in the runtime ZIP; generated test output in a source checkout. |
| `LICENSES/` | Complete upstream license texts and notices. |

The runtime ZIP includes the browser adapters, not every source build tool.
Clone the exact release tag to change or rebuild the native integration. Temporary
build checkouts stay in `work/`; publishable files are collected in `dist/release/`.

## Historical and published verification scope

The published alpha.2 archive carries `runtime-manifest.json`,
`reports/verification.json` and individual phase receipts. These bind the tested
files to their source revision and record what ran. Release gates cover Rust
units and Python FFI, C++/Rust ABI checks, 23 PhysX browser scenarios, a real
Blast fracture, shared-heap WebGPU transfers, Flow shader validation and
executed kernels, the native Flow graph, and the extracted ZIP.

CI requests SwiftShader for Chromium checks and Mesa lavapipe through Firefox
for the full Flow graph. Firefox can redact adapter identity; reports keep an
unknown observed identity separate from the requested driver. This is functional
and numerical evidence, not a hardware frame-rate benchmark, full SDK
certification or a browser/device compatibility matrix.

The current source checkout defines 37 functional CPU-WASM checks, including
behavioral tests for D6 drives, articulation drives,
controller floor/wall collision, convex and triangle mesh cooking, binary
serialization with 64-bit object IDs, and deterministic replay on the same
build. Vehicle2 checks measure tire support, acceleration, braking and steering;
callback checks measure native contact impulses and trigger entry/exit. It also
checks TypeScript consumers of the actual Rust, Blast and Flow
addon APIs, including invalid argument rejection. Adjacent `.d.mts` files
resolve browser ES module imports; `types/webgpu.d.ts` supplies the pinned GPU
interfaces. Type validation runs the official TypeScript compiler inside a
browser, without adding Node or npm to the application build. These source
checks do not alter the published alpha.2 files or their historical receipts.
Historical bounded tests exercise 288 scene lifecycles, 4,608 dynamic actors,
576 bulk contexts and 288 native Blast checks. Two ten-minute physical-time
replays must agree byte for byte. Those separately recorded CPU tests passed in the installed Chrome
and Edge browsers on one Windows device; those historical results do not establish alpha.3 admission. Scene and addon counts return to zero,
allocation probes reuse their storage, and WASM memory reaches a stable
64 MiB high-water mark. This verifies that bounded fixture; it does not prove
an unlimited-duration allocator leak absence or cross-device replay.

The historical alpha.2 verification excludes the NanoVDB emitter pipeline,
experimental physical-section solvers, solid pressure-boundary extensions and
wood thermal coupling. Its Flow obstacle coupling is one-way velocity coupling.

The published alpha.3 runtime was built from the exact 1,608-file source
inventory at commit `53c0cd99d0695a23b89047bde0f000fdecaaa363`.
Its [tagged CI run](https://github.com/BTSpaniel/Physx/actions/runs/36867355569)
passed all ten standalone SDK phases, including native PhysX/Blast and shared-heap
checks, all 124 WGSL modules, representative numerical compute, the real Flow
host graph and resource cleanup. The packaged archive separately passed all
37 browser cases and its falling-body example after extraction.

That run used an Ubuntu build host and requested Mesa lavapipe through Firefox.
The browser did not report its backend identity. These are functional and
archive-delivery checks, not hardware real-time measurements. The additional
Engine numerical, impact, live-view and installation gates remain independent.
Every new native build still needs its own source-bound, same-pair verification;
another host cannot borrow this release's runtime admission.
Full upstream SDK coverage, unlimited-duration leak absence, cross-build replay,
a browser/device matrix and real-time thermal behavior are outside the claim.
See [source selection](source-selection.json), [source build guide](SOURCE_BUILD_KIT.md)
and [provenance](PROVENANCE.md) for the precise boundary.
