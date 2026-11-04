# Build Inspector: advanced outputs, sources and Memory handoff

P3 reads existing build outputs in the selected workspace. Inspection does not
configure, compile, flash, install tools, import an application into the creator, or
edit source files. Environment analysis is not required to inspect an existing build.

## Open a build

1. Open **Advanced → Build Inspector → Build outputs** and enter the existing build directory, or choose
   **Inspect build** on a saved Configure/Build attempt.
2. Choose **Open build**. For a sysbuild root, select the image/domain to inspect.
3. Review the recorded target, application and output availability. Technical metadata,
   input lists and text output previews are optional disclosures.
4. Choose **Inspect Device Tree** to inspect that image's resolved tree. Search node names,
   paths or compatible values, then select a node to read its properties. **Browse sources**
   opens recorded input files; **Find bindings** opens binding navigation for a node.
5. Choose **Open Memory Explorer** to review that image for RAM/ROM analysis. Opening
   the review only checks existing files. Choose **Generate reports** explicitly to
   run the selected build's footprint tool and save a Memory snapshot.
6. Use **Check for changes** to compare the live outputs with this observation. If they
   changed, **Re-inspect directory** creates a new observation of the current files.

The Device Tree view presents the generated `zephyr/zephyr.dts`, which Zephyr describes
as its final merged tree. It is an inspection artifact, not the source file to edit for
durable hardware changes. See Zephyr's [input and output file documentation](https://docs.zephyrproject.org/latest/build/dts/intro-input-output.html).
Sysbuild's `domains.yaml` supplies image names and build directories; a domain does not
necessarily represent a separate CPU. See [sysbuild definitions](https://docs.zephyrproject.org/latest/build/sysbuild/index.html).

## What the observation means

Build Inspector and the Memory handoff share one workspace-bound observation ID. It records the
selected root, each readable image, metadata-derived context, output paths and hashes.
Opening a different build creates a distinct observation, even for the same application.
Reopening a view rechecks the retained observation rather than silently assigning new
bytes to its old identity.

**Source freshness is unknown.** Matching output hashes mean that those output bytes
have not changed since inspection. They do not prove that current source files, overlays,
bindings, modules or tools match the build. The source lists are those reported by saved
metadata and have partial coverage. Inspection does not establish build success or
hardware operation; a partial or failed build may still have readable outputs.

Effective board/revision/qualifiers come from supported build metadata. A cached board
selection is shown separately when the effective target is unknown. Unknown values stay
unknown. Missing metadata produces a partial context rather than inferred compatibility.

Changed, removed or unreadable artifacts are marked explicitly. A changed metadata file
or requested text output prevents reading it under the earlier observation. Re-inspect
to view the current outputs. Inspection IDs are held in server memory for 30 minutes,
with at most 16 retained; restarting the app or expiration requires opening the build
again. No project, attempt, environment or memory snapshot format is migrated.

## Memory Explorer handoff

The handoff selects the exact inspected image and `zephyr/zephyr.elf`; it does not search
for a replacement, choose the default sysbuild image, or start a report job. The review
shows the effective target and image, with paths and SHA-256 under a closed disclosure.
Missing report tooling, changed outputs and expired inspections leave an actionable
message. **Choose another build** returns to Memory's independent discovery workflow.

An explicit report job captures its selection at submission. It rechecks the inspected
ELF and build context before running tools and before publishing the snapshot. It also
pins the configuration, map and resolved DTS evidence recorded in its snapshot. A queued
job keeps this captured identity if the workspace changes or the inspection cache expires.
Changed evidence prevents snapshot publication. Reports run against the existing ELF;
the handoff uses its retained archive as the generator input, so a concurrent rewrite of
the live ELF cannot change the analyzed bytes. It does not configure, build or flash firmware.

The existing version-1 Memory snapshot remains the durable result. Its archived ELF and
metadata hashes identify the captured evidence; a transient inspection ID is not a new
provenance guarantee. Source freshness remains unknown. Builds with a custom ELF path
that differs from the inspector's supported filename require Memory's manual workflow.

## Source and binding navigation

**Browse sources** lists Device Tree inputs, explicit overlays and configuration files
recorded in the build metadata, plus source filenames from the verified preprocessed
DTS line markers. Search by filename/path and choose **Open file** to read current text.
Literal source include references link to files in the current file's directory and
recorded include directories. Comments and quoted strings are excluded from reference
scanning. Conditional expressions and macros are not evaluated, and multiple matching
files remain separate candidates. The catalogue is not a complete dependency graph or
a mapping from every resolved property to its source location.

**Find bindings** uses the selected resolved node's compatible strings and searches the
recorded binding directories in the current workspace. It preserves duplicate and bus
variants as **Compatible candidate** entries. When a supported generated header comment
records a binding path for the node, that link is labeled **Recorded binding**. This is
evidence about a path recorded by the build, not a hash of the historical YAML contents.
Nodes using child bindings or inferred properties may have no recorded YAML path.

**Open binding** displays current YAML, direct property declarations and linked includes.
Scalar, list and named/filter include declarations are navigable. Duplicate include
basenames are shown as ambiguous choices. Includes are followed only when selected;
cycles do not trigger recursive loading. Property summaries are local to the opened file:
include filtering, inheritance, bus matching and schema validation are not performed.
These rules follow the distinction between compatible and bus matching described in
Zephyr's [binding syntax](https://docs.zephyrproject.org/latest/build/dts/bindings-syntax.html)
and [binding introduction](https://docs.zephyrproject.org/latest/build/dts/bindings-intro.html).

Every source view is **current workspace content**. Source freshness relative to the
build remains unknown. Opening a source list records current file hashes; a later edit
requires **Refresh source list** before opening that file under a new catalogue.
Rechecking the tree, changing workspace/image or leaving the feature clears loaded
source text and pending requests. The resolved node/query selection is retained by the
existing tree workflow. Technical file identity and diagnostics stay collapsed.

Reads are restricted to regular, non-symlink UTF-8 files inside the selected workspace.
External source paths remain visible with an unavailable reason, and external binding
roots produce diagnostics. Metadata does not grant access outside the workspace. Files
are opened through server-issued catalogue IDs, never arbitrary client paths. No compiler,
preprocessor, checkout Python module or pickle is executed. There are no write endpoints.
See [ADR 0007](decisions/0007-source-and-binding-navigation.md).

## Browse an application before configuration

Expand **Advanced details** on a saved workspace application and choose **Browse sources**,
or enter its directory in Build Inspector’s **Sources and Device Tree** view. Relative paths start at the selected workspace. The directory must be a
non-symlink descendant of that workspace with a readable `CMakeLists.txt`; this marker
is not executed or treated as proof of a valid Zephyr configuration. No creator profile,
environment analysis, build inspection, generated DTS or ELF is required.

The application view lists current `.dts`, `.dtsi`, `.overlay`, configuration fragments,
`Kconfig*`, `*_defconfig` and YAML under the application's `dts/bindings`. It searches
within the application and excludes version-control, virtual-environment, dependency
and build directories. Headers can be followed through literal include links. Arbitrary
project code and general YAML metadata are not part of this catalogue.

Include navigation considers the current file's directory, the application and its
`include` directory, plus conventional `<workspace>/zephyr/include` and
`<workspace>/zephyr/dts` locations. Binding includes search the application's
`dts/bindings` and conventional `<workspace>/zephyr/dts/bindings`. These are current
candidate locations, not discovered CMake search paths. Custom Zephyr locations, module
roots and profile-specific inputs require a configured build's recorded context in this
slice. Conditional preprocessing, effective overlay selection and target selection are
not inferred. There is no resolved tree or node-binding lookup in this mode.

Catalogues pin the application directory and `CMakeLists.txt` identity. Deletion,
replacement or marker changes require reopening; changed source text requires
**Refresh source list**. File reads still use opaque catalogue/file IDs and current
hashes. Leaving and reopening revalidates the application; workspace or mode changes
discard old source text. No source or generated output is written.

Application discovery and binding indexing exclude `.git`, `.hg`, `.svn`, `.west`, `.venv`, `venv`, `__pycache__`,
`node_modules`, `build`, `build-*` and `build_*` directories, plus directories containing
`CMakeCache.txt` or `build.ninja`. Limits and incomplete coverage are reported explicitly.
An application path that is itself a build directory is rejected. Explicit literal DTS
includes may still link to files in excluded directories, labeled as current referenced
content. Exclusions filter automatic discovery; they do not identify every possible
custom output location. See [ADR 0008](decisions/0008-application-source-browsing.md).

## Supported scope and limits

- Existing single-image Zephyr builds and up to 16 readable sysbuild domains inside the
  selected workspace. Select another workspace to inspect a build elsewhere.
- Bounded regular-file reads on Linux/POSIX; symlink traversal and paths escaping the
  selected workspace or sysbuild root are rejected.
- `CMakeCache.txt`, supported `build_info.yml` mappings and known output filenames.
  Missing optional outputs remain unavailable. Imported metadata is treated as data;
  no pickle, CMake, Python adapter or compiler is executed.
- A structural reader for generated DTS nodes, labels and raw property values. It does
  not resolve effective binding schemas, interpret register cells, verify driver enablement or validate
  hardware. Unsupported syntax yields a diagnostic and raw-text fallback.
- Output files are hashed with size/total-read limits. Text previews are bounded to
  8 MiB; YAML metadata to 2 MiB. Oversized or malformed data cannot become a validated
  build context.
- Source catalogues retain at most 1,000 references with a 16 MiB read budget and 2 MiB
  per document. They expire after 30 minutes, with at most 16 retained, and cannot outlive
  the original build inspection in build mode. Application catalogues have an independent
  lifetime and pin their application directory instead. Missing/blocked files remain explicit.
- Binding indexes search at most 64 recorded roots, 10,000 YAML files and a 16 MiB read
  budget, with 256 KiB per YAML file. Directory depth/count and YAML event/depth limits
  also apply. Unsupported tags, aliases, duplicate keys and invalid YAML yield diagnostics;
  explicitly opened unsupported YAML can still be read as text. No untrusted constructors run.
- Results and include lookup counts are bounded. Hitting a limit yields a diagnostic or
  an actionable catalogue limit error, never a claim of a complete schema.

Per-property source attribution, effective binding
resolution and durable inspection history remain later work. Reviewable
overlay and configuration editing remain P4. Existing Memory Explorer workflows continue
to operate independently.

## API

Build Inspector owns these read-only routes. The old `/api/projects/inspect`,
`/api/projects/inspections/:id`, `/api/projects/inspection-text` and `/api/devicetree/*`
read routes remain compatibility aliases. Old inspection/application deep links also
redirect to Inspector. The bare Device Tree Editor entry now describes its planned visual
workflow; it has no editing implementation.

All routes use the shared authenticated router. Requests carry the expected `workspace`
so a changed selection cannot redirect an earlier view's request.

| Method and route | Input | Result |
| --- | --- | --- |
| `POST /api/inspector/inspect` | `{workspace, build_dir}` | New observation of an existing build |
| `GET /api/inspector/inspections/:id` | `workspace` query parameter | Original context/hashes with rechecked availability |
| `POST /api/inspector/inspection-text` | `{workspace, inspection_id, domain, kind}` | Verified allowlisted text output: `{path, text, sha256}` |
| `POST /api/inspector/devicetree` | `{workspace, inspection_id, domain}` | Shared observation/domain, verified document and structural tree |
| `POST /api/inspector/sources` | `{workspace, inspection_id, domain}` | Bounded current-source catalogue with `catalog_id` and file references |
| `POST /api/inspector/bindings` | Selection fields plus `{catalog_id, node_path}` | Current compatible candidates and supported generated-header binding-path evidence |
| `POST /api/inspector/source-file` | Selection fields plus `{catalog_id, file_id}` | Verified current text/hash, literal include references and direct binding properties |
| `POST /api/inspector/application-sources` | `{workspace, application_path}` | Application source catalogue with `mode: "application"`, absolute `application_path`, opaque IDs and coverage diagnostics |
| `POST /api/inspector/application-source-file` | Application selection plus `{catalog_id, file_id}` | Verified current source or binding text, includes and direct properties; no build identity |
| `POST /api/memory/inspection` | `{workspace, inspection_id, domain}` | Read-only report readiness for the exact selected image and ELF |
| `POST /api/memory/jobs` | `{build_path, name?, notes?, selection?: {workspace, inspection_id, domain}}` | Explicit report job, bound to the inspection when `selection` is supplied |

An observation has `schema_version: 1`, `id`, `workspace`, `root_build_dir`, `created_at`,
`default_domain`, `domains`, root `artifacts` and `diagnostics`. Each domain has `name`,
`build_dir`, `context`, `artifacts`, `freshness` and `diagnostics`. These are transient
read contracts, not a persistent source/toolchain provenance format. See
[ADR 0005](decisions/0005-read-only-build-inspection.md).
The detached selection used by Memory jobs is described in
[ADR 0006](decisions/0006-inspected-image-memory-handoff.md). Existing Memory job callers
and legacy routes continue to work without `selection`.

## Verification

Python tests cover read-only capture, workspace isolation, HTTP guards, shared target
and domain identity, changed/missing outputs, malformed metadata, path confinement and
bounded reads. Frontend tests cover stale responses, domain selection, re-entry, workspace
changes and disposal. Full repository checks and installed-package asset verification
passed for this slice.

Browser verification used copied outputs from the retained local
`qemu_cortex_m3/ti_lm3s6965` build and a synthetic two-image sysbuild. The generated QEMU
DTS exposed 24 nodes and 119 properties; all eight copied outputs stayed byte-for-byte
unchanged. Checks covered attempt handoff, search/selection restoration, domain switching,
explicit re-inspection after a changed DTS, workspace changes and unsupported-syntax
fallback. No configure, compile or hardware operation was run for this work. Real
multi-image hardware operation and binding-aware interpretation were not validated.

The Memory handoff was also exercised with copies of that real ELF in a synthetic
two-image sysbuild, selecting the non-default `app` image. Explicit RAM/ROM generation
saved a version-1 snapshot with the selected target/domain, archived ELF input and
footprints of 4,106 and 9,356 bytes. All 25 fixture files, including the domain manifest,
remained unchanged. Browser checks covered collapsed details with keyboard access,
stale configuration rejection, manual selection, leaving/reopening the tools, workspace
changes and the same image in Device Tree. Automated cases also cover queue expiry,
directory replacement, changed/added evidence and a transient live-ELF rewrite during
analysis. Corrupted retained evidence is rejected before generation and publication.
These are analysis checks, not hardware or runtime memory measurements.

Source/binding verification used an isolated copy of the QEMU outputs and the selected
checkout's 3,725 binding YAML files. Build metadata/preprocessor paths were remapped to
the fixture, and synthetic overlay/config inputs exercised navigation only; they were
not claimed as historical inputs to the retained ELF. Browser checks covered source
lists, unavailable files, overlay-to-header navigation, the recorded UART binding and
its `uart-controller.yaml` → `base.yaml` include chain, keyboard access, source edits
requiring explicit refresh, image/workspace changes and leaving/reopening the feature.
After restoring the deliberate source-edit fixture, all 4,359 files matched the baseline;
generated build outputs stayed unchanged. No build tools or hardware operations ran.
Python/frontend checks cover scope/hash isolation, hostile paths/YAML, missing/changed
files, read limits, include ambiguity/cycles and late responses. Full repository and
installed-package checks passed.
