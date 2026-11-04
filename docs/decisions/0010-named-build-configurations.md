# ADR 0010: Named build configurations for saved applications

Status: accepted

## Context

Applications were tied to the board target and SDK captured during creation. Users need
to build the same source for another target or newly analyzed environment without
regenerating the application or replacing its original history. This is the first P4
slice, following the creator priority in [ADR 0009](0009-project-navigation-and-inspector.md).

## Decision

Add named, immutable target/SDK configurations inside Project Creator. Reuse its existing
environment selection validation and execution guard. An explicit Save configuration
action writes feature metadata only. Configure/Build still require a reviewed command
plan, recheck environment and source identity, and allocate fresh output directories.
Fragment selection and visual Device Tree editing remain separate later slices.

Store optional schema-version-1 records under
`feature_data("projects") / "creator/configurations/<id>.json`, scoped to a project and
workspace. Preserve immutable project records and their original profile. The original
profile remains the implicit default for existing clients; no migration is required.
Reject future formats without replacing them. Bound record sizes, catalogue scans and
per-application configuration counts using the existing store conventions.

Build previews accept an optional configuration ID. Resolve it only within the captured
application/workspace, then copy the complete profile and configuration identity/name
into the server-held plan. Execute and recheck that captured profile, and retain it in
the attempt. Old attempts without these added fields stay readable.

Keep selection, target and SDK visible beside normal application actions. Clear review
plans when selections or workspace change; ignore obsolete asynchronous responses.
Detailed commands and environment data remain optional disclosures. Saving does not
rewrite application sources, generated README commands, or old attempts.

## Consequences

- One ordinary application can be built using several explicit target/SDK choices.
- Environment upgrades require analysis and a newly saved choice; existing choices
  never silently follow a different toolchain or target.
- Reopening metadata does not execute application code. Compatibility remains subject
  to an explicit configure/build, and hardware validation remains separate.
- Configuration rename/removal, portable export, ordered fragment selection and editor
  changes are deferred; none is implied by the new configuration record.
