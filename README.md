<!-- SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel) -->
<!-- SPDX-License-Identifier: MIT -->

# PhysX PE

**NVIDIA PhysX, Blast and Flow in the browser, with Rust batching and WebGPU integration.**

PhysX PE combines NVIDIA PhysX 5.11.0, NVIDIA Blast 5.0.6 and NVIDIA Flow in a
browser runtime maintained by [Jake Wehmeier (BTSpaniel)](https://github.com/BTSpaniel).
It provides a JavaScript module, a matching WebAssembly binary, TypeScript
declarations, and the adapters and shaders needed to run Flow on WebGPU.
Applications consume browser ES modules directly; no npm or application build
step is required.

The latest published runtime prerelease is
[5.11.0-alpha.3](https://github.com/BTSpaniel/Physx/releases/tag/v5.11.0-alpha.3),
published **2026-10-01**, from source commit
[`53c0cd99d0695a23b89047bde0f000fdecaaa363`](https://github.com/BTSpaniel/Physx/commit/53c0cd99d0695a23b89047bde0f000fdecaaa363).
The downloads below belong to that release.

This source checkout prepares **5.11.0-alpha.4**. It is **unpublished and pending
a complete fresh build, verification, packaging and Engine admission**. Selected
source, successful individual compilation steps and historical tests do not
establish those results. No alpha.4 download or performance admission is
announced here. See [SOURCE_BUILD_KIT.md](SOURCE_BUILD_KIT.md) for the selected
changes, exact recipes and verification boundary.

**[Downloads](#downloads) · [Quick start](#quick-start) · [Flow setup](#flow-on-webgpu) · [Build from source](#build-from-source)**

Built for [Particle Realms Engine](https://github.com/BTSpaniel/particlerealms.engine).
PhysX PE can also be used independently of the engine.

## Downloads

Start with the complete **published alpha.3** runtime ZIP for the examples,
Flow adapters and shaders, license notices and verification reports.

| Download | Contents |
| --- | --- |
| [Complete runtime ZIP](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/physx-pe-5.11.0-alpha.3-runtime.zip) | Matched runtime, Flow adapters and shaders, browser examples, local server, licenses and reports. |
| [physx-pe.mjs](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/physx-pe.mjs) | JavaScript module loader; requires the matching WASM. |
| [physx-pe.wasm](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/physx-pe.wasm) | Published compiled runtime. |
| [physx-pe.d.ts](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/physx-pe.d.ts) | Declarations for the published runtime. |
| [SHA256SUMS](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/SHA256SUMS) · [ZIP checksum](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/physx-pe-5.11.0-alpha.3-runtime.zip.sha256) · [Release manifest](https://github.com/BTSpaniel/Physx/releases/download/v5.11.0-alpha.3/release-artifacts.json) | Asset integrity and the published source revision. |

Always download the loader and WASM from the same release and host them together.
The individual files are convenient for embedding; the ZIP contains the full
distribution, including notices required for redistribution. GitHub's automatic
**Source code** archives contain build inputs, not prebuilt runtime binaries.
Private development builds and the pending alpha.4 source selection are separate
from these published alpha.3 assets.

## Quick start

1. Download and extract the **Complete runtime ZIP**.
2. Open a terminal in the extracted directory and start the included server:

   ```bash
   python tools/serve.py --port 8765
   ```

3. Open the [falling-body example](http://127.0.0.1:8765/web/quickstart.html).
   For the included smoke suite, open
   [runtime validation](http://127.0.0.1:8765/web/index.html) and select
   **Run validation**. The suite runs real PhysX simulations in a Worker,
   checks the matched runtime files and lets you save its report.

For your application, put `physx-pe.mjs` and `physx-pe.wasm` beside each other.
Import and await the module factory from a JavaScript module in that directory:

```javascript
import createPhysX from './physx-pe.mjs';

const PhysX = await createPhysX({
  locateFile: name => new URL(name, import.meta.url).href,
});
```

Your application creates the scene and owns its simulation loop and native
resources. The ZIP keeps the matched files in `dist/candidate/`; use that path
when importing directly from the extracted package. See the
[quick-start source](web/quickstart.html), [simulation suite](web/suite.mjs)
and [published declarations](https://github.com/BTSpaniel/Physx/blob/v5.11.0-alpha.3/types/physx-pe.d.ts).

Serve ES modules over HTTP on localhost or HTTPS in deployment, never `file://`.
Serve `.mjs` as JavaScript and `.wasm` as `application/wasm`; the included server
supplies these types. JavaScript garbage collection does not release PhysX
objects. The runtime uses single-threaded WebAssembly with SIMD.

## What runs where

| Component | Execution |
| --- | --- |
| PhysX rigid bodies, joints, articulations, cooking and queries | CPU, WebAssembly |
| Blast core, stress and fracture authoring bridge | CPU, WebAssembly |
| Rust pose batching and heap staging | CPU, WebAssembly SIMD |
| Selected experimental wood-thermal kernels | CPU, WebAssembly; API choice determines the numerical method |
| Flow sparse simulation | Host graph in WebAssembly; WGSL compute on WebGPU |
| Native memory to GPU transfers | Explicit uploads and readbacks through the shared WASM heap |

WebGPU accelerates Flow's compute work. The PhysX rigid-body and Blast solvers
remain on the CPU. CUDA and PhysX's native CUDA GPU backend are unavailable in
this build. The selected thermal kernels are not GPU thermal solvers.

## Flow on WebGPU

Use the complete runtime ZIP. Flow needs `addons/flow/flow_host_webgpu.mjs`
and the entire `dist/flow-wgsl/` directory, including its manifests and reflection
files. The WASM alone is not enough.

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
so an application can share a device already owned by its renderer.

## Selected alpha.4 changes

These changes describe the pending source selection, not the published alpha.3
download or a completed alpha.4 qualification:

- The explicit wood-thermal ABI1 kernel rejects a nonfinite intermediate char
  reaction rate with its existing status `6`, before the positive-rate timestep
  test. Finite equations and their evaluation order remain unchanged. Callers
  must still stage state and commit only after successful validation.
- Blast's accurate physical-section processor avoids preparing unused inherited
  base-processor data. Revision 3 retains its own geometry, physical scaling,
  factor construction and solve path; legacy processors remain separate. This
  removes redundant preparation without changing material laws, solver tolerance
  or iteration budgets. A performance claim requires a matched runtime test.
- Thermal ABI1 and ABI2/numerical version 9 remain separate interfaces. ABI1 uses
  the existing explicit adaptive method. The selected stable-depletion compile
  option evaluates the frozen-temperature loss with `log1p`/`expm1`, so numerical
  compatibility uses declared tolerances rather than a bit-exact JavaScript
  claim. ABI2/numerical version 9 uses adaptive implicit SDIRK transport and
  separate controls, workspace and diagnostics; it is not a drop-in ABI1
  acceleration or an automatic Engine integrator switch.

## Package and source map

| Path | Purpose |
| --- | --- |
| `dist/candidate/` | Matched loader, WASM, declarations and build manifest inside the runtime ZIP. |
| `dist/flow-wgsl/` | Generated Flow shaders, reflection files and manifests. |
| `addons/flow/` | Flow host adapter, shared-heap WebGPU bridge and source build tools. |
| `addons/blast/` | Blast bridge and source build tools. |
| `addons/thermal/` | Selected experimental CPU-WASM thermal sources in the source checkout. |
| `bridge/` · `rust/` | Native transfer interfaces, JavaScript batching adapters and Rust implementation. |
| `web/` | Browser examples, validation page and simulation suite. |
| `types/` | Generated PhysX/addon declarations and pinned WebGPU types. |
| `reports/` | Verification receipts in the ZIP; generated test output in a source checkout. |
| `LICENSES/` | Complete upstream license texts and notices. |

The ZIP includes browser adapters, not every source build tool. Clone this
repository to rebuild the native integration. Temporary build checkouts stay in
`work/`; publishable files are collected in `dist/release/`.

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
python release.py build
```

Select the intended committed source revision before building. Cloning the
public repository does not imply that a pending private successor is published.
Use [SOURCE_BUILD_KIT.md](SOURCE_BUILD_KIT.md) for the existing-toolchain Windows
route and selected native source checks.

Run `build`, `verify` and `package` separately. Release commands require a clean,
committed source tree. Packaging rejects missing, failed or stale verification,
checks hashes, and executes the extracted archive. Emscripten 4.0.19, Rust 1.90.0,
Slang 2025.6.1 and upstream source revisions are pinned in
[upstream.lock.json](upstream.lock.json).

For local verification alongside an Engine checkout, provide its shared lock:
`python release.py verify --software-vulkan --flow-browser-engine firefox
--lock-workspace /path/to/particlerealms.engine`. The bounded CPU endurance phase
uses this lock to serialize heavy tests. Hosted workflows use `--ci-isolated`,
which requires the actual GitHub-hosted Actions environment and cannot bypass
local isolation.

Use **Actions → Build PhysX PE → Run workflow** for a hosted build. Successful
runs retain runtime files and reports. Version tags create an alpha prerelease
only after the build and verification gates pass. Consumers need no npm setup.

## Verification and limits

The [published alpha.3 release](https://github.com/BTSpaniel/Physx/releases/tag/v5.11.0-alpha.3)
reports functional browser/ABI checks, actual Flow compute and an extracted
archive test in CI. Its archive receipts apply to its exact published bytes and
source commit. Chromium requests SwiftShader; Firefox Flow requests software
Vulkan and records redacted adapter identity as unknown. This is not a hardware
frame-rate benchmark, full upstream SDK certification or compatibility matrix.

The current selected source defines 37 functional CPU-WASM checks and 96
source-derived addon declarations. Its browser suite includes D6 and articulation
drives, controller floor/wall collision, convex and triangle mesh cooking,
64-bit serialization IDs, Vehicle2 behavior, native contacts/triggers and
same-build replay. TypeScript consumers check actual addon pointer, arity and
return types in a browser. Source presence and generated declarations do not
replace execution on a fresh matched pair.

Experimental thermal performance and physical fidelity remain limited. A
separate standalone ABI1 compatibility probe passed eight bounded cases with
mass/energy closure and allocation cleanup. One captured **0.1 s** thermal input
required **1,095 adaptive substeps**; on that Chrome/Windows fixture the median
native total cost was approximately **269 ms**, including preparation, transfer
and commit, versus approximately **593 ms** for JavaScript. This is a standalone
fixture result, not a unified alpha.4, complete-scene or realtime result. Caller
refinement also observed internal temperatures around **9,260–10,060 K**.
Matching the explicit JavaScript state and ledger does not validate those peaks
or establish physical realism or transient peak convergence. ABI2 is a different
numerical method and cannot silently replace ABI1 to improve a timing result.

Earlier bounded CPU tests exercised 288 scene lifecycles, 4,608 dynamic actors,
576 bulk contexts and 288 Blast checks, with matching ten-minute physical-time
replays on one Windows device. Those historical receipts establish their own
fixtures only. They do not certify a future source revision, unlimited-duration
leak absence, cross-build replay or a device matrix.

The pending source includes physical-section, thermal and transactional Flow
extensions and 124 shader kernels. Fresh native, browser, GPU, extracted-package
and Engine integration gates remain independent requirements. Momentum exchange
currently covers terminal normal exchange; heat remains an explicit unapplied
obligation. Rendered frame rate alone does not measure completed physical time.
See [source selection](source-selection.json), [source build guide](SOURCE_BUILD_KIT.md)
and [provenance](PROVENANCE.md) for source and admission boundaries.

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
and the [published declarations](https://github.com/BTSpaniel/Physx/blob/v5.11.0-alpha.3/types/physx-pe.d.ts).
Upstream documentation may describe interfaces or backends outside this
release's exposed and verified subset.
