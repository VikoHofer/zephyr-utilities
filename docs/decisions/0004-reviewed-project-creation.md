# ADR 0004: Reviewed creation and feature-owned build attempts

Status: accepted

## Context

P0/P1 provide local environment observations. The next useful workflow creates a small
ordinary Zephyr application with a selected target and installed SDK. Creation writes
source files; configuration executes application and toolchain code. Both need explicit
reviewed inputs and evidence that stays attached to the selected workspace. Device Tree
editing is not yet an implemented second consumer of build context.

## Decision

Implement P2 inside `features/projects/`, using the existing workspace, router, jobs and
browser shell. Add a minimal-C template and explicit GNU Zephyr SDK selection. Require
an existing resolved west workspace and module catalogue. Keep configuration packages,
profile editing, sysbuild and import out of this slice.

Use separate server-session plans for source creation and configure/build execution.
Show exact files or commands before the user applies a plan. Retain plans for 30 minutes,
consume them once, and capture workspace/environment selections before queueing work.
GET and preview requests do not create source/output directories or launch builds.
Reject changed directory identities, relevant environment evidence and application
sources instead of applying an earlier review to new inputs.

Publish reviewed source bytes with Linux directory-descriptor checks and atomic
no-replacement rename. Existing destinations, including empty directories, are never
regenerated or silently replaced. Keep generated sources independent of utilities
metadata; include direct CMake commands in the application README.

Each Configure or Build uses a new `<application>/build/<attempt-id>` directory. Build
includes configuration before compilation. Use explicit CMake/Ninja argument arrays,
selected Python/Zephyr/SDK/module/root inputs and an allowlisted environment. Preserve
partial output on failure/cancellation. No implicit pristine, flash, dependency download
or repository update is introduced. Separate output directories do not sandbox selected
application or toolchain code.

Persist feature-owned project/profile and attempt records with `schema_version: 1`
below `feature_data("projects") / "creator"`. Keep project definitions immutable and
atomically update running attempts to their recorded outcome. Preserve the legacy
memory layout and environment observation schema. Reject future formats without
replacing them; future migrations require tests. A running record with no active job
is presented as interrupted after restart, never automatically resumed.

Allow an optional relative `metadata.view` in shared jobs. The shell validates it and
routes the running-job badge within the registered feature, falling back to that
feature's entry path when absent or invalid. Creator jobs use `create`; this avoids
adding feature-specific branching or imports to the shared shell.

Capture requested and effective target/toolchain information separately. Keep commands,
source hashes, bounded logs, context and live artifact hashes attached to each attempt.
Unknown fields remain unknown. Metadata discovery, configuration, compilation and
hardware validation are distinct evidence levels. Artifact paths may later change;
these records do not archive a complete source/toolchain closure or immutable copies
of all outputs.

Keep this build context feature-owned. A shared `BuildContext`/artifact interface will
be extracted when the Device Tree reader becomes an implemented second consumer. This
extends [ADR 0003](0003-environment-observations.md)'s P1 scope without changing its
read-only analysis contract or coupling feature-private services.

## Consequences

- A newly created application is usable from the command line without Utilities.
- Review and execution cannot be redirected by a later shared workspace switch.
- Fresh output directories avoid hidden build-directory reuse and destructive pristine.
- Source directories with whitespace are rejected to match the selected Zephyr build
  system's support; low-level argument handling remains safe for quoted tool paths.
- Environment changes require renewed review; profile migration remains later work.
- Saved failures and interrupted attempts remain inspectable without claiming success.
- P3 must define cross-feature access and freshness revalidation before opening these
  artifact references in Device Tree Editor or Memory Explorer.

See the [Project Creator contract](../project-creator.md) for API shapes, supported scope,
storage details and validation limits.
