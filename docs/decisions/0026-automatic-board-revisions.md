# ADR 0026: Automatic declarative board revision selection

Status: Accepted, 2026-11-07.

## Context

The chip editor rejected every board with revision metadata, including nRF9160 DK
and nRF9161 DK. Projects could already retain default and declared explicit board
targets, but automatic application-overlay prediction did not resolve an omitted
revision. Resolving these two paths differently would show or edit the wrong source.

## Decision

Share a pure, bounded revision parser/resolver in `core/board_revisions.py`. Projects
uses captured board metadata to predict application overlay selection. Device Tree
Editor verifies that metadata against current board sources, resolves the revision
again, and composes base board DTS, selected revision overlay and application draft
in that order. Include closure, metadata and absent candidate paths retain the normal
source-change guards.

Keep the saved target and its requested revision unchanged. Projects exposes optional
`target.revision_resolution` evidence; chip observations expose the independently
verified `selection.revision` and ordered `selection.board_overlays`. These additive
observation fields do not change persisted profile or recovery schemas. The editor
shows the resolved revision without requiring another selection or extraction action.

Default and explicit declared revisions use standard declarative Zephyr metadata.
The existing catalogue admission boundary remains: arbitrary unlisted revision targets
are not added to Projects. Custom `revision.cmake`, board extensions and unsupported
composition remain visibly unavailable. Reading a project does not execute revision
scripts, configure firmware, alter its target or write an overlay.

## Consequences

Both features share revision semantics without importing each other's implementations.
New boards using these standard formats need no board-specific registration. A missing
revision overlay is valid when the base DTS supplies that revision; ambiguous source
names or changed metadata cannot silently select another source.

The local Zephyr CMake implementation remains the comparison authority for supported
source naming and revision behavior. Numeric compatible requests that it cannot resolve
remain unavailable; declared numeric defaults and exact revisions still work. Custom
scripts and more general board composition need a separate bounded implementation.

See the [implementation and verification handoff](../device-tree-editor-board-revisions-handoff.md).
