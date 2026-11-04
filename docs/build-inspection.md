# Existing builds, Device Tree inspection and Memory handoff

P3 reads existing build outputs in the selected workspace. Inspection does not
configure, compile, flash, install tools, import an application into the creator, or
edit source files. Environment analysis is not required to inspect an existing build.

## Open a build

1. Open **Project Creator → Inspect builds** and enter the existing build directory, or choose
   **Inspect build** on a saved Configure/Build attempt.
2. Choose **Open build**. For a sysbuild root, select the image/domain to inspect.
3. Review the recorded target, application and output availability. Technical metadata,
   input lists and text output previews are optional disclosures.
4. Choose **Open Device Tree** to inspect that image's resolved tree. Search node names,
   paths or compatible values, then select a node to read its properties.
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

Projects, Device Tree and the Memory handoff share one workspace-bound observation ID. It records the
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

## Supported scope and limits

- Existing single-image Zephyr builds and up to 16 readable sysbuild domains inside the
  selected workspace. Select another workspace to inspect a build elsewhere.
- Bounded regular-file reads on Linux/POSIX; symlink traversal and paths escaping the
  selected workspace or sysbuild root are rejected.
- `CMakeCache.txt`, supported `build_info.yml` mappings and known output filenames.
  Missing optional outputs remain unavailable. Imported metadata is treated as data;
  no pickle, CMake, Python adapter or compiler is executed.
- A structural reader for generated DTS nodes, labels and raw property values. It does
  not resolve bindings, interpret register cells, verify driver enablement or validate
  hardware. Unsupported syntax yields a diagnostic and raw-text fallback.
- Output files are hashed with size/total-read limits. Text previews are bounded to
  8 MiB; YAML metadata to 2 MiB. Oversized or malformed data cannot become a validated
  build context.

Source-file browsing, binding navigation, source-to-node attribution and durable
inspection history remain subsequent P3 work. Reviewable
overlay and configuration editing remain P4. Existing Memory Explorer workflows continue
to operate independently.

## API

All routes use the shared authenticated router. Requests carry the expected `workspace`
so a changed selection cannot redirect an earlier view's request.

| Method and route | Input | Result |
| --- | --- | --- |
| `POST /api/projects/inspect` | `{workspace, build_dir}` | New observation of an existing build |
| `GET /api/projects/inspections/:id` | `workspace` query parameter | Original context/hashes with rechecked availability |
| `POST /api/projects/inspection-text` | `{workspace, inspection_id, domain, kind}` | Verified allowlisted text output: `{path, text, sha256}` |
| `POST /api/devicetree/inspect` | `{workspace, inspection_id, domain}` | Shared observation/domain, verified document and structural tree |
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
