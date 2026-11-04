# ADR 0003: Versioned environment observations inside Projects

Status: accepted

## Context

Project creation needs a catalogue tied to the user's selected Zephyr workspace and
installed tools. Board metadata, module roots and SDK layouts change over time, while
the application must remain offline and avoid modifying firmware during inspection.
Device Tree editing will later need configured build context, which does not exist yet.

## Decision

Implement P0/P1 as feature-owned environment analysis inside `features/projects/`, using
the existing router, workspace, jobs, shell and storage namespace. The `projects` feature
is available for analysis; project creation remains planned. No additional server or
environment is introduced, and `core/` remains independent of feature implementations.

Persist `EnvironmentSnapshot` schema version 1 as immutable JSON observations beneath
`context.feature_data("projects") / "environments/snapshots"`. A versioned, atomically
updated pointer per selected workspace identifies its latest observation. Preserve the
existing memory storage layout and `--data-dir` semantics. Version 1 has no predecessor
requiring migration; readers reject unknown formats and writers preserve them. Future
format changes require explicit migration behaviour and tests.

Use bounded metadata adapters for hardware model v2 and installed SDK layouts. Preserve
source locations, declared versus discovered evidence, uncertain fields and diagnostics.
Known board/SoC extensions are distinct from duplicate base definitions. Identify SDKs by
installation path and fingerprint as well as version. Unknown metadata does not produce
guessed build-ready targets.

Run only local inspection probes through bounded, cancellable subprocesses. Capture a
job's workspace, normalized options and environment when it is submitted. Do not
configure applications, install tools, fetch dependencies or update a checkout during
analysis. Store only relevant normalized selections, not a full environment dump.

Fingerprint inputs and directory membership on explicit refresh. Reuse an unchanged
catalogue while retaining its original observation identity. GET requests serve saved
observations with an explicit not-rechecked freshness state; they do not start scans.
Tracked changes during discovery produce a stale result rather than a freshness claim.

The implemented contract is only `EnvironmentSnapshot`. Shared `BuildContext`,
`BuildProfile`, `ArtifactRef` and editor write contracts remain proposed. Extract shared
interfaces when an implemented second consumer requires them; do not import another
feature's private discovery or storage modules.

## Consequences

- New local metadata can appear after rescan without changing a bundled board list.
- Missing tools and unsupported definitions leave useful partial results with provenance.
- SDK presence and declared hardware support never imply configure/build/hardware success.
- Saved observations survive browser navigation and application restart; jobs retain the
  existing shared manager's in-process lifetime.
- There is no watcher or automatic upgrade mechanism. Users refresh after environment
  changes, and arbitrary CMake-derived roots remain unresolved until later configuration
  workflows or explicit additional roots are supplied.
- Observation storage retains evidence but does not archive a complete reproducible
  firmware environment. The [environment contract](../environment-analyser.md) defines
  the current API, supported scope and limits.
