<!-- SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) -->
<!-- SPDX-License-Identifier: MIT -->

# PhysX PE

**PhysX, Blast and Flow for the browser, with Rust SIMD and WebGPU integration.**

PhysX PE combines NVIDIA PhysX 5.11.0, Blast 5.0.6, Flow and fabmax-derived
bindings in an attributed browser runtime maintained by
[Jake Wehmeier (BTSpaniel)](https://github.com/BTSpaniel).
Built for [Particle Realms Engine](https://github.com/BTSpaniel/particlerealms.engine);
you can also use the SDK independently with browser ES modules. No npm setup is required.

**Current alpha prerelease: [5.11.0-alpha.3](https://github.com/BTSpaniel/Physx/releases/tag/v5.11.0-alpha.3)**,
published 2026-10-01 from commit
[`53c0cd99`](https://github.com/BTSpaniel/Physx/commit/53c0cd99d0695a23b89047bde0f000fdecaaa363).
Start with the complete runtime ZIP below.

## Downloads

**[PhysX PE 5.11.0-alpha.3](https://github.com/BTSpaniel/Physx/releases/tag/v5.11.0-alpha.3)**
is an alpha prerelease. Start with the complete runtime ZIP.

| Download | Contents |
| --- | --- |
| [Complete runtime ZIP](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/physx-pe-5.11.0-alpha.3-runtime.zip) | Matched runtime, Flow adapters and shaders, browser examples, local server, licenses and verification reports. |
| [physx-pe.mjs](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/physx-pe.mjs) | JavaScript module loader. Requires the matching WASM below. |
| [physx-pe.wasm](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/physx-pe.wasm) | Compiled PhysX, Blast, Rust and Flow host runtime. |
| [physx-pe.d.ts](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/physx-pe.d.ts) | PhysX WebIDL and 96 source-derived addon declarations. |
| [SHA256SUMS](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/SHA256SUMS), [Runtime ZIP checksum](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/physx-pe-5.11.0-alpha.3-runtime.zip.sha256), [Release manifest](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/release-artifacts.json) | File integrity, matched archive and exact compiled source revision. |

Download the loader and WASM from the same release and host them together with
your application. The ZIP contains the full notices needed for redistribution.
The [historical alpha.2 release](https://github.com/BTSpaniel/Physx/releases/tag/v5.11.0-alpha.2)
remains available with its original assets and verification scope.

For native rebuilding, clone the exact release tag in [Build from source](#build-from-source).

**Build sources:** [Portable source-build kit](https://github.com/BTSpaniel/Physx/releases/download/source-kit-v5.11.0-alpha.3/physx-pe-5.11.0-alpha.3-source-build-kit.zip) and [SHA256 checksum](https://github.com/BTSpaniel/Physx/releases/download/source-kit-v5.11.0-alpha.3/physx-pe-5.11.0-alpha.3-source-build-kit.zip.sha256). This separate source release contains a genuine Git bundle, clone instructions and full licenses; it is not a runtime binary.

## Quick start

1. Download and extract the **Complete runtime ZIP**.
2. Open a terminal in the extracted directory and start the included server:

   ```bash
   python tools/serve.py --port 8765
   ```

3. Open the [falling-body example](http://127.0.0.1:8765/web/quickstart.html).
   For the full included smoke suite, open
   [runtime validation](http://127.0.0.1:8765/web/index.html) and select
   **Run validation**. The suite runs real PhysX simulations in a Worker,
   checks the matched runtime files and lets you save its report.

For your own application, put `physx-pe.mjs` and `physx-pe.wasm` beside each
other. Import and await the module factory from a JavaScript module in that
same directory:

```javascript
import createPhysX from './physx-pe.mjs';

const PhysX = await createPhysX({
  locateFile: name => new URL(name, import.meta.url).href,
});
```

Your application creates the scene and owns its simulation loop and native
resources. The complete ZIP keeps the matched files in `dist/candidate/`;
use that path when importing directly from the extracted package. See the
[quick-start source](web/quickstart.html), [simulation suite](web/suite.mjs)
and [TypeScript declarations](https://github.com/BTSpaniel/Physx/blob/v5.11.0-alpha.3/types/physx-pe.d.ts)
for the exposed calls.

Serve ES modules over HTTP on localhost or HTTPS in deployment, never
`file://`. Serve `.mjs` as JavaScript and `.wasm` as `application/wasm`; the
included server supplies these types. JavaScript garbage collection does not
release PhysX objects. The release uses single-threaded WebAssembly with SIMD.

## What runs where

| Component | Execution |
| --- | --- |
| PhysX rigid bodies, joints, articulations, cooking and queries | CPU, WebAssembly |
| Blast core, stress and fracture authoring bridge | CPU, WebAssembly |
| Rust pose batching and heap staging | CPU, WebAssembly SIMD |
| Flow sparse simulation | Host graph in WebAssembly; WGSL compute on WebGPU |
| Native memory to GPU transfers | Explicit uploads and readbacks through the shared WASM heap |

WebGPU accelerates Flow's compute work. The PhysX rigid-body solver remains on
the CPU. CUDA and PhysX's native CUDA GPU backend are unavailable in this build.

## Flow on WebGPU

Use the complete runtime ZIP. Flow needs `addons/flow/flow_host_webgpu.mjs`
and the entire `dist/flow-wgsl/` directory, including its manifest and
reflection files. The WASM alone is not enough.

The device must support `float32-filterable`, at least **1,024 compute
invocations per workgroup**, and a **1,024-lane X workgroup**. Request these
capabilities explicitly. WebGPU availability alone does not guarantee support.

Save the following module at the extracted package root and import it from an
HTML page served by the included server. It advances two seconds of simulation
and releases its resources; it does not include a volume renderer.

```javascript
import createPhysX from './dist/candidate/physx-pe.mjs';
import { FlowHostWebGpu } from './addons/flow/flow_host_webgpu.mjs';

if (!navigator.gpu) throw new Error('WebGPU is unavailable in this browser');
const adapter = await navigator.gpu.requestAdapter();
if (!adapter) throw new Error('No WebGPU adapter is available');
const device = await adapter.requestDevice({
  requiredFeatures: ['float32-filterable'],
  requiredLimits: {
    maxComputeInvocationsPerWorkgroup: 1024,
    maxComputeWorkgroupSizeX: 1024,
  },
});

let flow;
try {
  const PhysX = await createPhysX();
  const shaders = new URL('./dist/flow-wgsl/', import.meta.url).href;
  flow = await FlowHostWebGpu.create(PhysX, device, shaders);
  flow.setEmitter({ position: [0, 0, 0], radius: 0.45, enabled: true });
  for (let frame = 0; frame < 120; frame++) await flow.step(1 / 60);
  console.log('Completed Flow frames:', flow.stats.frames);
  console.log('Active sparse blocks:', flow.stats.activeBlocks);
} finally {
  try { await flow?.dispose(); } finally { device.destroy(); }
}
```

A renderer can consume `flow.output` while the host is alive. Await each step
and dispose the host before destroying the device. The host borrows its device,
so your application can share a device already owned by its renderer.

## Build from source

Use Linux or WSL with Python 3.12, Git, CMake, Make, a host C++ compiler and Rustup.
Clone the release tag to preserve the source revision used by the published runtime:

```bash
git clone --branch v5.11.0-alpha.3 https://github.com/BTSpaniel/Physx.git
cd Physx
git rev-parse HEAD
python tools/bootstrap.py --install
source work/env.sh
python -m pip install playwright==1.57.0
python -m playwright install --with-deps chromium firefox
python release.py build
```

The source commit must be `53c0cd99d0695a23b89047bde0f000fdecaaa363`.
Emscripten 4.0.19, Rust 1.90.0, Slang 2025.6.1 and upstream inputs are pinned.
Build commands require a clean committed source tree. A new build needs its own
verification; this release's reports do not admit different compiled bytes.

For an offline source checkout, download and extract the verified source-build kit, then run:

```bash
git clone --branch v5.11.0-alpha.3 physx-pe-source.bundle Physx
```

Its actual bundle clone reproduces all 1,608 committed input files and passes the original source and selection checks. Keep the outer kit files outside the checkout.

For a hosted build, open **Actions -> Build PhysX PE -> Run workflow**.
The existing tag workflow compiles, verifies and tests the extracted runtime
before publishing it. Local verification uses the existing shared host lock;
Flow software verification needs actual Mesa lavapipe and the supported browser.
See [SOURCE_BUILD_KIT.md](SOURCE_BUILD_KIT.md) for full build, lock, verification,
portable-source and admission requirements. GitHub's automatic source ZIPs omit
the Git history required by the committed-source guard.

## Verification and alpha scope

The [tagged alpha.3 CI run](https://github.com/BTSpaniel/Physx/actions/runs/36867355569)
passed all ten standalone SDK phases, including native PhysX/Blast, shared-heap
checks, all 124 WGSL modules, representative numerical compute and the real Flow
host graph with resource cleanup. The extracted archive passed all 37 browser
cases and its falling-body example. All seven published assets were downloaded
without authentication and matched the tagged build byte for byte.

The run requested Mesa lavapipe through Firefox on Ubuntu; the browser did not
report its backend identity. These checks establish functional and archive
delivery results, not hardware real-time performance or full upstream parity.
Engine and platform installation have additional qualification gates.

Physical-section, transactional Flow and wood-thermal sources are included.
The thermal solver remains experimental and can exceed real-time budgets.
Momentum exchange covers terminal normal exchange and leaves heat unapplied.
See [source selection](source-selection.json), [source build guide](SOURCE_BUILD_KIT.md)
and [provenance](PROVENANCE.md) for the precise scope and historical evidence.

## Credits and license

PhysX PE is an attributed integration and browser port. NVIDIA created PhysX,
Blast and Flow; [Max Thiele (fabmax)](https://github.com/fabmax/physx-js-webidl)
created the binding foundation. This release retains the fabmax v2.7.3 binding
lineage and independently pins NVIDIA's 5.11.0 SDK. It does not claim to be the
fabmax v2.8.0 package or a clean-room rewrite of either project.

Original PhysX PE additions are [MIT licensed](LICENSE). Upstream components
retain their own licenses and copyrights, including NVIDIA, fabmax, webidl-util,
V-HACD, OpenVDB, Emscripten and Rust contributors. Preserve the distribution's
[third-party notices](THIRD_PARTY_NOTICES.md), [authors](AUTHORS.md),
[provenance](PROVENANCE.md) and `LICENSES/` when redistributing it.

API references: [NVIDIA PhysX documentation](https://nvidia-omniverse.github.io/PhysX/),
[Emscripten module options](https://emscripten.org/docs/api_reference/module.html)
and the [shipped TypeScript declarations](https://github.com/BTSpaniel/Physx/blob/v5.11.0-alpha.3/types/physx-pe.d.ts).
Upstream documentation may describe interfaces or backends outside this
release's exposed and verified subset.
