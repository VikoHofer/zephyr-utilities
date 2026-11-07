# ADR 0025: New-overlay drafts, publication and Projects inclusion

Status: accepted and implemented, 2026-11-07. This completes the missing-overlay
extension proposed in [ADR 0024](0024-chip-pin-workspace.md). Verification and remaining
product gates are recorded in the [new-overlay handoff](../device-tree-editor-new-overlay-handoff.md).

## Context

A project without an application overlay must permit the same pin editing, source
history and recovery workflow as a project with an existing overlay. An absent
destination is different from an existing empty file. Creating a file also does not
establish that a saved Projects configuration will use it. The editor must retain
these distinctions through first Save, concurrent changes and restart recovery.

The read-only `ProjectContextProvider` remains read-only. The editor must not import
Projects internals, modify its record storage directly or rewrite application CMake
to include an overlay. File publication and configuration inclusion cannot be one
atomic filesystem operation.

## Decision

Projects proposes one application-relative overlay destination for the exact selected
target when its current selection supports a missing destination. The editor validates
the path and exposes a virtual document with `availability: absent`, `can_create: true`
and `baseline_exists: false`. Ordinary missing documents do not grant creation authority.
Opening automatically creates the canonical empty draft and its private recovery record;
it creates neither an application file nor missing application directories. The chip
workspace uses that draft through the normal guarded discovery and activation path.

Draft wire schema 1 adds `baseline_exists` and `needs_creation`. An absent draft remains
dirty even with empty source. Source commands, Undo and Redo retain their existing
canonical history. First Save changes the baseline to an existing file while retaining
history; Undo after Save changes draft text and never deletes the published file.

Durable draft records use schema 2 and explicitly retain `baseline_exists`, the absent
destination observation, existing directory identities and missing directory suffix.
Schema-1 records remain readable as existing-file baselines. Recovery validates the
record and current context, then requires an explicit comparison and restore. An
appeared empty file cannot silently become the original absent baseline. Unknown or
malformed records are preserved; opening does not migrate or replay them.
Opening may reuse a schema-2 empty recovery record at revision zero when it has never
been edited, has no history, and its destination observation and selection guards still
match. The record receives the fresh context without consuming another recovery slot.
Edited records, including source undone back to empty, are never reused this way and
remain available for explicit restoration.

Save plans use `operation: create` for a new destination and never treat an empty new
file as a no-op. On Linux, the publisher stages the candidate, durably records intent,
revalidates the absence and directory chain, then uses atomic no-replacement rename.
If parent directories are missing, a private subtree containing the candidate is
staged and the first missing component is published with the same no-replacement
operation. A competing leaf or parent is preserved. Symbolic links are not followed.
The publisher verifies staged directory contents before and after publication through
fresh directory descriptors, verifies the resulting file, and retains evidence after
interference. This avoids treating a reused directory enumeration offset as empty
contents while preserving unexpected-entry checks.
Creation journals use schema 2 with `operation: create`; existing schema-1 replacement
journals remain readable. Interrupted or uncertain publication is never automatically
replayed or rolled back. Existing-file Save retains ADR 0020's exchange protocol.

The composition root supplies the narrow public `ProjectOverlayProvider` through
`ApplicationContext.project_overlays`. Its two operations are:

| Operation | Authority and result |
| --- | --- |
| `review(selection, relative_path, creating=False)` | Revalidates the captured project/configuration and source selection; returns a private durable inclusion plan or a matching pending plan. It changes no firmware source or selected configuration. |
| `register(registration, workspace, path, sha256)` | Verifies the saved file and reviewed selection, then includes only that path through a Projects-owned compare-and-swap update; returns an inclusion receipt and fresh selection. |

The provider preserves the selected configuration's existing inputs and Kconfig choice.
Automatic overlay discovery remains automatic when it already predicts this file;
otherwise the provider adds the path to the ordered overlay selection, switching `auto`
to `append` when necessary. It updates only Original or the selected named configuration
and retains unrelated configurations. A legacy profile upgrades only when this reviewed
selection change needs the version-2 fragment envelope.

Projects stores schema-1 inclusion plans/receipts under its own
`creator/overlay_registrations/` directory. These records retain before/after project
and configuration records, source evidence and the completed source hash. They are
separate from the editor's draft records and file-publication journals. The current
limit is 64 registration records; unknown formats and capacity exhaustion stop new
operations without deleting evidence.

File publication precedes inclusion. A saved file plus failed inclusion is reported
explicitly and the file remains intact. The same Save request can retry only inclusion
against the retained plan; it cannot republish the source. If the draft changes, a new
review is required. After restart, a fresh Save review can find the durable pending
inclusion plan. Matching already-applied configuration state is accepted without
rewriting it; unrelated configuration/source changes require reconciliation. Completed
receipts cannot silently reapply a configuration that was later reverted.

The editor adopts a freshly captured context/document after inclusion, preserving
canonical history. Browser responses remain guarded against navigation and newer
typing. Save never runs Configure, builds or firmware; a separate Configure remains
necessary to capture actual overlay participation.

## Consequences

Straightforward registered/imported projects can begin pin editing without creating an
overlay manually or visiting Projects to add the new input. Directory-only contexts
without a saved exact-target configuration remain source-only. Ambiguous composition
and unsupported pin profiles retain visible reasons; creation does not broaden hardware
support. Linux atomic rename and locking support remain requirements for guarded Save.
An append selection with both a missing custom input and a different missing default
overlay does not qualify as a complete single-overlay observation. The draft remains
editable/saveable, while pin activation explains the additional missing dependency.

The system can distinguish recoverable source, completed filesystem publication and
configuration inclusion. No cross-feature private import or multi-file transaction is
introduced. Evidence may require explicit reconciliation after an interrupted write,
and software validation does not establish physical hardware operation.
