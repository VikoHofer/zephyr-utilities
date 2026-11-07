# ADR 0023: Board hardware observation and inherited connections

Status: accepted; the first bounded slice is implemented and verified as recorded in
the [handoff](../device-tree-editor-controller-first-handoff.md).

The [2026-11-07 redesign](../device-tree-editor-pin-workspace-plan.md) replaces the
connection product direction. [ADR 0024](0024-chip-pin-workspace.md) records automatic
discovery on project selection in place of this ADR's manual Load action. The bounded
source/tooling safeguards remain; the former connection commands are retired.

## Context

The source-preserving editor operates on an application overlay. Its existing binding
and module observations intentionally cover local declarations only. That model omits
the selected board's inherited controllers, so beginners cannot start with an empty
overlay. The user explicitly prioritizes controller support across vendors and a
visual connection workflow, beginning with Nucleo F446RE.

Board hardware requires DTS includes, preprocessor constants, reference overrides and
bindings from the selected environment. Regex scanning or aliases cannot resolve that
model accurately. A resolved observation must remain separate from writable source.

## Decision

Projects exposes detached selected-board metadata through its existing public context
provider. Device Tree Editor owns hardware discovery and connection planning; it does
not read Projects storage or import its private implementation. Selection identity,
exact target, roots and source provenance remain part of the captured context.

An explicit Load board hardware action may invoke bounded read-only preprocessing and
the selected Zephyr devicetree parser in a subprocess with fixed argument arrays and a
temporary working directory. This is an exception to the former Configure-only tooling
boundary. It never evaluates application/board CMake, runs west builds, imports captured
pickles, downloads packages or modifies firmware inputs. Selected parser/tool code is
executable tooling, not a sandboxed data file; temporary directories do not sandbox it.

Resolve a supported exact board source using selected metadata and module/custom DTS
roots. Observe its transitive source closure, the current canonical overlay draft and
bindings. Preflight reads/includes against allowed roots; refuse escapes, opaque include
selection and unsupported binary source inclusion. Bound input size, node counts,
process time/output and retained observations. Fingerprint source/binding closure,
selection and relevant tooling; revalidate authority before and after connection plans.
Incomplete composition is reported as incomplete rather than silently ignored.

Projects discovery and editor selection share the pure bounded hardware metadata parser
in `core/hardware_metadata.py`. It accepts data only, expands SoC CPU clusters and
nested variants, and has no feature imports, filesystem access or tool execution.
Each feature retains its own input reading, provenance and observation lifecycle.
The editor recomputes qualifiers from current recorded board/SoC metadata and requires
them to match the captured selection before resolving the complete DTS filename.
It never falls back from a missing variant/core to a parent target. Platform metadata
selects the exact target architecture; a board-wide fallback requires one current,
unambiguous architecture. Relevant source and platform inventories are guarded against
later changes. This extends the existing observation contract without migrating saved
profiles or observations.

The hardware projection has stable effective-path identities, source origins, compatible
and bus/provider evidence, status, existing children and available pin/connector facts.
Disabled controllers are still visible. Aliases may annotate a device but do not select
topology roots or determine support. Binding defaults do not establish physical package
or header pins. Effective pinctrl shows current routes; omitted unused alternatives
require a separate family-specific routing catalogue.

Connection commands select an observed controller/provider identity and generate small
source-preserving application edits. They create or reuse a reference override or GPIO
consumer group as required, validate known address/pin constraints and collisions, and
produce one canonical Undo/Redo action. They do not copy inherited controller properties
into the overlay or mutate the resolved observation as though it were writable source.
Save and Configure remain explicit separate operations. No automatic provider removal
or shared-resource cleanup follows device removal.

## Consequences

An empty overlay can display inherited board hardware and become useful without manual
controller declarations. Generic discovery is shared across vendors; exact pin encoding
remains an adapter capability. Unsupported pin reassignment does not hide the board or
block independent supported connections. Board/SoC inputs are preserved.

The first connection slice covers GPIO LEDs and I2C sensors using existing board bus
routing. Full visual pinmux routing, arbitrary source composition, module software
configuration and hardware operation require their own evidence and later acceptance
gates. No universal board-support claim follows from catalogue enumeration alone.
