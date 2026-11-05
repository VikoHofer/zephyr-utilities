# ADR 0021: Explicit editor configuration through Projects

Status: accepted for D5.

## Context

The editor owns exact overlay drafts and guarded Save. Projects owns saved build
configurations, selected tool environments, reviewed command plans and managed outputs.
An overlay saved successfully is not necessarily used by the selected configuration.
An appended temporary overlay cannot faithfully model removal of declarations, and
moving application files can change relative includes or arbitrary CMake behavior.

## Decision

Application composition supplies an optional `ProjectValidationProvider` through
`context.project_validation`. Its public protocol lives in `core/project_validation.py`;
Projects implements review, submission, result, cancellation and navigation handoff.
The editor never imports Projects workflows or writes Projects records.

D5 supports **Save then Configure** for registered single-image configurations. There
is no unsaved staging adapter in this stage. Review binds the canonical saved overlay's
path and exact UTF-8 bytes/hash to the selected project/configuration, target, fragment
recipe, environment, tools and input observations. Configure uses the existing source
layout and a fresh Projects-owned output directory. It does not append a candidate,
temporarily replace live files or silently alter overlay selection.

Only the explicit reviewed Configure action executes project tooling. Opening, editing,
Save, review and navigation to Projects do not configure or compile. Compilation is a
separate reviewed Projects action. Source/output separation does not sandbox CMake or
toolchain code. Normal Projects limits, environment rechecks, resource ownership and
shared-job cancellation apply.

The provider records application and selected source/tool evidence before execution and
checks it afterward. Captured configuration metadata must identify overlay participation.
Unknown dependency coverage remains inconclusive; successful Configure is not a claim
of complete source freshness or hardware validity. Dynamic reads outside the bounded
observed inputs are not proven. Later source edits do not rewrite historical attempts.

Diagnostics are text data. Only a verified location in the exact current candidate can
link to editable source. Generated outputs and other paths remain read-only evidence.
Results carry candidate and input identity; changing the draft invalidates its current
result display. Workspace switching cannot redirect or cancel an accepted job. Leaving
the feature disposes polling while the shared job continues.

Project context revalidation ignores changes confined to historical attempt evidence.
New Configure history must not invalidate an otherwise unchanged editor draft. Project,
configuration, environment and source identity remain guarded; captured history is still
presented as the original observation rather than silently refreshed.

## Consequences

The editor has one explicit validation entry point without a second build subsystem.
Existing relative includes and declaration removals retain their actual semantics.
Users must Save first, and unregistered directories cannot invent a toolchain selection.
Session validation reviews/results are bounded and do not resume automatically after
restart; durable logs and configured outputs belong to Projects attempts. No persistence
schema migration or automatic build is introduced.

Faithful staging, richer source adapters, binding forms and pin/module configuration
remain later work. The [D5 handoff](../device-tree-editor-d5-handoff.md) records the
delivered limits and verification.
