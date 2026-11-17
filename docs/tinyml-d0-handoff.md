# TinyML Demo Creator D0 handoff

Status: implemented, 2026-11-10. D0 qualifies sources and freezes data/handoff contracts.
The feature remains **planned**. Catalogue routes, creation tokens, adapters and the
TinyML chooser are not implemented; D1 is next.

## Delivered foundations

- Read-only developer source qualifier with bounded, no-follow reads and hash-only JSON.
- Version-1 descriptor, compatibility identity/evidence, preselection and optional
  Projects origin validators/fingerprints in `projects/demo_contracts.py`.
- Five small synthetic JSON fixtures: TFLM descriptor, unadmitted emlearn seed, source-ready,
  missing-module and unsupported-layout evidence. They contain no real model/source code.
- A real local TFLM source inventory, matching descriptor, sanitized environment inventory
  and exact-target acceptance matrix. These are observations, not Configure/Compile results.
- [ADR 0027](decisions/0027-tinyml-demo-creation-handoff.md), freezing Projects ownership,
  public handoff semantics, provenance and identity invalidation before D1 publishes data.
- A pinned, licensed [emlearn source decision](tinyml-d0-emlearn-seed.md) that requires
  neither model training nor conversion during demo creation.

No dependencies were installed, manifests changed, firmware sources edited, application
directories created or Configure/Compile/Flash jobs run. Existing saved TinyML data remain
inert. D0 does not restore any trainer, annotation editor or model-data exporter.

## Reproduce the source qualification

Use the existing Zephyr environment from the repository root:

```sh
python scripts/qualify_tinyml_demo_sources.py \
  --zephyr-base /path/to/zephyrproject/zephyr \
  --workspace /path/to/zephyrproject \
  --target qemu_x86/atom \
  --output /tmp/tinyml-d0-report.json
```

The output destination must be new and outside the Zephyr/module source directories;
omitting `--output` writes JSON to stdout. Optional `--sdk /path/to/sdk` reads version
metadata only. The command does not launch the app or register an API. Exit 0 means the
bounded source layout is understood; it may still report a missing module. Exit 2 means
source is absent/unsupported or the inspection/output operation failed. Do not treat
source qualification as build readiness.

The script recognizes this first sample's exact mixed C/C++ layout, configuration,
CMake static-initialization behavior, literal include dependencies and model array
identifier/length. Unsupported metadata containers, dynamic/alternate include directives,
links, special files, additions, oversized files and changing source identities produce
an unavailable/unsupported reason. It never evaluates imported CMake or training code.

This utility has its own bounded read-only observation limits: 128 sample files,
8 MiB per inspected file, 16 MiB total and bounded local manifest traversal. Omitted
notebooks/images may be hashed without being admitted for publication. The descriptor's
existing 1 MiB/file and 4 MiB total publication bounds remain separate and unchanged.

## Qualified local source and file plan

The [source inventory](tinyml-d0-source-inventory.json) records the local Zephyr
`fbab2f598d545ad043ce50a75fefa4550b053f0b`, version 4.4.2, with no tracked changes observed.
The Git probe does not claim to inventory untracked checkout files; the sample tree is
enumerated and hashed separately. No absolute host paths or model/source contents are
stored in these documentation artifacts.

The sine sample has 17 files totaling 629,913 bytes. The proposed runtime-only plan is:

| Action | Files / purpose |
| --- | --- |
| Include unchanged | `prj.conf` and ten `src/` C/C++ sources/headers, retaining their notices |
| Transform with retained original | `CMakeLists.txt`: replace the understood GLOB with an explicit reviewed source list, retain the C++ static-initialization compiler option and add the existing default-overlay activation through Projects |
| Omit with path/hash/reason | `README.rst`, `sample.yaml`, `train/README.md`, the training notebook and the architecture PNG |
| Add during future creation | Apache license text, origin/runtime integration guide, original CMake bytes, default target overlay and provenance; these generated files are reviewed in D2 |

Omitted upstream instructions remain linked by the generated integration guide. This is
an explicit runtime-only adaptation, not a complete clone or a silent README overwrite.
All individual hashes, byte sizes, include providers and configuration values appear in
the inventory. New source layouts need another adapter review.

The actual entrypoint is **`src/main.c`**, which calls the C-linkage bridge in
`main_functions.h` and the C++ inference implementation in `main_functions.cpp`. The
runtime also requires `assert.cpp`, constants, output handling and model sources.
The current main.c-specific post-check is therefore insufficient to bind the ML runtime
even though this particular sample still has a C entrypoint.

The model's textual `src/model.cpp` is 16,318 bytes, SHA-256
`f59fa15965b7e8c70253630983bbd54f6298b686c963f5490ff3cce6addcf924`.
Its embedded byte array declares 2,488 bytes and eight-byte alignment. D0 checks that
declaration/length and the TFLite identifier, not FlatBuffer semantics, supported operators,
tensor allocation or inference behavior. The descriptor's model hash identifies the
text file and must not be labeled as a binary model hash.

The [real descriptor example](tinyml-d0-tflm-descriptor.json) is accepted by the pure
contract validator. Its thirteen reviewed source/license entries total 42,065 bytes,
before the future generated guide/archive/overlay files. Root `LICENSE` is bound to
Apache-2.0 evidence; source notices are retained. It is metadata only, not a copy recipe
that can be submitted to an API or a creation review.

For reproducibility of this D0 example, its source capture fingerprint is SHA-256 over
canonical JSON of `{zephyr: report.zephyr, source: report.source,
manifest: report.dependencies.tflite_micro.manifest}` with sorted keys, ASCII JSON and
compact separators. The descriptor fingerprint additionally binds its requirements,
expected output and curated adapter identity. The two artifact tests detect a descriptor
that drifts from this recorded capture; future runtime capture must revalidate local bytes.

## Installed dependencies and target acceptance matrix

The selected optional manifest declares `tflite-micro` at
`optional/modules/lib/tflite-micro`, pinned to
`fcc760af130f3a595b5802cdebcc77461e54f382`. That declared checkout is absent. The qualifier
does not turn integration glue elsewhere in Zephyr into an installed framework module.
It checks only its explicit manifest/workspace scope; D1 must use Projects' full selected
module/root observations, including supported custom roots.

The emlearn firmware module is also absent from the currently analyzed active Projects
modules. The installed emlearn Python distribution supplies headers, not its repository's
Zephyr module metadata. The [seed note](tinyml-d0-emlearn-seed.md) distinguishes those
facts and records the verified upstream test/runtime/module/license hashes.

The [environment inventory](tinyml-d0-environment-inventory.json) was captured through the
existing Projects analyzer. SDK metadata 1.0.1 and the relevant Arm/x86 GNU components
and QEMU host binaries were discovered. No SDK was selected for a Configure operation;
the effective compiler remains unknown. Installed compiler paths are not execution proof.

| Demo / variant | Exact target for later acceptance | D0 evidence | Remaining gate |
| --- | --- | --- | --- |
| `tflm-sine/reference` | `qemu_x86/atom` | Target discovered; upstream board alias `qemu_x86` is declared for reference tests/integration | Install/qualify selected module separately, implement D2 adapter, Configure and Compile |
| `tflm-sine/reference` | `qemu_x86_64/atom` | Target discovered; upstream alias `qemu_x86_64` appears in platform declarations | Same checks; secondary acceptance after the first target |
| `emlearn-xor/loadable-trees` | `qemu_cortex_m3/ti_lm3s6965` | Target discovered; upstream walkthrough uses its board-level alias; static source fixture selected | Adapt/admit the pinned synthetic seed and installed module in D4, then Configure/Compile and explicitly check fixed outputs |
| Later optimized TFLM | `mps3/corstone300/fvp` | Exact target discovered; separate upstream CMSIS-NN test declaration; CMSIS-NN module observed active | Separate variant/adapter/module/configuration and emulator gates; not interchangeable with reference kernels |
| Physical-board investigation | `nrf52840dk/nrf52840` | Exact target discovered; no ML demo build result | Qualify an exact recipe/SDK/module combination and Compile; physical execution is another observation |
| Physical-board investigation | `nucleo_f446re/stm32f446xx` | Exact target discovered; no ML demo build result | Same separate exact-target Compile and hardware gates |

The qualifier compares exact strings against upstream declarations. Therefore an exact
target such as `qemu_x86/atom` is not literally an upstream `qemu_x86` alias match; that
false membership flag is neither incompatibility nor a successful target resolution.
Projects' selected-target resolver remains authoritative. No build/support badge follows
from this matrix, a board name, an active CMSIS module or a source-qualified result.

## Frozen D1 handoff

[ADR 0027](decisions/0027-tinyml-demo-creation-handoff.md) is the normative ownership,
identity and invalidation decision. The pure validators are foundations only: they do
not read source, issue tokens, load evidence from ProjectStore or authorize execution.

Future catalogue input is a captured `workspace` plus optional coherent
`selection: {board_id, target, sdk_id}`. The creation-handoff request body is:

```json
{
  "workspace": "/workspace",
  "demo_id": "tflm-sine",
  "variant_id": "reference",
  "source_fingerprint": "<current SHA-256 from Projects catalogue>",
  "environment_fingerprint": "<current Projects observation SHA-256>",
  "selection": {
    "board_id": "<Projects board ID>",
    "target": "qemu_x86/atom",
    "sdk_id": "<Projects SDK ID>"
  }
}
```

The versioned captured selection adds `schema_version: 1`. If no target/SDK has been
chosen, omit `selection` or use null; partial tuples and caller source paths, commands,
recipe objects and code are invalid. IDs/fingerprints must be resolved and revalidated
server-side; a structurally valid object does not establish compatibility.

D1 will expose only documented Projects routes:

- `POST /api/projects/demo-catalog` for bounded descriptors and requirement observations.
- `POST /api/projects/creation-handoffs` for a curated preselection token.
- `GET /api/projects/creation-handoffs/:id?workspace=...` for idempotent read resolution.

The returned opaque 32-hex ID selects `projects/create/<id>`. Retain at most 32 selections
for 30 minutes from submission; resolution does not extend retention, create files or
start a job. Restart loses these transient tokens, not completed Projects applications.
Changing workspace, environment, source/model, adapter, target/revision, SDK, ordered
module/root inputs or configuration invalidates bound review/check evidence.

Projects applies the selection through its existing form controller. Dirty forms offer
Keep current draft / Use demo selection; late responses must match workspace, navigation
generation and form revision. A token is not a creation plan. Fresh Preview/Create and
separate explicit Configure/Build continue using the existing Projects services/storage.
No new core provider, creator clone, TinyML project database or background scheduler is
needed for a frontend-only public API consumer.

## Exact implementation work left after D0

| Area | Necessary D1/D2 change |
| --- | --- |
| `projects/samples.py` | Keep classic C adapter restrictions; add a focused C++/module/runtime-only adapter path rather than merely another classic catalogue ID |
| `sample_compatibility.py` | Recognize admitted fixed-input ML requirements and preserve temporary Configure checks; unfamiliar IDs currently return unknown before Configure |
| `CreatorService` / `api.py` | Add catalogue/token handlers and explicit validated demo/variant fields; keep ordinary Preview/Create publication |
| `creation.py` / templates/provenance | Admit reviewed demo manifests and additive `demo_origin` without changing legacy project semantics; retain original transformed inputs |
| `build_workflow.py` | Bind all adapter entrypoint/model files rather than only `src/main.c`; reuse application-wide input hashes and existing attempts |
| `projects/web/index.js` and `creator-view.js` | Preserve/resolve `create/<id>` instead of normalizing it to Applications |
| `creator-state.js` | Apply guarded preselection through existing updates; invalidate stale checks/reviews and preserve unsaved user form work |
| TinyML frontend | D3 only, after D1/D2 make the first advertised demo usable |

No existing API, UI, copier, ProjectStore record reader/writer or build behavior was
changed in D0. The added contract module is not imported by the running feature registry.

## Verification

Focused synthetic tests cover future/malformed schemas, dependency/unknown states,
forged success structure, source/model/license/provenance binding, every identity
invalidation dimension, root/module precedence, safe relative paths and bounds, dynamic
include rejection, malformed YAML, changed/linked source and module evidence. The two
real metadata-artifact tests check file/model/license links and reproducible source identity.

All 32 focused D0 tests passed. `python scripts/check.py --quick` passed, and the complete
`python scripts/check.py` run passed 1,559 Python tests and 544 frontend behavior tests,
including existing HTTP integration cases with loopback access. Lint, syntax and
architecture checks passed. `python scripts/verify_package.py` passed, including import
of the installed pure contract module. Documentation links/JSON/fences, sanitized path
policy and `git diff --check` passed.

No browser behavior or routes changed, so D0 did not add a browser workflow to verify.
No real model inference, Configure, Compile, emulator run or physical hardware validation
is part of this D0 gate. The absence of the optional firmware modules remains a dependency
fact for D2/D4, not a failed or completed build check.
