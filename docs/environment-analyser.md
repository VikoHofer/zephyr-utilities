# Environment analyser

Project Creator's Environment view implements P0/P1 of the
[Project Creator master plan](project-creator-masterplan.md). It discovers the selected
local workspace, Zephyr checkout, board and chip metadata, and installed SDKs. The separate
[Create application workflow](project-creator.md) implements P2 reviewed generation and
explicit configure/build actions. Device Tree editing remains planned.

## Using the view

1. Start `zephyr-utils` in the existing Zephyr environment.
2. Select the workspace with the shared workspace switcher, then open **Project Creator**.
   The direct route is `/#/projects/environment`.
3. Click **Analyze workspace**. The scan runs through the shared job manager and shows
   progress, logs and a cancellation control.
4. Search boards by name or target, narrow the results by chip/family and vendor, and
   select a board to inspect its declared targets, revisions and source locations.
5. Inspect installed SDKs and diagnostics. Expand the environment details for tool
   versions, west projects, module declarations and discovery roots. **Use this board**
   carries a discovered board into Create application; select its exact target and SDK there.

Opening the view reads the last saved observation; it does not start a scan. A saved
observation is labeled as not rechecked. **Analyze again** checks input fingerprints and
can reuse an unchanged hardware catalogue. **Full rescan** rebuilds the catalogue even
when those fingerprints match. Neither action downloads tools or updates repositories.

Use **Additional discovery paths** for an explicit Zephyr checkout, board roots, chip
roots or SDK installation paths. Enter one path per line. A board root contains a
`boards/` directory; a chip root contains a `soc/` directory. Relative paths are resolved
against the selected workspace. These form choices are kept separately for each workspace
during the browser session, so changing workspace does not silently reuse its predecessor's
absolute paths.

For persistent board-root management, use **Custom boards**. Available registered roots
are automatically merged into analysis and build rechecks. Registration changes or a
missing/replaced registered directory mark saved observations stale until a new scan.
Manual discovery paths remain independent of registration. See [custom boards](custom-boards.md).

Leaving the view does not cancel its server job. Reopening it restores the selected
workspace's saved observation and latest retained job. Switching workspace changes future
analysis requests; a submitted job keeps its captured workspace, options and environment.

## Discovery scope

| Area | Current behaviour |
| --- | --- |
| Workspace | Find an ancestor `.west`, inspect its configuration, and resolve the manifest through the selected Python environment's installed west library |
| Zephyr | Record the selected checkout's path, declared version, Git commit/dirty state when available, and SDK reference from `SDK_VERSION` |
| Tools | Query local Python, west, CMake, Ninja, dtc and Git versions; missing tools produce diagnostics |
| Projects and modules | Distinguish active west projects from Zephyr modules, record available revisions and module metadata, and report missing projects |
| Hardware roots | Combine explicit and registered custom roots, supported environment overrides, roots in the selected directory, module build settings and the Zephyr checkout |
| Boards and chips | Read hardware model v2 metadata, board/SoC extensions, CPU clusters, nested variants, revisions and platform architecture hints |
| SDKs | Inspect installation metadata, installed executable GNU/LLVM compiler components, recognized host tools and CMake package files |

The selected workspace's `.venv/bin/python` is preferred when present; otherwise the
utilities interpreter is used. Its path is preserved rather than resolving the virtual
environment symlink into a system interpreter. Probes use argument arrays, explicit
working directories, time/output bounds and cancellation. They do not invoke `west
update`, configure an application, compile firmware or execute SDK setup scripts.

Manifest imports are resolved locally through west. When that is unavailable or fails,
the analyser can show bounded direct manifest entries, marked unresolved. It does not
fetch missing imports or assume unresolved project activity. Module settings can supply
board, SoC, DTS and snippet roots; this release catalogues boards and chips. Recording
DTS/snippet roots does not provide binding, shield or snippet browsing yet.

Supported environment selections include `ZEPHYR_BASE`, `BOARD_ROOT`, `SOC_ROOT`,
`ZEPHYR_MODULES` and `EXTRA_ZEPHYR_MODULES`. Module replacement and additional-module
settings have distinct meanings. Application CMake and configuration packages are not
evaluated, so roots added dynamically by them can only be included through explicit
discovery paths in this release.

## Hardware support and interpretation

The metadata adapter is `hwm-v2-metadata/2`. Version 2 shares pure SoC/qualifier parsing
with board observation and applies the qualifier limit to bare CPU-cluster expansion
as well as variants. The adapter version invalidates analysis caches; saved observation
schemas remain unchanged. Synthetic tests cover shapes used by Zephyr
3.7 and 4.4, including a backported extension case, unknown metadata and conflicting
definitions. The current local Zephyr checkout is an integration reference. This is
metadata-shape coverage, not a claim that every release, vendor fork or target builds.
Linux is the tested host; other host platforms are not yet claimed as verified.

Board extensions preserve their contributing origins and add declared variants to an
existing base board. Multiple base definitions are a conflict. Unknown fields, malformed
metadata, missing SoCs and unsupported layouts produce diagnostics rather than invented
targets. Identifiable unsupported boards remain visible without executable target choices.
Legacy hardware model metadata without `board.yml` is unsupported.

Targets retain SoC/cluster/variant qualifiers and declared revisions. Custom revision
logic is reported but not executed. Architecture hints come from matching platform
metadata, not from guessing a chip family. A chip can appear without a usable board
definition; selecting that chip is a catalogue filter, not creation of a new board port.

Board and SoC facts use `evidence: "declared"`. SDK records use `evidence: "discovered"`.
No result is configure-, build-, runner- or hardware-validated. A board's presence does
not establish driver readiness, peripheral operation, pin availability or application
memory capacity.

## Installed SDK inventory

SDKs are identified by their canonical installation path and a metadata/component
fingerprint, not version alone. Equal-version installations remain separate. The scanner
recognizes older root-level GNU toolchains and newer `gnu/` layouts, LLVM components,
and recognized host-tool layouts. It does not run the compilers.

Search inputs include explicit installation paths, `ZEPHYR_SDK_INSTALL_DIR`, supported
CMake SDK directory variables, CMake search prefixes, the user's CMake SDK package
registry and bounded conventional locations such as `/opt`, `/usr/local`, the user's
home/local directories, and the workspace and its parent. Search is shallow and bounded;
an installation elsewhere may require an explicit path. A registry entry is read as a
path, never evaluated as CMake code.

`available_toolchains` describes the SDK's package metadata. `toolchains` lists compiler
components found as executable files in supported layouts. `uninstalled_toolchains`
shows advertised GNU components that were not found. Incomplete setup, absent compiler
components and unrecognized metadata are reported separately.

The checkout's `SDK_VERSION` is a reference value, not proof of an installed or selected
SDK. The analysis-only `selection` retains requested environment overrides and leaves
`effective_compiler: null`, `status: "not-configured"`. Each SDK has
`compatibility: "unknown"`. SDK installation remains outside this release. P2 selects an installed GNU SDK explicitly
in a separate project profile; it does not rewrite this analysis observation.

## Freshness and limits

There is no filesystem watcher. Reopen reads saved evidence; an explicit analysis
rechecks the environment. Fingerprints include relevant input contents and directory
names, so additions, deletions and uncommitted metadata changes can invalidate a cached
catalogue without changing Git HEAD. Tool identities, module/root choices and SDK
component metadata also participate. SDK executable fingerprints use file identity and
stat information rather than claiming a complete binary archive.

Input enumeration and fingerprinting run around hardware discovery. If tracked inputs
change during the scan, the observation is marked `stale` and a rescan is required.
`partial` means discovery produced warning/error diagnostics; `complete` means discovery
finished without those diagnostics, not that a build has been verified. Catalogue
changes list added, removed and changed boards, chips and SDK records relative to the
previous observation.

Traversal, file sizes, parsed structures, record counts, subprocess duration and output
are bounded. Hardware metadata is limited to 2 MiB per file, traversal depth 24 and
bounded directory/file counts; fingerprinting covers at most 30,000 inputs. Tool probes
default to 12 seconds and 2 MiB of output. SDK directory listings and installation
candidates are also bounded. Reaching a limit produces diagnostics. Select narrower
roots or explicit SDK paths when a scan is incomplete.

## HTTP contract

All routes use the shared authenticated router, session token, origin validation and
JSON request handling. POST bodies must be JSON objects, including `{}` for cancellation.

| Method and path | Request | Result |
| --- | --- | --- |
| `GET /api/projects/environment` | None | `{workspace, snapshot, job}` for the selected workspace; snapshot/job can be null; does not scan |
| `POST /api/projects/analyze` | Optional discovery settings below | HTTP 202 shared job record with `feature: "projects"` |
| `GET /api/jobs/:id` | None | Shared job status, metadata and logs; a completed analysis has `result.snapshot` |
| `POST /api/jobs/:id/cancel` | `{}` | Shared cancellation request; previous saved evidence is retained |

Analysis settings:

```json
{
  "zephyr_base": "/workspace/zephyr",
  "board_roots": ["/workspace/custom-hardware"],
  "soc_roots": [],
  "sdk_paths": ["/opt/zephyr-sdk-example"],
  "force": false
}
```

All fields are optional. Path lists accept at most sixteen entries each; individual
paths accept at most 4,096 characters. Unknown options, wrong types and invalid path
values are rejected. `force` bypasses catalogue reuse; it never authorizes a build,
installation or workspace update. An environment resource key prevents concurrent scans
of the same selected workspace.

The GET response marks the returned snapshot `freshness: "saved-observation"` without
rewriting its stored contents. A scan result has observation/check timing; a cache hit
retains the prior observation ID and `created_at`, sets `cache.hit: true`, and adds
`checked_at`. Cache-hit timing is a job result, not a new persistent observation.

## EnvironmentSnapshot version 1

The implemented persistent contract is feature-owned `EnvironmentSnapshot` version 1.
It is JSON data, not a shared configured-build contract.

| Field | Meaning |
| --- | --- |
| `schema_version`, `adapter_version`, `id`, `created_at` | Format, analyser adapter, immutable observation identity and observation time |
| `workspace` | Selected path, west top directory, manifest path and resolution state |
| `zephyr` | Checkout path, declared version, commit/dirty state and recommended SDK reference when known |
| `request` | Normalized discovery options, excluding the force flag |
| `tools`, `modules`, `roots` | Tool observations; project/module identities and state; ordered roots with origins |
| `boards`, `socs` | Declared hardware records with source locations, status and evidence |
| `sdks`, `selection` | Installed candidates/component observations and requested environment selections |
| `diagnostics` | `{severity, code, message, path?}` records; imported values remain text |
| `capabilities` | Adapter support and explicit false values for project creation, configuration, build/hardware/runner validation |
| `fingerprint`, `cache_key`, `cache`, `freshness` | Input identity and observation/reuse state |
| `status`, `changes` | Complete/partial/stale discovery and differences from the previous observation |

Board records include `name`, optional display/vendor information, SoC names, qualifiers,
revisions/default, declared targets, directories, origins, source files and optional
platform hints. SDK records include their path, version, installed components, origins,
source files, fingerprint and unknown compatibility. Missing information stays null,
empty or explicitly unsupported; it is never converted to a successful validation.

## Storage and future integration

Observations live below `context.feature_data("projects") / "environments"`:

```text
<memory-data-dir>/.features/projects/environments/
  snapshots/<observation-id>.json
  workspaces/<workspace-path-hash>.json
```

Snapshot files are immutable. An atomic, versioned workspace pointer selects the latest
saved observation. Cancelled/failed jobs do not replace the last successful save. A
cache hit reuses that observation instead of rewriting it. This storage preserves the
existing `--data-dir` meaning and Memory Explorer snapshot layout.

Version 1 is the first environment format, so there is no older environment migration.
Unknown versions are rejected while preserving existing files, including on forced
rescan. Future format changes need an explicit migration with regression coverage.
The analyser stores normalized observations and provenance; it does not archive entire
checkouts, SDK binaries or enough source inputs to reproduce a firmware build.

P2 now uses a feature-owned project profile and attempt context for reviewed creation
and explicit builds. Those records do not upgrade an environment observation into build
evidence. Shared `BuildContext`/artifact interfaces and Device Tree handoff remain
proposals in the master plan. Add a narrow public contract when the second implemented
consumer needs it; the analyser does not import Memory Explorer's private discovery code.
See [ADR 0003](decisions/0003-environment-observations.md),
[ADR 0004](decisions/0004-reviewed-project-creation.md) and the
[creator contract](project-creator.md).
