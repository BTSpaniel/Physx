<!-- SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) -->
<!-- SPDX-License-Identifier: MIT -->

# Source provenance and release scope

This package is the PhysX PE browser integration maintained by Jake Wehmeier
(BTSpaniel). It is an attributed port of NVIDIA PhysX, Blast, and Flow using
fabmax-derived bindings, with custom Rust batching, WebGPU memory bridges,
validation, ownership, build, and verification code. It is not presented as a
clean-room reimplementation of the upstream SDKs or binding code.

`upstream.lock.json` pins the upstream repositories, commits, SDK versions,
Emscripten, Rust, and Slang. `source-selection.json` identifies sixteen selected
bridge inputs by SHA-256 and lists excluded candidate features. These hashes
match the source inputs recorded for the installed reference runtime. The
reference loader/WASM hashes identify that older observed build; no binary
identity or current-test pass is claimed for a fresh portable rebuild.

`rust/src/regression_tests.rs` is an original build-lab MIT test module restored
verbatim because `rust/src/lib.rs` already includes it under `#[cfg(test)]`.
Its SHA-256 is
`554af72bcaf1fec9860007f217d2844e3cf2245666ead9e14ff4f7eec56d5386`.
The original header and all thirteen regression tests are preserved. This
transitive test source adds no runtime algorithms or dependencies and does not
change the sixteen stable runtime input hashes in `source-selection.json`.

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
source admissions, and wood thermal coupling are pending candidates and are
excluded from this source selection. Their presence in a development workspace
does not establish release support. Likewise, rendered frame rate does not
establish real-time physics, and historical test results do not certify a new
artifact. Fresh build and browser receipts must identify the current exact
loader/WASM/shader pair before it is offered as a verified runtime.

Only this bounded standalone package is licensed here. Particle Realms Engine,
Editor, WebGPU OS, and other project code outside this package keep their
existing licenses. Authorship credits identify original and custom contributions
without changing upstream copyrights or claiming upstream endorsement.

The current declaration generator also inventories actual first-party native
Rust, Blast and Flow exports. It preserves inherited WebIDL interface names
while expressing the observed prototype methods and 64-bit `bigint` calls.
Generated `.d.ts` and `.d.mts` entries share the same content; JavaScript addon
adapters have adjacent generated module declarations. These source changes do
not change the selected sixteen native inputs or published alpha.2 binaries.
The accompanying WebGPU ambient declarations are copied verbatim from the
commit pinned in `upstream.lock.json`, retain their own BSD-3-Clause notice,
and are identified separately in the source and license maps.
