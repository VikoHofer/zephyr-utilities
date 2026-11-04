# Existing builds and Device Tree inspection

The first P3 slice reads existing build outputs in the selected workspace. It does not
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
5. Use **Check for changes** to compare the live outputs with this observation. If they
   changed, **Re-inspect directory** creates a new observation of the current files.

The Device Tree view presents the generated `zephyr/zephyr.dts`, which Zephyr describes
as its final merged tree. It is an inspection artifact, not the source file to edit for
durable hardware changes. See Zephyr's [input and output file documentation](https://docs.zephyrproject.org/latest/build/dts/intro-input-output.html).
Sysbuild's `domains.yaml` supplies image names and build directories; a domain does not
necessarily represent a separate CPU. See [sysbuild definitions](https://docs.zephyrproject.org/latest/build/sysbuild/index.html).

## What the observation means

Projects and Device Tree share one workspace-bound observation ID. It records the
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

Source-file browsing, binding navigation, source-to-node attribution, the direct Memory
Explorer handoff and durable inspection history remain subsequent P3 work. Reviewable
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

An observation has `schema_version: 1`, `id`, `workspace`, `root_build_dir`, `created_at`,
`default_domain`, `domains`, root `artifacts` and `diagnostics`. Each domain has `name`,
`build_dir`, `context`, `artifacts`, `freshness` and `diagnostics`. These are transient
read contracts, not a persistent source/toolchain provenance format. See
[ADR 0005](decisions/0005-read-only-build-inspection.md).

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
