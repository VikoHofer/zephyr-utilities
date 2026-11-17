# TinyML MNIST chooser and Project Creator

Project Creator creates an MNIST digit-recognition application from a source template
bundled with Zephyr Utilities, for an exact board target and installed Zephyr SDK. The
common firmware
uses the Zephyr serial shell and TensorFlow Lite Micro for sensor-free inference on
30 embedded handwritten-digit images. TinyML selects the demo, exact board target
and SDK; the existing Projects workflow owns file review, creation and builds. Trained
model/reference assets and TensorFlow Lite Micro are installed separately into managed
data storage. Creation and builds do not read or depend on another application.

## Install the offline runtime assets

Install the local asset pack and reviewed TensorFlow Lite Micro checkout explicitly:

```sh
zephyr-utils --install-mnist-assets /path/to/mnist-pack --tflm-source /path/to/tflite-micro
```

The installation runs before the same application starts. `PACK` and `FRAMEWORK` are
local directories; nothing is downloaded, trained or installed with pip or west. Use
the same `--data-dir` for installation and subsequent launches. The pack contains:

```text
model/mnist.tflite
model/metadata.json
model/model_metadata.hpp
data/test_images.bin
data/test_samples.inc
data/NOTICE.rst
LICENSE
```

The framework must be the reviewed detached revision
`fcc760af130f3a595b5802cdebcc77461e54f382` with its understood Zephyr module metadata.
Installation copies and fingerprints the inputs in:

```text
<data-dir>/.features/projects/tinyml-runtime/mnist-v1/
    model/...
    data/...
    framework/tflite-micro/...
    framework/tflite-micro/REVISION
    manifest.json
```

The manifest binds the model, reference assets, framework bytes and pinned revision.
The installed framework retains a verified `REVISION` marker instead of requiring the
source checkout's Git directory. Installing identical inputs again preserves the existing
installation; a different or damaged installation is reported without overwriting it.
Ordinary launch, discovery, preview, creation and build never install assets or search
application folders for missing inputs. A missing pack leaves a visible setup reason.
The UART drawing viewer remains independent of asset installation and project creation.

## Create and build

1. Start `zephyr-utils` in the existing Zephyr environment, select the workspace and
   analyze its environment in Projects.
2. Open **TinyML Demo Creator**, select **MNIST digit recognition**, search for a
   board, choose its exact target and an installed GNU SDK. Read local requirements
   and any unavailable reason, then choose **Continue in Project Creator**.
3. Projects preselects the MNIST runtime, board target and SDK. If its form already
   has changes, choose **Keep current draft** or **Use demo selection**; accepting
   retains the name and destination. Enter a new destination before previewing.
   The source is the Utilities-owned template and installed asset pack. The MNIST
   entry also remains available through Projects' **Local Zephyr sample** picker.
4. Use **Check compatibility** to configure a temporary reviewed copy. This checks
   the selected target's framework and console settings; it does not compile or
   run inference.
5. Choose **Preview application**, inspect the files and commands, then explicitly
   create the application. Creation does not configure, compile or flash it.
6. Open the saved application under **Applications**, then review and run
   **Configure** or **Build** through the existing controls.

The copy includes an editable default overlay named for the selected exact target.
The H753-specific configuration remains scoped to that board. Other boards use the
common firmware and must pass their own checks. A discovered board or a successful
Configure result alone does not establish that the demo fits or runs on that board.
Continuing uses an opaque Projects selection retained for at most 30 minutes, with
at most 32 selections in server memory. Restart, expiry, workspace/source/dependency
changes or changed target/environment evidence require a fresh chooser selection.
Resolving it creates no application files or jobs. Late replies cannot replace a
form edited during loading or a different workspace/view. Additional ML demos and
framework variants remain planned.
The Float32 model uses about 200 KiB of ROM; the application also needs its images,
firmware, a 16 KiB tensor arena, two 8 KiB stacks and runtime overhead.

To reuse the same application on another board, choose **Add configuration** under
**Applications** and save another exact target/SDK selection. Configure and build
that configuration separately. Build results belong to their captured target,
SDK, source and output directory.

## Source ownership and retained evidence

The bundled `features/projects/mnist_template/` owns the C++ runtime, common and
board-scoped configuration, README, Apache-2.0 license and attribution. It was adapted
once from the earlier demonstration as a development reference. That original app
and every generated app are excluded from future template lookup. Utilities does
not ship trained models, images or captured device data in Git or its wheel.

Each created application owns its runtime source, model metadata, reference scores
and embedded model/image bytes. The binary assets become lossless textual C
initializers; no training or model conversion runs. CMake uses the requested name,
for example `project(ai_demo)`, and compiles that app's own `src/` files. Its only
MNIST framework reference points to the managed `framework/tflite-micro` installation,
not another application's `.deps` directory.

`zephyr-sample.json` records adapter `mnist-runtime-v2`, adapter version 2, bundled
source hashes, installed-asset identity, model/image hashes, framework identity,
license evidence and transformations. The existing outer provenance schemas 1/3
remain in use. Dependency byte fingerprints detect local edits even when a revision
marker stays unchanged. Source, asset or framework changes require refreshing the
selection and reviewing the current plan again. Attribution is retained in
`NOTICE.rst`, `data/NOTICE.rst` and `data/LICENSE-MODEL.txt`.

Keep the managed framework installation available when building, like the selected
Zephyr checkout and SDK. On another machine, install the same framework revision and
deliberately set `MNIST_TFLM_DIR` to its location. An application is independent of
other applications; its framework dependency remains external.

Version-one creation plans require a fresh preview. Existing project records and
build histories remain readable, and saved files are not silently rewritten on
launch. Deliberate migration of an older generated app must first retain a backup
and migration receipt, then run a fresh Configure against the managed framework;
old build evidence does not establish the migrated configuration. See
[ADR 0029](decisions/0029-independent-mnist-template.md).

## Run the demo

After using the normal external tools to flash or otherwise start the firmware,
connect to its serial shell and run:

```text
ai info
ai list
ai show 0
ai infer 0
ai test
```

`ai info` reports the running target and model information. `ai show 0` prints an
embedded image, `ai infer 0` reports predictions and scores, and `ai test` compares
all 30 images with their recorded host reference scores. Reference-score agreement
and correct classification are separate observations; neither is a general accuracy
or timing guarantee for another model or dataset.

## Draw and infer over UART

Open **TinyML → Draw a digit**, select a UART port and choose **Open UART**. This
view is independent of the demo, board and SDK chooser. Open connects to the port
without querying the firmware or waiting for a response. The interface uses 115200
baud, 8 data bits, no parity and one stop bit. UART discovery currently supports
POSIX hosts with advertised `/dev` character devices; unsupported hosts retain an
explicit unavailable reason. Close another serial terminal if it already owns the port.

Draw a single white digit on the black canvas with a pointer or touch. Keyboard users
can move the cursor with arrows, draw with Shift+arrows, and stamp with Space/Enter.
The prepared 28×28 preview updates after a stroke; **Update image preview** explicitly
retries preparation. Normalization reuses the demo tool's crop, 20×20 LANCZOS scaling
and mass-centering algorithm. **Clear drawing** preserves any previous run's evidence.

Choose **Send drawing** to transfer the exact preview using the existing MNIST
tool's `mnist-gray8-v1` protocol, row acknowledgments and CRC. The viewer attempts
communication directly; there is no target or firmware preflight. A missing response,
unsupported command, malformed response or disconnect produces a nearby communication
error. Successful sends keep the port open for the next drawing. **Close UART**
releases the connection; it can also stop an active transfer. **Cancel inference**
stops an active transfer and releases its connection. Cancelling before a queued
transfer starts can leave the connection open. Refreshing ports or opening the view
never opens a UART connection. Flashing remains an external action.

The result shows the board prediction, ten class scores and reported inference time
in microseconds. This is **Invoke only**: it excludes image preparation and UART
transfer. A reported zero is not evidence of physical inference performance. Port,
input SHA-256 and CRC remain available under **Run details**. Without a metadata
query, firmware, target, model, SDK and build identity remain unknown.

The result retains the image used for that run. Editing or clearing the canvas cannot
relabel an earlier result as the new drawing. Leaving/reopening TinyML reconnects to
the open connection and retained jobs without cancelling them; switching workspace
cannot retarget an active transfer. The connection is shared across workspaces;
results retain their captured workspace. Drawings are browser-session state and inference evidence stays in the
bounded shared job manager; neither is automatically saved as a dataset or file.

The drawing adapter is bundled with its Apache-2.0 attribution and uses Pillow and
pyserial from the existing application environment. It never imports workspace tools.
The generated `README.rst` describes the independent template and managed dependencies.
See [ADR 0028](decisions/0028-tinyml-drawing-uart.md) for the device and evidence boundary.

## Independent-template validation

The independent template passed 1,665 Python tests and 609 frontend tests, quick
checks and installed-wheel verification on 2026-11-15. Tests cover absent original
applications, no fallback, explicit asset installation, independent names, changed
inputs, immutable installation, cancellation and retained provenance. Browser checks
covered the bundled-template chooser handoff and reviewed CMake before creation.

The existing `ai_demo` was migrated after verifying its captured files were unchanged.
Its previous files and project record were backed up, and a migration receipt records
the new adapter. Earlier build attempts were preserved. A fresh temporary Configure
and Build passed for `frdm_mcxn947/mcxn947/cpu0` with Zephyr 4.4.2 and SDK 1.0.1.
The linker reported 355,904 bytes Flash and 39,264 bytes RAM; these are build sizes,
not runtime peak RAM. The requirements checker accepted its configured framework,
owned C++ sources and serial shell. File-access tracing of this fresh build found no
access to `applications/mnist_shell`. No firmware was flashed or run on hardware.

The historical results below describe earlier implementations and captured inputs.

## Historical validation

The earlier workspace-source adapter configured and compiled on 2026-11-15 with
Zephyr 4.4.2, SDK 1.0.1 and the pinned dependency above:

| Exact target | Configure | Compile | Linker-reported Flash / RAM |
| --- | --- | --- | --- |
| `nucleo_f446re/stm32f446xx` | Passed | Passed | 351,136 / 39,424 bytes |
| `nrf52840dk/nrf52840` | Passed | Passed | 355,548 / 39,488 bytes |

The MNIST requirements checker independently accepted both fresh configurations.
These sizes describe these builds; they are not runtime peak RAM measurements.
Builds used temporary copies/output directories and left the original demo intact.
No flashing or physical inference was performed. Other targets need their own
configuration, compilation and runtime evidence.

Regression tests cover exact-target overlays, reviewed publication, asset identity,
unsafe/missing inputs, changed dependencies, runtime source ownership and browser
selection invalidation. Full repository and installed-package checks passed.
The chooser continuation passed 1,599 Python tests and 575 frontend tests, plus the
installed-package asset check. Browser checks covered
selection, a real temporary Configure, reviewed files, Escape/cancellation, leaving
and reopening Projects, chooser handoff with Keep/Use and preserved keyboard focus,
and 600-pixel chooser/review layouts.

The drawing continuation passed 1,635 Python tests and 600 frontend tests, including
real pyserial pseudo-terminal transfers, cancellation, exclusive-lock release and
reconnect. Installed-wheel checks include the drawing assets, protocol module and
license notices. A browser run sent keyboard-drawn pixels through a synthetic UART
peer and displayed its checked prediction/time; clear/reopen, input/result identity,
focus and a 600-pixel layout were checked. This was synthetic protocol/UI validation;
no physical board was accessed for the drawing continuation.

The independent UART viewer follow-up passed 1,644 Python tests and 609 frontend
tests, plus quick and installed-package checks. Browser checks opened UART without
a chooser selection, sent two drawings over the same real pyserial pseudo-terminal,
and verified received prediction/time, retained connection after leaving/reopening,
explicit Close, empty-drawing feedback, preserved focus/scroll and a 600-pixel layout.
The UART peer was synthetic; this does not add physical-board runtime evidence.

See the [TinyML plan](tinyml-demo-creator-plan.md) for subsequent recipes and
framework work, and the [Project Creator guide](project-creator.md) for application,
configuration and build lifecycle details.
