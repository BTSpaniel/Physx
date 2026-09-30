<!-- SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) -->
<!-- SPDX-License-Identifier: MIT -->

# PhysX PE

**PhysX, Blast and Flow for the browser, with Rust batching and WebGPU integration.**

PhysX PE combines NVIDIA PhysX 5.11.0, NVIDIA Blast 5.0.6 and NVIDIA Flow in a
browser runtime maintained by [Jake Wehmeier (BTSpaniel)](https://github.com/BTSpaniel).
It provides a JavaScript module, a matching WebAssembly binary, TypeScript
declarations and the adapters and shaders needed to run Flow on WebGPU.
Use the prebuilt files directly with browser ES modules. No npm or application
build step is required.

**[Downloads](#downloads) · [Quick start](#quick-start) · [Flow setup](#flow-on-webgpu) · [Build from source](#build-from-source)**

Built for [Particle Realms Engine](https://github.com/BTSpaniel/particlerealms.engine).
Use PhysX PE on its own or explore the engine's public distribution for the
broader WebGPU platform.

## Downloads

**[PhysX PE 5.11.0-alpha.2](https://github.com/BTSpaniel/Physx/releases/tag/v5.11.0-alpha.2)**
is an alpha prerelease. Start with the complete runtime ZIP for the examples,
Flow shaders, license notices and verification reports.

| Download | Contents |
| --- | --- |
| [Complete runtime ZIP](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.2/physx-pe-5.11.0-alpha.2-runtime.zip) | Matched runtime, Flow adapters and shaders, browser examples, local server, licenses and reports. |
| [physx-pe.mjs](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.2/physx-pe.mjs) | JavaScript module loader. Requires the matching WASM below. |
| [physx-pe.wasm](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.2/physx-pe.wasm) | Compiled PhysX, Blast, Rust and Flow host runtime. |
| [physx-pe.d.ts](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.2/physx-pe.d.ts) | TypeScript declarations for the forwarded PhysX WebIDL interface. |
| [SHA256SUMS](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.2/SHA256SUMS) · [Release manifest](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.2/release-artifacts.json) | File integrity and the source revision used for the release. |

Download the loader and WASM from the same release and host them together with
your application. The individual files are convenient for embedding; the ZIP
contains the complete distribution, including notices required for redistribution.
GitHub's automatically generated **Source code** archives contain build inputs,
not prebuilt runtime binaries.

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
and [TypeScript declarations](https://github.com/BTSpaniel/Physx/blob/v5.11.0-alpha.2/types/physx-pe.d.ts)
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

## Package and source map

| Path | Purpose |
| --- | --- |
| `dist/candidate/` | Matched loader, WASM, declarations and build manifest inside the runtime ZIP. |
| `dist/flow-wgsl/` | Generated Flow shaders, reflection files and manifest inside the runtime ZIP. |
| `addons/flow/` | Flow host adapter, shared-heap WebGPU bridge and source build tools. |
| `addons/blast/` | Blast bridge and source build tools. |
| `bridge/` · `rust/` | Native transfer interfaces, JavaScript batching adapters and Rust implementation. |
| `web/` | Browser example, validation page and simulation suite. |
| `types/` | Reviewed PhysX declarations in the source repository. |
| `reports/` | Verification receipts in the runtime ZIP; generated test output in a source checkout. |
| `LICENSES/` | Complete upstream license texts and notices. |

The runtime ZIP includes the browser adapters, not every source build tool.
Clone this repository to change or rebuild the native integration. Temporary
build checkouts stay in `work/`; publishable files are collected in `dist/release/`.

## Build from source

Use Linux or WSL with Python 3.12, Git, CMake, Make, a host C++ compiler and
Rustup. Bootstrap installs the pinned toolchain locally without changing your
global Rust default. Software Flow verification also needs Mesa lavapipe
(`mesa-vulkan-drivers` on Ubuntu).

```bash
git clone https://github.com/BTSpaniel/Physx.git
cd Physx
python tools/bootstrap.py --install
source work/env.sh
python -m pip install playwright==1.57.0
python -m playwright install --with-deps chromium firefox
python tools/flow_gpu_probe.py --software-vulkan --browser-engine firefox
python release.py all --software-vulkan --flow-browser-engine firefox
```

You can run `build`, `verify` or `package` separately. Release commands require a
clean, committed source tree. Packaging rejects missing, failed or stale
verification, checks file hashes, and runs the extracted archive before
accepting it. Emscripten 4.0.19, Rust 1.90.0, Slang 2025.6.1 and the source
revisions are pinned in [upstream.lock.json](upstream.lock.json).

For a hosted build, use **Actions → Build PhysX PE → Run workflow**.
Successful runs retain runtime files and reports. Version tags create an alpha
prerelease only after the build and verification gates pass. Emscripten uses
its bundled development tools during compilation; consumers need no npm setup.

## Verification and alpha scope

Each runtime archive carries `runtime-manifest.json`,
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

This alpha exposes the implemented integration, not every upstream feature.
The NanoVDB emitter pipeline, experimental physical-section solvers, solid
pressure-boundary extensions and wood thermal coupling are excluded. Flow
obstacle coupling is one-way velocity coupling. Full vehicle, character
controller, articulation and serialization coverage, long-duration leak checks,
deterministic replay and complete Particle Realms integration are outside this
release's verification claim. See [source selection](source-selection.json)
and [provenance](PROVENANCE.md) for the precise boundary.

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
and the [shipped TypeScript declarations](https://github.com/BTSpaniel/Physx/blob/v5.11.0-alpha.2/types/physx-pe.d.ts).
Upstream documentation may describe interfaces or backends outside this
release's exposed and verified subset.
