# Project Creator

Project Creator implements P2 and the first P4 configuration slice of the [master plan](project-creator-masterplan.md): a
minimal C application in the existing workspace, with an exact board target and an
explicit installed GNU Zephyr SDK. Creation, configuration and compilation are separate
reviewed actions. Saved applications can have several named target/SDK configurations.
Device Tree editing, existing-project import and configuration fragment selection remain planned.

## Sidebar workflow

Project Creator opens **Applications** for managing saved apps and running reviewed
Configure/Build operations. Choose **Create application** for a new destination, or
**Environment** for board, chip and SDK discovery. These are separate sidebar subviews.
Advanced inspection is available separately under **Advanced → Build Inspector**.

## Create an application

1. Start `zephyr-utils` in the existing Zephyr environment and select the workspace.
2. Open **Project Creator → Environment**, then **Analyze workspace**. Resolve missing
   tools or unsupported module metadata before creation. Select a board and use
   **Use this board**, or open **Create application** directly.
3. Enter the application name and destination. The default is
   `<workspace>/applications/<name>`. The destination must be new, even if an existing
   directory is empty.
4. Choose the board, its exact declared target and an installed GNU SDK. Target choices
   retain revision, SoC, CPU and variant qualifiers. SDK selection is explicit; the
   checkout's recommended version does not select an installation for you.
5. Click **Preview application**. Inspect every file, the destination, selected tools
   and command recipe. Changing a form field discards the review.
6. Click **Create application** to apply those files. The environment and destination
   are rechecked before publication. Creation does not configure or compile anything.
7. Open **Applications** in the sidebar, then choose **Configure** or **Build**. Review the exact
   commands, environment and new output directory, then click **Run configure** or
   **Run build**.

Source previews, commands, logs and technical evidence start collapsed. Open their
dropdowns when you want to inspect them; operation status, errors and action buttons
remain visible. Open attempt details stay open while job updates arrive.

Configure generates a fresh build system and captures its resolved context. Build first
configures another fresh output directory, then compiles. It does not reuse a preceding
Configure directory. Each operation has a shared job, logs and cancellation. Leaving the
view does not cancel the job, and switching workspace cannot redirect its captured
inputs. Reopening reads saved projects and retained jobs without starting an operation.

The workspace application list checks saved folder locations when opened, when the
window regains focus, and when you choose **Refresh list**. Deleted or moved application
folders are hidden from the list. Saved profiles and build history are retained, so
restoring a folder to its original location makes its entry reappear. An unreadable
location stays listed with a diagnostic rather than being treated as deleted.

A successful build establishes compilation for that attempt. It does not establish
hardware operation, peripheral wiring, runtime memory limits or runner compatibility.
There is no flash action in this release.

Expand **Advanced details** and choose **Browse sources** on an application to open its current Device Tree files,
configuration fragments and local bindings before configuring or building it. Build
Inspector also accepts a workspace application directory directly, without creating a saved
Project Creator profile. This is read-only source navigation, not project import or
effective configuration discovery. Deleted or replaced application folders invalidate
an open source catalogue. See the [source browsing limits](build-inspection.md#browse-an-application-before-configuration).

## Save and choose build configurations

In **Applications**, each app has a **Build configuration** selector. **Original** uses
the target and SDK chosen when the app was created. Choose **Add configuration** to
give another board target and installed SDK a name, then **Save configuration**.
Board and chip search uses the selected workspace's latest environment catalogue.
Saving records this selection in Utilities; it does not change application files,
rewrite the generated README, configure or compile.

Choose a saved configuration, then **Configure** or **Build** and review the operation.
The review and attempt history show which configuration, exact target and SDK were used.
Changing the selection clears a pending review. Each configuration uses fresh output
directories through the same build workflow; attempts retain their captured selection.

Configurations are saved choices, not moving references to the newest SDK. After a
Zephyr/toolchain change, run **Analyze workspace** and save a new configuration from
the refreshed catalogue. The build rechecks the captured environment and rejects stale
choices rather than silently retargeting them. Discovery alone does not establish target
and SDK compatibility; an explicit configure/build provides that evidence.

Names contain 1–64 characters without control characters and must be unique within
the application, ignoring case. **Original** is reserved. Up to 32 additional
configurations can be saved per application. Renaming/removing configurations, selecting
ordered Kconfig/overlay fragments and importing existing applications remain later work.

## Inspect existing build outputs

Choose **Inspect build** on an attempt, or open **Advanced → Build Inspector → Build outputs** and enter an existing
build directory. Inspection is read-only and does not require a creator profile or an
environment rescan. Select a sysbuild image/domain when present, then use **Inspect Device
Tree** to inspect its resolved nodes and properties. Technical details remain optional.
See the [build inspection guide](build-inspection.md) for freshness checks, supported
metadata, API routes and the remaining P3 scope. Overlay editing remains planned.

## Generated files and independent use

The `minimal-c` template, version 1, produces:

```text
<application>/
  CMakeLists.txt
  prj.conf
  src/main.c
  .gitignore
  README.md
```

The source has no board-specific LED or sensor requirement. It does not require an
application overlay. The board and SDK appear in build arguments, not in the portable
C source. Zephyr defines the usual application files and permits direct CMake builds;
see [Zephyr application development](https://docs.zephyrproject.org/latest/develop/application/index.html).

The generated README contains the selected direct CMake/Ninja commands. These work
without the utilities' stored profile and use `build/manual`; choose another output
directory after changing target or toolchain. The UI allocates a separate
`<application>/build/<attempt-id>` for each reviewed attempt and never silently removes
an existing output directory.

The preview and README retain absolute local tool paths. They are a recipe for this
workspace, not a portable SDK bundle or a dependency lockfile. Moving a project or
changing its environment may require adjusting the independent command-line recipe.
Profile migration and portable recipe export are later work.

## Supported scope

| Area | Current support |
| --- | --- |
| Host | Linux, with atomic no-replacement directory publication supported by the filesystem |
| Workspace | Existing local west workspace with a resolved manifest and a nonempty resolved module list |
| Template | One minimal C application; one image |
| Target | Exact discovered hardware-model-v2 board target, including declared revisions and qualifiers |
| Toolchain | Explicit discovered Zephyr SDK with installed GNU compiler components |
| Local tools | Selected Python environment, CMake and Ninja; discovered dtc when available |
| Configuration | Named exact-target/SDK selections per saved application; Zephyr's automatic configuration/overlay discovery |
| Output | Fresh directory per attempt; no automatic pristine, flashing, installation or workspace update |

Application names start with a letter and contain at most 64 letters, digits,
underscores or hyphens. Destinations must be inside the selected workspace and outside
Zephyr, utilities, SDK, dependency, hidden and tool directories. Parent traversal,
control characters, semicolons and symlinked application paths are rejected. Application
paths containing whitespace are also rejected: the selected Zephyr build system does
not support them, as documented in
[creating a Zephyr application](https://docs.zephyrproject.org/latest/develop/application/index.html#creating-an-application-by-hand).
Tool arguments and displayed shell commands still use appropriate argument handling and
quoting; rejection is a Zephyr capability limit, not permission to split paths into words.

The recipe passes explicit target, SDK, Zephyr package, Python, Ninja, modules and
hardware roots. It uses an allowlisted execution environment rather than inheriting
compiler or configuration overrides, and disables automatic `ZephyrBuildConfiguration`
and `ZephyrAppConfiguration` package discovery. It does not run `west` build defaults.
Application CMake and the selected Zephyr/toolchain code do run during an explicit
Configure or Build; separate output directories are not a sandbox for that code.

The creator does not supply explicit `CONF_FILE` or `DTC_OVERLAY_FILE` lists. Zephyr
retains its automatic discovery rules, including application inputs added outside the
UI. Configured input paths are captured where the selected build system reports them.
Graphical configuration editing, selectable fragments/shields/snippets, generic sample
copying, sysbuild, LLVM selection and freestanding workspaces remain outside P2.

## Preview and change protection

Creation and build previews are server-session plans, valid for 30 minutes and consumed
once. They contain the exact recipe and relevant source/destination identity. The UI
clears plans after edits, workspace changes, errors and remounts. A restarted server or
an expired/used plan requires a fresh preview. Previews create no application or build
directories and execute no configure/build process.

Before an explicit action, the service rechecks the selected execution environment:
Zephyr identity, hardware/manifest/module metadata, tools, discovery choices and the
selected SDK. Unrelated SDK search-directory membership is not an execution input.
Changes to relevant evidence reject the operation for renewed analysis and review.
This guard is narrower than the full catalogue inventory cache; it does not archive or
hash every source file in every tool and dependency repository.

Creation checks directory ancestry and stages the reviewed bytes before atomic
publication that cannot replace an existing destination. If a directory appears or is
redirected after preview, the operation fails without clobbering it. Failure cleanup
removes only unchanged files owned by that operation. If source creation succeeds but
profile persistence fails, the error identifies the created application path; its
independent source files remain there.

Build preview hashes bounded application inputs outside `build/` and `.git/`, records
source/build-parent directory identity and chooses an unused output path. These checks
run again before execution. An edited source, replaced directory, symlink or newly
occupied output requires another preview. Failure and cancellation retain partial build
output. Applications changed during execution are marked stale in the recorded context.

## HTTP contract

Routes use the shared authenticated router, origin checks and JSON body validation.
Every mutation includes the expected `workspace` path; a shared workspace switch before
request handling is rejected. Commands and files are retained server-side in the plan,
not accepted back from an edited browser payload.

| Method and path | Request | Result |
| --- | --- | --- |
| `GET /api/projects/creator` | None | `{workspace, snapshot, projects, jobs, diagnostics}`; saved projects include their configurations and attempts; no execution |
| `POST /api/projects/preview` | `{workspace, name, destination?, board_id, target, sdk_id}` | `{plan}` with exact files, profile, recipe and destination identity |
| `POST /api/projects/create` | `{workspace, plan_id}` | HTTP 202 shared create job; completed result contains `project` |
| `POST /api/projects/configurations` | `{workspace, project_id, name, board_id, target, sdk_id}` | `{configuration}`; saves metadata only |
| `POST /api/projects/build-preview` | `{workspace, project_id, operation, configuration_id?}` | `{plan}` with selected configuration/profile, operation, exact fresh `build_dir`, recipe and source identities |
| `POST /api/projects/build` | `{workspace, build_plan_id}` | HTTP 202 shared job; successful result contains `attempt` |
| `GET /api/jobs/:id` | None | Shared progress, logs and outcome |
| `POST /api/jobs/:id/cancel` | `{}` | Shared cancellation request; partial output is retained |

`operation` is `configure` or `build`. Board and SDK IDs refer to the saved environment
catalogue; a target must belong to its selected board. Unknown fields and invalid
selections are rejected. Failure/cancellation attempts remain readable through the
creator GET even when the shared job has been evicted from in-memory history.

An omitted, null or empty `configuration_id` selects the original project profile,
preserving earlier clients. A supplied configuration must belong to this application
and workspace. The server captures its complete profile in the build plan and attempt;
execution uses that captured profile. Saved configurations are immutable.

A recipe contains `cwd`, `environment`, `configure` and `build` argument arrays, plus
`display_configure` and `display_build` shell renderings. The execution uses argument
arrays and explicit working directory/environment, without a shell. Display commands
include the same environment selection and can be inspected or used independently.

## Version 1 records and evidence

Project, configuration and attempt records are feature-owned JSON under
`context.feature_data("projects") / "creator"`:

```text
<memory-data-dir>/.features/projects/creator/
  projects/<project-id>.json
  configurations/<configuration-id>.json
  attempts/<attempt-id>.json
```

| Record | Main fields |
| --- | --- |
| Project | `schema_version`, `id`, `workspace`, `name`, `path`, `profile`, template identity/version, generated file hashes, directory identity, creation time |
| Profile | Exact requested target/board, selected SDK and tools, Zephyr identity, modules/roots, environment observation identity and execution guard, analysis choices |
| Configuration | `schema_version`, `id`, `project_id`, `workspace`, `name`, `created_at`, complete `profile` |
| Attempt | `schema_version`, `id`, `project_id`, `workspace`, selected `configuration` and `profile`, operation/status, recipe, source hashes, output path, times, context, artifacts, diagnostics, error and bounded log |
| Captured context | Requested/effective target, revision/qualifiers, compiler/SDK, source/build paths, automatic configuration and DTS input paths when reported, freshness/coverage |
| Artifact reference | Kind, live path, SHA-256, size, availability and generated evidence |

The store preserves immutable project definitions and atomically updates an attempt
from running to its terminal outcome. Unknown schema versions are rejected without
replacing existing records. These first-version formats have no predecessor to migrate;
future versions require migration tests. A previously running attempt with no active
server job appears as `interrupted` after restart; it is not automatically resumed.

The optional configuration directory is additive. Existing project records retain their
original profile without migration. Earlier attempts without configuration/profile
fields stay readable; their saved commands and context remain the evidence for that run.
See [ADR 0010](decisions/0010-named-build-configurations.md).

Saved attempt logs retain the final 500 entries, with each entry bounded to 2,000
characters. Processes have a 30-minute total attempt limit and a 16 MiB output limit.
Application fingerprinting is bounded to 2,000 files, 32 MiB total, 4 MiB per file and
16 directory levels. Project/attempt records are limited to 4 MiB, with catalogue limits
of 500 projects and 2,000 attempts. Reaching limits produces an error or diagnostic.

Artifact references are live-path observations with hashes, not immutable copies.
Missing outputs and partial context remain explicit. Source freshness covers the
application inputs inspected by this release, not the entire transitive dependency,
binding and toolchain closure. Opening a saved attempt does not rehash every artifact.
Future Device Tree handoff must recheck evidence and add a focused public interface;
P2 does not import another feature's private implementation or introduce a shared
`BuildContext` API. See [ADR 0004](decisions/0004-reviewed-project-creation.md).

## Validation scope

The named-configuration slice was checked with 322 Python tests and 124 frontend tests,
plus quick checks and installed-wheel asset verification. Browser checks covered saving,
selection/review invalidation, draft restoration, workspace isolation, invalid names,
keyboard focus and the narrow layout. A named configuration compiled the minimal app
for `qemu_cortex_m3/ti_lm3s6965` with SDK 1.0.1 in an isolated temporary fixture. Its
original project record and all five source files stayed unchanged, and the attempt
retained the chosen profile. Other targets and a second SDK selection are covered by
synthetic tests; only one installed SDK was available for the real build.

For the original P2 workflow:

Local integration on 2026-11-04 configured and compiled the generated minimal application
for `qemu_cortex_m3/ti_lm3s6965` using Zephyr SDK 1.0.1 and its ARM GNU compiler.
The displayed configure and build commands also completed independently with zero exit
status and produced an ELF. The input files were unchanged by these runs. This is a
compile check; no physical hardware operation was validated.

The complete `python scripts/check.py` run passed 211 Python tests and 41 frontend
tests. Quick checks and `python scripts/verify_package.py` also passed. Browser checks
covered preview, creation, configuration, cancellation and a full build of a scratch
application in the workspace. Leaving/reopening the view and switching workspace during
an active build preserved the captured operation. No browser console errors were reported.

Synthetic tests cover reviewed creation, collisions, traversal and symlink changes,
changed source/environment inputs, independent SDK selections, command recipes,
configuration/build failures, process cancellation, persistence and UI lifecycle.
Only one installed SDK was available for local integration; a second installed-SDK
selection is covered by fixtures rather than a second real toolchain run. Broader target,
SDK and host coverage must be established separately.
