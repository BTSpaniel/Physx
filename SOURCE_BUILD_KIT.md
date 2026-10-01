<!-- SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) -->
<!-- SPDX-License-Identifier: MIT -->

# PhysX PE alpha.3 source build kit

Prepared **2026-10-01** for **5.11.0-alpha.3**. The preparation status is **UNPUBLISHED / PENDING fresh build, verification, package and Engine admission**. This isolated kit contains the reviewed PhysX 5.11, Blast, Flow and experimental wood-thermal source selection with 96 source-derived addon declarations. `source-selection.json` is marked **FINAL_SOURCES_SELECTED_FOR_BUILD** with no pending native inputs. That status permits a source build; it does not certify execution. This kit does not replace the production source tree, the published alpha.2 runtime, or the Engine runtime.

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

The four selected wood-thermal files use the owner's authorized MIT distribution. Their Alpha originals and original license text remain under reference provenance. The source gate checks that replacing only each SPDX license identifier reconstructs every selected byte; algorithms, line endings and copyrights remain unchanged. No Alpha orchestration helper executes in this kit. The MRI9 translation unit includes the unchanged base implementation and freshly compiles all seven actual exports. Only ABI2/numerical9 is the proposed thermal capability; retained ABI1 exports do not certify a different thermal solver.

The CMake recipe links fresh PhysX/WebIDL, Rust, the convex bulk adapter, Blast, Flow and MRI9 into one shared WASM heap with explicit `-O3 -fno-fast-math -fno-associative-math -ffp-contract=off -msimd128` final flags. The default physical tolerance stays 1e-6. Material laws, dt, damage controls and trial budgets remain unchanged. The existing `release.py build` still requires an exact clean committed source tree and all pinned upstream/overlay inputs. After review and source commitment, native Windows Python can drive it under the real host lock:

```text
python tools/source_kit_windows.py full-build --lock-workspace C:/Coding/game --emsdk C:/path/to/existing/emsdk --slangc C:/path/to/existing/slangc --rust-toolchain C:/path/to/existing/rustup/toolchains/1.90.0-x86_64-unknown-linux-gnu --report reports/full-source-build-01.json
```

The alpha.3 candidate requires a fresh invocation at its final committed source revision. An earlier isolated source revision compiled successfully; its receipts do not admit this candidate. Fresh compiled bytes need their own actual same-pair native, browser, GPU, extracted-package and Engine verification before any release or installation. Runtime package code includes both imported Flow boundary/scalar helpers and checks all 124 WGSL modules; this does not claim every pipeline or platform is supported.

The thermal solver is experimental and can be expensive: the historical captured 0.1-second case required roughly 1.6 seconds of CPU-WASM kernel work. No real-time thermal claim follows. Momentum exchange currently covers terminal normal exchange only; heat remains an explicit unapplied obligation. The historical component receipts do not admit newly compiled bytes.

The inherited IDL declarations and notices stay byte-identical. The complete raw declaration inventory matches `reference/combined-native-06/expected-addon-exports.json` exactly. Bounded recognition of the actual `PR_EXPORT` macro scans the two thermal definition files once. The TypeScript consumer checks all 25 additional non-Flow APIs alongside the existing 71; wrong pointer representations, arity and return types must fail. Numeric wasm32 addresses do not encode alignment, allocation, lifetime or ownership. Thermal arenas/workspaces are caller owned, aligned and disjoint; convex queries borrow a live shape after completed scene simulation. These declarations do not imply that published alpha.2 implements the selected alpha.3 APIs. Delivered-archive compatibility tests retain alpha.2 as their historical default; a new delivered ZIP must explicitly pass `--expected-version 5.11.0-alpha.3` with that release's actual anonymous-download proof. A local ZIP cannot supply anonymous-publication evidence.
