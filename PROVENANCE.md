<!-- SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) -->
<!-- SPDX-License-Identifier: MIT -->

# Source provenance and release scope

This package is the PhysX PE browser integration maintained by Jake Wehmeier
(BTSpaniel). It is an attributed port of NVIDIA PhysX, Blast, and Flow using
fabmax-derived bindings, with custom Rust batching, WebGPU memory bridges,
validation, ownership, build, and verification code. It is not presented as a
clean-room reimplementation of the upstream SDKs or binding code.

`upstream.lock.json` pins the upstream repositories, commits, SDK versions,
Emscripten, Rust, and Slang. `source-selection.json` identifies the current
released alpha.4 full source selection, its displaced legacy inputs, and the frozen
component references described in `SOURCE_BUILD_KIT.md`. The historical sixteen
inputs remain in `reference/alpha2-production/source-selection.json`. The
installed-reference loader/WASM hashes identify that older observed build;
no binary identity or current-test pass is claimed for a fresh source rebuild.

The historical public runtime
[5.11.0-alpha.3](https://github.com/BTSpaniel/Physx/releases/tag/v5.11.0-alpha.3)
was published on 2026-10-01 from source commit
[`53c0cd99d0695a23b89047bde0f000fdecaaa363`](https://github.com/BTSpaniel/Physx/commit/53c0cd99d0695a23b89047bde0f000fdecaaa363).
Those historical assets and their receipts remain attached to that source and
binary pair. The [alpha.4 prerelease](https://github.com/BTSpaniel/Physx/releases/tag/v5.11.0-alpha.4)
was published on 2026-10-03 from
[`5dea7b3726ef5cd58115d4832085c96bb03eea26`](https://github.com/BTSpaniel/Physx/commit/5dea7b3726ef5cd58115d4832085c96bb03eea26)
by [original CI run 37101094361](https://github.com/BTSpaniel/Physx/actions/runs/37101094361).
Its own ten SDK phases, 37-case browser evidence, package receipt and anonymous
seven-asset checks bind the public CI pair. A later documentation merge is not
the compiled tag identity, and SDK publication does not admit every Engine path.
The separately published alpha.3 portable source bundle retains its original
53c0cd9 source identity and licensing; it is not an alpha.4 source kit.

The selected thermal MIT distribution retains its historical Alpha source and
license provenance under `reference/`. The current base thermal implementation
is checked against that licensed origin plus the recorded finite char-rate
derivation: `if (!isFinite(charRate)) return NONFINITE;` before the positive-rate
timestep test. This restores the existing nonfinite status `6` without changing
finite equations or their evaluation order. The source gate reconstructs the
approved derivation; it does not authorize arbitrary changes to the origin.
ABI1 retains the explicit method, while ABI2/numerical version 9 remains a
separate implicit transport interface.

The accurate physical-section processor avoids unused inherited base-processor
preparation while retaining revision 3's own geometry, physical scaling,
factor construction and solve path. Legacy processors remain separate, and
material laws, physical tolerance and iteration budgets are unchanged. This
source optimization carries no performance admission for a fresh runtime pair.

`rust/src/regression_tests.rs` is an original build-lab MIT test module restored
verbatim because `rust/src/lib.rs` already includes it under `#[cfg(test)]`.
Its SHA-256 is
`554af72bcaf1fec9860007f217d2844e3cf2245666ead9e14ff4f7eec56d5386`.
The original header and all thirteen regression tests are preserved. This
transitive test source adds no runtime algorithms or dependencies. Its retained
test-only provenance is separate from the runtime changes selected for the
current full source kit.

`patches/browser-overlay.patch` contains the retained SDK/browser source
adaptations. Its SHA-256 is
`fc1675b9b0982997f2e4cb337bde36f093b84edd3995fc0bab3c61538ac4cee0`.
The earlier workbench patch hash is
`a1d433af5cf689d194de4c2f31235f29312b847c9e8072212d5c613ea2430e54`.
Portable preparation removes generated local addon build blocks and normalizes
build paths so the Python builder can regenerate them in its own workspace.
`upstream-modifications.json` records every retained patch entry, its path,
entry hash, and inherited notice scope. The patch includes JNI platform and
generator references even though the release build targets WebAssembly.

The newly written stream adapter is retained in
`source-overlays/PrWasmStreams.h`. Blast preparation verifies three exact
NVIDIA source hashes before generating scalar platform/vector adaptations.
Those outputs retain original source notices, and their adaptation receipts
record both input and generated hashes. Flow shader compilation similarly
records the exact shader inputs, Slang executable, transformations, WGSL, and
reflection. The generated shader corpus retains NVIDIA's BSD-3-Clause header.

Flow also retains its PNanoVDB host dependency. `nanovdb-provenance.json` records
an exact implementation-body match between the pinned NVIDIA header and the
historical OpenVDB `v12.0.0` header, including both source hashes and the
normalization method. That historical release explicitly uses Apache-2.0;
its copyright notice, license, relicensing record, and the exact NVIDIA header
accompany this package. This evidence is about the pinned dependency's origin
and terms, not a claim that all NanoVDB-dependent code is excluded.

`license-provenance.json` records the complete retained license texts, their
SHA-256 digests, pinned source URLs, and whether a text was copied verbatim or
extracted from an original source header. `source-license-map.json` maps the
selected custom bridge, stream adapter, type declarations, and browser patch to
their contribution and inherited-license scopes. These files use public source
IDs and relative package paths; private build-machine paths are excluded.

The physical section solvers, solid Flow pressure-boundary extensions, scalar
source admissions, wood thermal coupling, and convex boundary query are included
in the released alpha.4 source selection. Source selection does not establish
functional, Engine integration, real-time, or release admission. Historical
test results do not certify a new artifact, and rendered frame rate does not
establish real-time physics. Fresh build and verification receipts must bind
the current committed source revision and exact loader/WASM/shader pair before
it is offered as a verified runtime. Alpha.4's original bounded SDK and package
results are documented above and in the release; they do not establish general
Engine, hardware real-time, physical-realism or device-matrix admission.
Historical alpha.2 and alpha.3 artifacts remain unchanged.

Only this bounded standalone package is licensed here. Particle Realms Engine,
Editor, WebGPU OS, and other project code outside this package keep their
existing licenses. Authorship credits identify original and custom contributions
without changing upstream copyrights or claiming upstream endorsement.

The current declaration generator also inventories actual first-party native
Rust, Blast and Flow exports. It preserves inherited WebIDL interface names
while expressing the observed prototype methods and 64-bit `bigint` calls.
Generated `.d.ts` and `.d.mts` entries share the same content; JavaScript addon
adapters have adjacent generated module declarations. Declaration generation
does not itself alter native algorithms. The current source kit selects the
additional runtime sources explicitly; historical alpha.2 and published alpha.3
binaries are unchanged.
The accompanying WebGPU ambient declarations are copied verbatim from the
commit pinned in `upstream.lock.json`, retain their own BSD-3-Clause notice,
and are identified separately in the source and license maps.
