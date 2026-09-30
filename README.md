# PhysX PE

PhysX PE is BTSpaniel's browser physics integration: NVIDIA PhysX SDK 5.11.0,
NVIDIA Blast 5.0.6, NVIDIA Flow compute on WebGPU, and custom Rust SIMD and
WebAssembly bridges. Maintained by Jake Wehmeier (BTSpaniel).

This repository contains the source and build instructions for the
`5.11.0-alpha.1` release. It stays private for now. GitHub Actions builds the
runtime from pinned source revisions, runs the verification gates, and retains
the runtime archive and reports. A version tag creates a private prerelease
only after verification succeeds.

## What runs where

| Component | Execution |
| --- | --- |
| PhysX rigid bodies, joints, cooking and queries | CPU, WebAssembly |
| Blast core, stress and fracture authoring bridge | CPU, WebAssembly |
| Rust pose batching and heap staging | CPU, WebAssembly SIMD |
| Flow sparse simulation graph | Native host in WebAssembly, WGSL compute on WebGPU |
| Heap uploads and downloads | Explicit transfers through the shared WebAssembly heap |

The custom runtime is named `physx-pe.mjs` and `physx-pe.wasm`. Load the module
and its matching WASM from the same release. `physx-pe.d.ts` describes the
forwarded WebIDL interface. Upstream `physx-js-webidl` names remain in source
patches and attribution records because that project supplies the binding
foundation.

## Build on GitHub

Open **Actions → Build PhysX PE → Run workflow**. Ordinary source pushes also
run the build. Download the runtime ZIP and diagnostics from the completed run.
Tags matching `v*` produce a private GitHub prerelease when all gates pass.
No separate access token or publishing service is required for that workflow.

## Build locally

Use Linux or WSL with Python 3.12, Git, CMake, Make, a host C++ compiler and an
official Rustup installation. The bootstrap installs the pinned development
tools inside the build environment; it does not change the global Rust default.
Software Flow verification also needs Mesa lavapipe (`mesa-vulkan-drivers` on
Ubuntu). CI uses Chromium for PhysX and shared-heap checks, and requests Firefox
with the selected lavapipe ICD for the Flow graph. Firefox redacts public
adapter identity; the reports distinguish this driver selection from an
observed adapter name. The capability probe executes one 1,024-thread
workgroup before the full build; SwiftShader's smaller workgroup limit cannot
run this Flow graph.

```bash
python tools/bootstrap.py --install
source work/env.sh
python -m pip install playwright==1.57.0
python -m playwright install --with-deps chromium firefox
python tools/flow_gpu_probe.py --software-vulkan --browser-engine firefox
python release.py all --software-vulkan --flow-browser-engine firefox
```

You can run `build`, `verify`, and `package` separately. Packaging refuses a
missing, failed or stale verification report. Outputs are under `dist/`; work
checkouts and test reports are local build state and are excluded from Git.

The build checks the NVIDIA and fabmax commit pins, applies the checked browser
overlay, compiles Rust and the unified runtime, and generates Flow WGSL with
Slang 2025.6.1. It does not perform an unresolved merge or fetch a floating
upstream branch. See [the pins](upstream.lock.json) and
[source selection](source-selection.json).

## Use the runtime

Serve the extracted archive with `python tools/serve.py --port 8765`, then open
`http://127.0.0.1:8765/web/index.html`. The WebAssembly file must be served as
`application/wasm`; JavaScript modules must have a JavaScript MIME type.

```javascript
import createPhysX from './dist/candidate/physx-pe.mjs';

const physics = await createPhysX({
  locateFile: name => new URL('./dist/candidate/' + name, import.meta.url).href,
});
```

The release includes the Flow host bridge under `addons/flow/` and generated
shaders under `dist/flow-wgsl/`. Flow needs a
browser with WebGPU and the requested device features; installing the WASM
alone does not enable GPU simulation. The `web/` validation page and Python
browser runners exercise the shipped interface. No npm application build is
needed. Emscripten uses its own bundled development tools during compilation.

## Verification scope

Each build produces its own hashes and reports. The gates cover Rust units and
Python FFI, C++/Rust ABI probes, 23 PhysX browser scenarios, Blast fracture,
shared-heap WebGPU transfers, and the native Flow graph. Chromium tests request
SwiftShader; the Flow runner requests software Vulkan and records any redacted
adapter identity as unknown. Actual compute and solver readbacks establish
execution and numerical checks, not adapter identity, hardware frame rates or
certification across devices.

This alpha exposes the implemented integration, not every upstream PhysX,
Blast or Flow feature. The NanoVDB emitter pipeline is excluded. Flow obstacle
coupling in this source selection is one-way velocity coupling. Experimental
physical-section solvers, solid pressure-boundary extensions and wood thermal
coupling are excluded from this release. Their presence in later playground
work does not make them verified release features.

The selected custom bridge inputs match the installed reference's source
hashes. The portable packaging and SDK overlay are a new revision; a rebuilt
binary is not claimed to be byte-identical to the historical installed binary.

## License and provenance

Original PhysX PE additions are released under [MIT](LICENSE). NVIDIA, fabmax,
webidl-util, VHACD and Emscripten retain their respective licenses and credits;
see [third-party notices](THIRD_PARTY_NOTICES.md), [authors](AUTHORS.md) and
[provenance](PROVENANCE.md).

This is an attributed integration and port. It is not represented as a
clean-room rewrite of NVIDIA's solvers or fabmax's bindings. Renaming a runtime
or changing its packaging does not change the origin of that code.
