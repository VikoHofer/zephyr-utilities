# ADR 0022: Module layout and source identity

Status: accepted for D9; implementation verification is in progress.

## Context

The module canvas needs to retain presentation coordinates across context reloads and
application restarts. Canonical overlay source must remain sufficient to reconstruct
the hardware graph. Source occurrence identifiers change across draft revisions and
cannot identify a persistent position. UI layout must not become a firmware dependency,
source history entry or recovery obligation.

Recipes also need a clear removal boundary. D9's small built-in catalogue can create a
single leaf under an already declared bus or GPIO group without owning shared hardware.
Handwritten declarations must remain usable without mandatory conversion or metadata.

## Decision

Reconstruct the logical graph from the current canonical source and selected local
bindings. Unique local paths become stable presentation keys prefixed with `node:`.
Occurrence IDs continue to identify source command targets only within their exact
observed draft. Paths with repeated contributors receive no persistent graph key.
Recipe matching is structural and informational; it never establishes ownership,
hardware compatibility or an automatic upgrade obligation.

Version the data-only built-in recipe catalogue and require the exact observed recipe
version when adding a node. D9 recipes generate one leaf only. They do not create or
own parents, providers, pinctrl states, include directives or configuration fragments.
Removal therefore deletes only the selected leaf after existing source-consumer guards,
and leaves all shared declarations intact. No ownership metadata is introduced. A future
recipe that creates multiple declarations must establish a new reviewed ownership and
removal contract before using it.

Persist presentation separately through `LayoutService` and `LayoutStore`, under
`context.feature_data("devicetree") / "module-layouts"`. Schema version 1 contains
`schema_version`, `layout_id`, `revision`, `scope` and `positions`. The scope includes
workspace path and identity, application path, overlay path, project/configuration IDs
and requested target. Its canonical JSON hash supplies the filename and layout ID.
It excludes transient context IDs, draft revisions and firmware hashes so reopening the
same selected document can retain positions.

Coordinates are integer grid values from 0 to 31 on each axis. Each position must belong
to the current observed graph. Layout read/save requests require the canonical context
and draft guard plus the module observation ID. Writes also require the observed layout
revision. Reading filters positions for absent nodes; layout data never creates hardware
nodes. Renaming or moving a source node changes its path key and can reset its position.
This has no effect on hardware semantics.

The store supports at most 256 layout records, 256 KiB per record and 256 positions per
record. The canvas shows at most 256 nodes and reports truncation explicitly. Unknown
schema versions or malformed records are rejected and preserved, with no automatic
replacement. No previous layout format exists, so there is no migration in D9.

An in-process lock and directory `flock` serialize read/compare/write across cooperating
application processes. Files are bounded regular files; symlink directory components
and record files are refused. Writes use a temporary file, synchronization and atomic
replacement, followed by directory synchronization. A stale layout revision or failed
replacement leaves the previous record intact. A synchronization error after replacement
can leave the new revision stored; reload establishes its current state. Storage exhaustion preserves
existing layouts rather than evicting user data.

## Consequences

Source alone reconstructs the same logical module graph after reviewed Save/reopen,
including when no utility metadata is available. Losing layout data only loses card
positions. Moving cards does not change overlay bytes, source Undo/Redo, recovery or
project execution. Saving layout and saving firmware source remain distinct actions.

The initial recipes work with supported local declarations across vendors. They do not
resolve the complete inherited tree, route wires, enable software or extend family pin
adapters. Missing binding/recipe data remains unavailable. Multi-node recipes, automatic
dependency cleanup, physical board drawings and richer layout identity remain later
work requiring their own reviewed scope.
