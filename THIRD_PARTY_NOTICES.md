<!-- SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) -->
<!-- SPDX-License-Identifier: MIT -->

# Third-party notices

PhysX PE is an attributed browser port and integration of upstream software.
The MIT license in the package root applies to its custom code and original
modifications. Upstream code keeps the licenses and copyright notices listed
below, including notices within individual source files. Generated WGSL is a
translation of NVIDIA shader source and keeps its source attribution.

## Custom contribution license scope

Scope: this license covers project-authored integration, build tools, tests,
documentation, and original modifications in this package. It does not replace
the licenses or copyrights of NVIDIA PhysX, NVIDIA Blast, NVIDIA Flow, fabmax
bindings, generated upstream shaders, or compiler runtime libraries. The
retained browser patch includes adapted upstream source. Its upstream portions
keep their original terms. See THIRD_PARTY_NOTICES.md, PROVENANCE.md, and
LICENSES/ for the complete retained texts and contribution scopes.

## NVIDIA PhysX 5.11.0

Source: [NVIDIA-Omniverse/PhysX](https://github.com/NVIDIA-Omniverse/PhysX/tree/da950a3537927784951853c66618036f332ca0ce),
tag `ovphysx-0.6.3`, commit `da950a3537927784951853c66618036f332ca0ce`.
The SDK's `physx/LICENSE.md` is Apache-2.0, retained verbatim in
[PhysX-SDK-Apache-2.0.txt](LICENSES/PhysX-SDK-Apache-2.0.txt).
The repository root's NVIDIA BSD-3-Clause text is also retained in
[NVIDIA-Repository-BSD-3-Clause.txt](LICENSES/NVIDIA-Repository-BSD-3-Clause.txt).
Individual historical SDK and Vehicle2 example headers retain their NVIDIA,
AGEIA, and NovodeX notices. The full inherited Vehicle2 header notice is in
[NVIDIA-Legacy-Vehicle-NOTICE.txt](LICENSES/NVIDIA-Legacy-Vehicle-NOTICE.txt).

The retained browser overlay modifies SDK and binding files. Preparation records
the modified paths and preserves their original notices. Changing the module
name to `physx-pe` does not change their authorship or license.

## NVIDIA Blast 5.0.6 and geometry references

The selected low-level, stress, and authoring sources are from the same pinned
NVIDIA commit. The full BSD-style three-clause notice is in
[NVIDIA-Blast-BSD-3-Clause.txt](LICENSES/NVIDIA-Blast-BSD-3-Clause.txt).
The scalar platform and authoring adaptations preserve NVIDIA's algorithms and
headers; their generators record exact source and output hashes.

V-HACD reference and included geometry headers retain Khaled Mamou's copyright
and three-clause terms in [VHACD-BSD-3-Clause.txt](LICENSES/VHACD-BSD-3-Clause.txt).
Bullet-derived geometry headers retain their source zlib notices, including the
restriction against misrepresenting original authorship. Their complete source
header notices are collected in
[Bullet-Zlib-NOTICES.txt](LICENSES/Bullet-Zlib-NOTICES.txt).
Blast's provided Boost text is retained in
[Boost-BSL-1.0.txt](LICENSES/Boost-BSL-1.0.txt). Listing a reference or header does
not assert that every upstream optional geometry implementation is linked.

## NVIDIA Flow and generated WGSL

Flow host operators, interfaces, and shader sources are from the same pinned
NVIDIA commit. They retain NVIDIA Corporation & Affiliates' 2014–2025
BSD-3-Clause source notice, copied in full to
[NVIDIA-Flow-BSD-3-Clause.txt](LICENSES/NVIDIA-Flow-BSD-3-Clause.txt).

The supplied PNanoVDB dependency credits Andrew Reidmeyer and the OpenVDB
contributors. Its complete implementation matches OpenVDB `v12.0.0`, commit
`269300808a4651daa65836214fc20f78e1c0eb7f`, after normalizing line endings and
comparing from the author header. That historical OpenVDB source is explicitly
Apache-2.0. The NVIDIA copy omits its preceding OpenVDB copyright and license
identifier; these are restored in the distribution notices, rather than
assigning MIT or assuming a later upstream license. See
[nanovdb-provenance.json](nanovdb-provenance.json) for the exact comparison,
[OpenVDB-Apache-2.0.txt](LICENSES/OpenVDB-Apache-2.0.txt),
[OpenVDB-RE-LICENSE_NOTE.txt](LICENSES/OpenVDB-RE-LICENSE_NOTE.txt), and
[PNanoVDB-OpenVDB-NOTICE.txt](LICENSES/PNanoVDB-OpenVDB-NOTICE.txt).
The exact pinned NVIDIA dependency is retained in
[PNanoVDB-source.h](LICENSES/PNanoVDB-source.h). Flow's `Sparse.cpp` and
`SparseNanoVdbExport.cpp` reference this header in the selected host build;
excluding an emitter pipeline does not exclude these host references.

Slang `2025.6.1` translates the shader corpus into WGSL. Shader compilation
preserves the NVIDIA source notice and records source, compiler, WGSL, and
reflection hashes. The WebGPU storage-access and mesh-scan adaptations are
recorded transformations of upstream shaders. They are not independently
authored replacements for NVIDIA Flow. The Slang compiler is a separately
downloaded development tool and is not included in this source package.

## fabmax bindings and WebIDL generator references

`physx-js-webidl` `v2.7.3`, commit
`ae92287fcf581c748b22d78dc5f9e3e21376fff3`, carries the MIT license,
Copyright © 2021 Max Thiele. The exact texts are retained in
[Fabmax-Bindings-MIT.txt](LICENSES/Fabmax-Bindings-MIT.txt) and
[Fabmax-Bindings-NOTICE.md](LICENSES/Fabmax-Bindings-NOTICE.md).
The original notice identifies that upstream package's PhysX 5.6.1 binary; it
is preserved as a historical notice, not the version identity of PhysX PE.

The browser binding source originates in
[fabmax/PhysX](https://github.com/fabmax/PhysX/tree/52dab13147435e818bbf10bd4230c0067e024efc),
commit `52dab13147435e818bbf10bd4230c0067e024efc`. That repository's root license
is NVIDIA BSD-3-Clause. Original source-level NVIDIA and geometry notices remain
applicable in addition to the MIT package notice. The retained patch includes
JNI reference output labeled as generated by `webidl-util`. Its Apache-2.0
text and Max Thiele's copyright are retained in
[WebIDL-Util-Apache-2.0.txt](LICENSES/WebIDL-Util-Apache-2.0.txt).
The license copy is pinned to that license file's upstream commit
`516aa7af11c93bed172ec5042fb620c6a9bd49c2`; this does not claim that the retained
JNI output was generated with that exact revision. `webidl-util` itself is not
required or distributed by this browser build.

## WebGPU TypeScript declarations

`types/webgpu.d.ts` is a verbatim copy of `gpuweb/types`'s `dist/index.d.ts`
at commit `2b7c1c80f92323b12355033b2f1b65fe406a08da`. It retains the
WebGPU Developers' BSD-3-Clause license, copied in full to
[WebGPU-Types-BSD-3-Clause.txt](LICENSES/WebGPU-Types-BSD-3-Clause.txt).
Its source and license hashes are recorded in `source-license-map.json`,
`license-provenance.json` and `upstream.lock.json`.

The TypeScript compiler used for declaration validation is Microsoft's pinned
`Microsoft.TypeScript.MSBuild` 5.9.3 development archive. The validation tool
downloads it into the ignored build workspace and executes its unmodified
compiler in a browser. The compiler and its standard library files are not
included in the runtime ZIP.

## Compiler runtime notices

The build uses Emscripten `4.0.19` and Rust `1.90.0`. Their runtime and library
notices accompany generated loader/WASM artifacts. Complete copies are retained
in [Emscripten-LICENSE.txt](LICENSES/Emscripten-LICENSE.txt),
[Rust-LICENSE-MIT.txt](LICENSES/Rust-LICENSE-MIT.txt), and
[Rust-LICENSE-APACHE.txt](LICENSES/Rust-LICENSE-APACHE.txt).
Emscripten's provided musl, libc++, libc++abi, and compiler-rt notices are in
[musl-COPYRIGHT.txt](LICENSES/musl-COPYRIGHT.txt),
[libcxx-LICENSE.TXT](LICENSES/libcxx-LICENSE.TXT),
[libcxxabi-LICENSE.TXT](LICENSES/libcxxabi-LICENSE.TXT), and
[compiler-rt-LICENSE.TXT](LICENSES/compiler-rt-LICENSE.TXT).
These include their original exceptions and component-specific terms.
Development tool installations are not shipped in the package.

## Custom integration and distribution

The original custom build-lab MIT text is preserved in
[Particle-Realms-Build-Lab-MIT.txt](LICENSES/Particle-Realms-Build-Lab-MIT.txt).
The package root [LICENSE](LICENSE) adds the maintainer's custom contribution
without deleting that existing copyright.

Distribute `LICENSE`, this file, `AUTHORS.md`, `PROVENANCE.md`, `LICENSES/`, and
their provenance manifests with the source and compiled loader/WASM/shaders.
Retain original source headers, generator notices, and prominent modification
records when redistributing adapted source. Upstream names may identify origin;
they do not imply upstream endorsement.
