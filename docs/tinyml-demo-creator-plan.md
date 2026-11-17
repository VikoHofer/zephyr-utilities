# TinyML Demo Creator implementation plan

Status: R0/D0 complete, 2026-11-10; MNIST runtime, Projects catalogue/preselection and
TinyML chooser delivered 2026-11-15. The former training/annotation implementation is
removed. Broader descriptor-driven framework selection and sine/XOR adapters remain
planned; the D1–D4 work below now records those remaining recipes as well as the
delivered MNIST slice.

The subsequent user request also delivers the focused [drawing/UART view](tinyml-mnist-demo.md#draw-and-infer-over-uart).
It uses the existing MNIST normalization/upload protocol and the shared job manager;
its port selection and explicit Open/Send/Close controls are independent of demo,
board and SDK selection. It does not restore dataset collection, training or automatic flashing. Its runtime
dependency/device boundary is recorded in [ADR 0028](decisions/0028-tinyml-drawing-uart.md).

The existing MNIST demonstration supplied the development reference for the first
integration. The user's follow-up requires an independent Utilities-owned template:
the runtime source is now bundled under `projects/mnist_template/`, while the trained
model, reference assets and pinned framework are explicitly installed into managed
storage. New creation and builds never read the demonstration or another generated
app, and CMake uses the requested application name. See
[ADR 0029](decisions/0029-independent-mnist-template.md).

The [runtime adapter](tinyml-mnist-demo.md) remains available through Projects' existing
sample catalogue and publisher, with multiple exact-target build configurations.
TinyML selects MNIST, board and SDK through the Projects-owned transient handoff.
MNIST adapter version 2 (`mnist-runtime-v2`) retains the existing outer provenance
schemas 1/3 rather than adopting the D0 sine/XOR descriptor schema. Old creation plans
require a fresh preview; existing application records and build history remain readable.
Classic sample restrictions remain unchanged. Sine and XOR remain later recipes.

## Product direction

Make it easy to start a buildable ML demo for a selected Zephyr target by reusing
the existing Project Creator. TinyML supplies a focused framework/demo chooser;
Projects remains the owner of creation, application/configuration records and builds.

The intended flow is:

```text
Select target and SDK
  -> Choose framework and demo
  -> Review requirements and local availability
  -> Open the existing Project Creator with the demo selected
  -> Review files and create application
  -> Explicitly Configure or Build
  -> Open application, output or Memory Explorer through existing controls
```

A user choosing a framework sees demos with reviewed recipes for that framework.
Framework selection is a catalogue filter, not an arbitrary runtime substitution inside
another demo. Start with pretrained inference on fixed inputs so the first examples
need no external sensors or model-training environment. Hardware-dependent examples
follow only with their own verified requirements.

This plan replaces Dataset → Train → Evaluate → Prepare firmware and its T0–T5 sequence.
Dataset import, annotation, model training/conversion, generated model-data export,
device capture, flashing, cloud services and framework benchmarking are outside this
product slice. Upstream tools continue to provide those workflows where appropriate.

## Current state and reuse

The application remains one offline English browser UI started by `zephyr-utils`.
Use the selected Zephyr environment, shared workspace, authenticated router and job queue.
Do not add a launcher, server, port, virtual environment or persistent job system.

| Responsibility | Existing owner and reuse point |
| --- | --- |
| Environment, exact boards/revisions, SDKs and modules | Projects environment observations and `CreatorService.current()` |
| Target/SDK validation | `features/projects/creation.py:select_profile` |
| Name, destination and file review | `creation.preview_project`, `CreatorService.preview` and the existing creation form |
| Guarded publication and registration | `creation.apply_project`/`publish_files`, `CreatorService.create`, ProjectStore |
| Local sample provenance and license evidence | `projects/samples.py`, extended with focused demo adapters |
| Explicit compatibility check | `projects/sample_compatibility.py` and `CreatorService.sample_check` |
| Configuration/build commands and attempts | `builds.command_recipe`, `BuildWorkflow`, existing Projects APIs |
| Input identity, including C++ and model headers | `projects/application_inputs.py:source_inputs` plus recipe/module evidence |
| Application management, reconnect, cleanup and inspection | Existing Projects, Inspector and Memory Explorer workflows |

Before MNIST, the sample copier supported Hello World, Blinky and Button through a
three-ID catalogue, restricted UTF-8 text/suffixes, simple local C-source CMake rules,
controlled headers and sample metadata. Its checker and build entrypoint assumptions
also needed explicit MNIST support. The separate reviewed C++/module/CPU-inference
adapter now provides that support, including actual entrypoint and model/source identity.

Keep the classic sample behavior and its existing guards separate from ML adapters.
Do not globally relax CMake, includes, sample metadata, file bounds or source checks.

## First demo catalogue

MNIST is the implemented integration. The remaining candidates retain explicit
admission gates; their listing is not a claim of current build support.

| Demo | Framework/variant | Initial role and requirements |
| --- | --- | --- |
| MNIST digit recognition | TFLM Float32 reference inference | Bundled source template with separately installed model/images/framework; serial shell, no sensors; select exact board/SDK and explicitly check/configure/build |
| Sine Regression | TensorFlow Lite Micro reference kernels | Later sensor-free recipe; clone the runtime portion of the local Zephyr `samples/modules/tflite-micro/hello_world` example, retaining its pretrained textual model and source licenses |
| XOR Classifier | emlearn decision trees | Second framework; adapt the D0-selected hand-constructed upstream XOR test fixture with fixed predictions, plus an installed emlearn module; no training/generation during creation |
| Sine Regression, optimized | TFLM with CMSIS-NN | Separate later variant with exact Cortex-M/module/configuration requirements and its own build gate |
| Gesture Recognition | TFLM Magic Wand | Later sensor-dependent recipe; require a verified accelerometer/route or the documented emulated input setup |

The upstream TFLM sine example uses a pretrained sine model and offers reference and
CMSIS-NN variants. Its documentation demonstrates `qemu_x86`; the selected local
`sample.yaml` declares `qemu_x86`/`qemu_x86_64` for reference kernels and
`mps3/corstone300/fvp` for CMSIS-NN. Use the selected checkout's declarations at runtime,
not hardcoded promises based on the current docs.
[Zephyr TFLM sine sample](https://docs.zephyrproject.org/latest/samples/modules/tflite-micro/hello_world/README.html).

Zephyr documents emlearn as an external module. Its upstream Zephyr walkthrough shows
an XOR random forest, generated model header and `qemu_cortex_m3` build. There is no
emlearn sample in the currently selected Zephyr sample tree, so the second recipe needs
its own source/license/fixture admission rather than an invented upstream sample path.
The creator consumes fixed inference sources, not the walkthrough's training process.
[Zephyr emlearn module](https://docs.zephyrproject.org/latest/develop/manifest/external/emlearn.html),
[emlearn Zephyr walkthrough](https://emlearn.readthedocs.io/en/latest/getting_started_zephyr.html).

Magic Wand recognizes gestures from accelerometer input and documents a LiteX/Renode
setup. It is not an immediately portable motion/still demo for arbitrary boards.
[Zephyr Magic Wand sample](https://docs.zephyrproject.org/latest/samples/modules/tflite-micro/magic_wand/README.html).

Local inspection on 2026-11-10 found Zephyr 4.4.2. Its optional submanifest declares
TFLM, but the declared module checkout is absent; the conventional emlearn checkout is
also absent. Integration glue, an optional manifest entry or an installed Python package
does not establish an installed firmware module. These observations are planning evidence
only and must be refreshed against the user's selected workspace during implementation.

## User interface

Keep feature ID `tinyml`, displayed as **TinyML Demo Creator**. The first available view
will be **Demos**. Framework filters and demo cards show the task, input/output behavior,
required hardware, source/version/license, model origin and dependency state.

1. Use Projects' public environment/creator observations for exact target and installed
   SDK choices. Share the small target/SDK selector if extracting it has a real second use;
   do not duplicate environment discovery or the whole creator form. No separate TinyML
   board database is needed. If analysis is missing/stale, explain it and link to Projects
   Environment; do not silently analyze, configure or build while opening the page.
2. Filter by framework, demo and CPU/sensor requirements. Keep unavailable choices visible
   with specific reasons. Display the exact requested target/revision/core and toolchain.
3. **Use in Project Creator** creates a bounded preselection handoff and opens the classic
   Create application form with demo, variant and target/SDK selected. Project name and
   destination remain editable. If that form contains unsaved work, show the proposed
   selection and allow retaining or replacing the draft rather than silently discarding it.
4. Projects performs its ordinary file/command review and explicit Create action. The
   review adds demo dependencies, copied/omitted source lists, model provenance, variant
   configuration and a clear description of expected output.
5. Creation offers existing Configure/Build controls and Applications navigation. Results
   live in the existing attempt/output views. Creation never implicitly compiles or flashes.

Do not fork the creator controller, review dialog, configuration editor, build UI or
application history inside TinyML. Staying inside TinyML with an embedded creation form
is a later option only if a shared public creator component is extracted first.

Use the shared palette/tokens and `mount(root, context)` lifecycle. All imported labels
and paths use text-safe DOM APIs. Actions need nearby busy/result/error feedback; ordinary
selection and polling preserve focus/scroll. Deliberate handoff navigation uses the shell
and `context.reveal` once. Dispose polling/listeners without cancelling accepted jobs.
Late responses must match view lifetime, workspace and selection identity. Verify 600-pixel
widths, keyboard access, empty/missing-module/error states, leaving and reopening.

## Compatibility and dependency evidence

Track separate observations rather than a single unsupported “works on this board” flag:

| Evidence | Meaning |
| --- | --- |
| Source ready | Installed sample/seed and its complete runtime recipe are understood, hashed and licensed |
| Missing dependency / incompatible | A specific module, source file, SDK, configuration or hardware requirement is missing or contradicted |
| Not checked | Source prerequisites permit an explicit check, but Configure/Compile evidence is absent |
| Configuration checked | The reviewed temporary copy configured for this exact identity; compilation and hardware remain untested |
| Build passed | The created application compiled successfully for the captured identity; hardware behavior remains untested |

Keep upstream declared test platforms separately labeled as declarations. A CPU family,
recognized board brand, copied overlay or successful Configure is not proof of compilation.
Static footprint is not runtime peak memory. Retain failed checks and actionable logs.

A compatibility key includes environment observation/fingerprint, requested/effective
target and revision, SDK/toolchain identity, demo/adapter version, selected variant,
runtime file/model hashes, module roots/revisions and relevant configuration/overlays.
Changing any of these invalidates the corresponding check and creation review. Refuse
unsafe or changed inputs instead of falling back to another demo, target or module.

Resolve required framework modules through captured installed-module evidence and
explicit module arguments. MNIST uses its independently installed framework under
Projects' `tinyml-runtime/mnist-v1` data store, referenced by generated CMake through
`MNIST_TFLM_DIR` and `ZEPHYR_EXTRA_MODULES`. Preserve custom board/SoC/DTS/binding roots
and the selected Zephyr environment. Never infer module paths from a conventional
name alone. Missing dependencies show the exact requirement. Opening, selecting and
creating a demo do not run `pip install`, `west update`, download models, rewrite a
manifest or edit another application. Explicit offline setup is available through
`zephyr-utils --install-mnist-assets PACK --tflm-source FRAMEWORK` before launch;
it copies and fingerprints selected local inputs once, with no automatic fallback.

## Public handoff and ownership

The first implementation needs documented Projects HTTP endpoints, not private Python
imports or cross-feature browser-state mutation. TinyML's frontend uses the shared API
client and shell navigation. Projects owns the catalogue, recipes and handoff tokens.
No TinyML backend service or new `core` provider is needed solely to forward requests.
If a genuine backend caller later needs creation, extract a focused public protocol then.

Proposed additions under the existing authenticated Projects router:

| Endpoint | Proposed responsibility |
| --- | --- |
| `POST /api/projects/demo-catalog` | Workspace-bound local demo/variant descriptors and requirements for the selected target/SDK |
| `POST /api/projects/creation-handoffs` | Validate a curated demo/source fingerprint and optional exact target/SDK preselection; return an opaque expiring ID and shell route |
| `GET /api/projects/creation-handoffs/:id` | Resolve/revalidate the preselection for the captured workspace; reading never creates files or submits a build |

Continue using existing `/api/projects/creator`, `/sample-check`, `/preview`, `/create`,
configuration and `/build-preview`/`/build` APIs. Extend their supported demo recipe and
variant fields deliberately; validate fields, do not accept arbitrary source paths,
commands, CMake snippets, Python code or recipe objects from the browser.

Use route `projects/create/<handoff-id>`, preserving normal `projects/create`. Both
`projects/web/index.js` and `creator-view.js` preserve creation-token subviews, including
direct navigation/reload. The shell carries slash subviews. The token selects inputs
only; a separate fresh review authorizes publication.

Follow existing preview bounds: initially 30-minute expiry and at most 32 handoffs per
server. Tokens capture workspace, environment/source/recipe fingerprints and preselection;
they are unavailable after restart. Resolving the same valid handoff is idempotent and
does not republish source. Expired/stale tokens retain the current form and show a way
back to Demos. Source/target/module changes require another compatibility check/review.

Completed applications, configurations and attempts use Projects storage exclusively.
Add a small versioned demo-origin descriptor to new application provenance: demo/variant,
adapter/source/module/model identities, license references and transformations. Read older
Projects records without that optional descriptor. TinyML may retain transient filters;
do not add a parallel project catalogue, “experiment” record, training-model registry
or build queue. The managed MNIST runtime asset store holds fixed installation inputs,
not experiments, drawings or inference results.

## Runtime recipe adapters

Store focused adapters with Projects, for example `demo_catalog.py`, `demo_handoffs.py`
and `sample_adapters/tflm.py`/`emlearn.py`. Names are implementation suggestions; preserve
clear ownership rather than creating a plugin platform.

A declarative descriptor has schema version, stable demo ID, framework/variant ID,
adapter version, trusted source locator, runtime inclusion/exclusion rules, model/input
provenance, required module/configuration/hardware facts and expected console behavior.
Unknown descriptor versions and unrecognized source layouts remain unavailable.

For MNIST, source comes from the bundled Utilities template and model/framework
inputs come from the managed installation. Its original demonstration is historical
attribution only. The following admission guidance applies to later upstream recipes:

- Admit the understood C++ headers/sources and module includes through an explicit adapter.
  Preserve necessary compiler configuration, including upstream static-initialization
  behavior. Convert understood source lists to a deterministic reviewed file plan;
  never evaluate arbitrary CMake just to discover copy inputs.
- The upstream tree includes training notebooks and image assets, while current Projects
  publication handles bounded textual file plans. Start with an explicit **runtime-only
  copy** manifest, listing copied, transformed and omitted paths. Link the original
  training documentation and retain original bytes/provenance for transformed build files.
  Do not present a partial tree as a complete upstream clone or silently extend binary writes.
- Preserve sample configuration and board overlays, and add the existing editable
  target-named default overlay without replacing sample defaults. Show predicted inclusion
  separately from Configure evidence. Reference kernels and CMSIS-NN use distinct recipes.
- Replace C-only post-check assumptions with the adapter's actual entrypoints and
  runtime/model input hashes. Reuse existing application-wide input hashing and build
  output guards; do not create an alternative build executor.

For emlearn, adapt/admit the D0-selected versioned upstream synthetic XOR fixture with
fixed expected predictions, exact source hashes and complete MIT attribution. Its model
is hand-constructed, so generation is not applicable. Resolve runtime headers through the installed module. No
estimator training or generated user model conversion happens during creation. Real
model files, captured user data, build outputs and downloaded packages do not enter Git.

Keep present copier limits (128 files, 1 MiB/file, 4 MiB total) until admission proves
the selected runtime recipe fits. If a later demo requires more, introduce explicit
adapter bounds with tests rather than raising every classic sample's limits. The existing
application-input observer has separate 4 MiB/file and 32 MiB total bounds; copying,
observation and publication limits must agree for each admitted recipe.

## Delivery sequence

### R0: retire the previous TinyML implementation — complete

Remove dataset/annotation/preprocessing/model-export/backend-probe modules, browser assets,
dedicated tests, optional training dependency declarations and developer spike command.
Remove the TinyML-only shared terminal callback extension and its tests. Remove the old
training plan, handoffs and persistence/process ADRs. Keep only the planned replacement
manifest and update current documentation/catalogue descriptions.

Existing `.features/tinyml` data are retained inert, without startup reads, migrations or
garbage collection. Original CSVs, user projects, downloaded files and installed packages
are untouched. New demo projects will not reinterpret these old records. No shared
environment package uninstall is part of removing the application feature.

Gate: old APIs/assets are unavailable, app startup registers no TinyML service, ordinary
Projects and other tools still work, base package installs without ML extras, checks and
installed-package verification pass. This cleanup does not implement the new creator.

Removal verification on 2026-11-10 passed quick checks, the full suite (1,527 Python tests
and 544 frontend tests), installed-package verification and documentation link checks.
A regression covers unavailable old API/assets and untouched legacy saved data. Browser
verification shows the replacement as planned and the normal Project Creator form still
available. These checks validate retirement and platform behavior, not a new ML demo build.

### D0: qualify demo sources and freeze the reuse contract — complete

Delivered in the [D0 handoff](tinyml-d0-handoff.md), with the frozen boundary in
[ADR 0027](decisions/0027-tinyml-demo-creation-handoff.md). Real source/descriptor and
sanitized environment inventories are linked there. TFLM source qualification passed;
its optional firmware module is absent, so no Configure/Compile result is claimed.
The second-framework seed decision selects a pinned hand-constructed emlearn XOR fixture
rather than a training/generation workflow. Adapting/admitting that seed remains D4.

Inventory the selected checkout's samples, module declarations/installed roots, SDKs and
test platforms. Capture one TFLM runtime-only source/omission plan and its license/model
evidence. Resolve all current copier/checker/C++ entrypoint limitations listed above.
Decide the tiny emlearn seed/source and provenance before promising a second framework.
Write an ADR for the Projects API/handoff/provenance extension before publishing records.

Gate: versioned descriptor examples and fixtures for available/missing-module/unsupported
layout states; exact files and changes for the first adapter; acceptance target matrix;
documented handoff fields and invalidation rules. Do not install dependencies implicitly.

### D1: Projects catalogue and creation handoff — MNIST delivered

The MNIST endpoint/token/navigation implementation is complete and uses the existing
sample adapter. The descriptor-driven catalogue work below remains for later recipes.

Add curated descriptors, target/module requirement observations, expiring preselection
tokens and the documented public endpoints. Extend Projects navigation/form handling for
`create/<id>`. Keep regular templates and sample choices working unchanged.

Gate: a synthetic curated demo preselects the existing form and goes through its existing
preview/create flow, producing one normal Projects application record. Cover stale tokens,
workspace changes, unsaved form replacement, reload/re-entry and dependency changes.

### D2: real TFLM reference-kernel recipe — MNIST delivered, sine planned

MNIST has historical source/publication, temporary Configure and exact-board Compile
evidence in the [integration guide](tinyml-mnist-demo.md). Independent-template and
migration validation is recorded separately there. The following sine-specific work remains.

Implement the C++/module/runtime-only adapter and CPU-inference compatibility check.
Preserve source/license/model identities, default-overlay behavior, selected SDK/modules,
explicit Configure/Build and existing cancellation/error/publication recovery.

Gate: create from an installed qualified TFLM source, revalidate source before publication,
Configure and Compile on a declared exact acceptance target (initial candidate `qemu_x86`).
Show actual build evidence and expected output instructions. A missing module or unsupported
layout remains visibly unavailable. Do not advertise hardware target support from this gate.

### D3: TinyML demo/framework chooser — MNIST delivered, additional recipes planned

The MNIST chooser with exact target/SDK selection, public preselection and dirty-form
review is complete. Additional framework filters/cards depend on further admitted recipes.

Implement the small TinyML Demos view, framework/target filters, requirements details and
Use in Project Creator handoff. Mark the feature available only when its first advertised
demo completes D1/D2. Expose unavailable framework cards honestly; do not hide missing setup.

Gate: real browser selection → existing form → reviewed application → explicit successful
build, with one shared application/attempt history. Verify narrow width, keyboard/focus,
scrolled feedback, late responses, workspace switches, leave/re-entry and packaged assets.

### D4: emlearn and a physical-board acceptance pass

Add the independently qualified emlearn synthetic inference seed and installed-module
adapter. Test framework switching without mixing recipe inputs. Select at least one exact
physical-board target discovered locally; nRF52840 DK and Nucleo F446RE are investigation
candidates, not assumed successes. Record RAM/ROM/build errors honestly.

Gate: both framework choices create normal Projects applications and compile using the
captured source/module/SDK matrix. A physical-board build proves compilation only;
hardware output stays unverified unless separately run and recorded. Sensor-dependent
Magic Wand and optimized/NPU variants are separate later acceptance work.

## Validation and release evidence

| Area | Required checks |
| --- | --- |
| Classic workflow regressions | Minimal C/C++, Hello World, Blinky, Button, default overlays, imports/configurations and existing build/cleanup behavior |
| Source admission | C++ layouts, headers/modules, runtime inclusion/omission, licenses, changed model/source hashes, symlinks/traversal and unsupported metadata |
| Compatibility | Missing module/SDK, exact variants/cores/revisions, unsupported CMake, explicit Configure versus successful Compile, no sensor assumptions |
| Handoff/review | Expiry, duplicate resolution, stale workspace/environment/source/SDK, unsaved form preservation, no writes from catalogue or token resolution |
| Publication/provenance | Destination collisions, cancellation, changed source, one Projects record, optional-origin backward compatibility and existing recovery |
| Browser | Shared authenticated client, keyboard/Escape/focus, desktop/600 px, late replies, reload, workspace switching and leave/re-entry |
| Package | Bundled source/configuration/licenses; trained assets excluded from Git/wheel; no training extra, model-generation import or network/CDN runtime requirement |

Run `python scripts/check.py --quick`, `python scripts/check.py` (with loopback available)
and `python scripts/verify_package.py`. Use small synthetic fixtures in Git. Record real
builds separately with exact Zephyr commit, demo/source/model/module hashes, target,
SDK/compiler, commands, exit status and output identities. Historical MNIST results
precede the independent-template replacement; fresh validation must identify the new
adapter and managed framework. See the integration guide for current results.

The first usable slice is one sensor-free ML demo created and built through the classic
workflow. Multiple frameworks and targets become available only as their own gates pass.
This keeps the replacement focused on preparing useful firmware projects.
