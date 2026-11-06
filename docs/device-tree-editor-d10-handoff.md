# Device Tree Editor D10 handoff

Status: resource diagnostics and explicit removal reviews complete within their documented
graph limits on 2026-11-06. Full repository, browser and installed-package checks passed.
D9 was committed as `3cf6db1` before this stage.
D11 advanced peripherals/system resources is next when requested, in bounded slices.
No complete inherited-tree, electrical, runtime or hardware validation is claimed.

## Delivered workflow

Choose **Load diagnostics** in **Resources and removal** to inspect the current canonical
draft. Errors, warnings, unknown evidence and information are distinct. Known consumers
have source locations and explicit inspection actions. Findings cover literal local
references, aliases/chosen, binding path values, bus addresses and established GPIO limits,
plus allocations/reservations/controller instances from the existing supported pin adapter.
Unknown pin support does not suppress the vendor-independent local graph.

Multiple children share their bus controller's lines without being counted as independent
pin owners. Default and sleep pinctrl states on the same controller are not concurrent
owners. Duplicate enabled local addresses and established exclusive pin/output ownership
are conflicts; disabled declarations remain visible as warnings. Opaque status and generic
GPIO sharing without proven exclusivity remain unknown. Missing `ngpios`, malformed reserved
ranges and unsupported native chip-select capacity are not invented limits. The editor's
7-bit I2C range is a scoped warning for other addressing modes, not universal device rejection.

Choose a local target and **Review removal**. The dialog lists its descendants, known
consumers, unresolved evidence and the proposed diff. Optional literal reference properties
and alias/chosen entries can be offered as individually selected whole-property repairs.
Selecting a repair requests a new preview; it never applies it. Required, ambiguous, mixed,
opaque or structurally unsafe consumers require separate source/form repair. A provider or
pin group with a required consumer remains blocked, with links to the consumer's source.
Known inherited controller references also block removal of a local state or nested group,
even if its pin allocation is unknown or that controller is disabled. Their board provenance
is displayed without inventing a local source span. Indexed chip-select and pin-state
routing require explicit form/source repair; they are not whole-property repair choices.

**Apply removal to draft** applies exactly the reviewed candidate as one Undo/Redo operation.
It preserves unrelated parents, providers and properties. It does not save the file,
configure, compile, flash or execute project code. Existing reviewed Save, recovery and
Configure contracts remain unchanged. In the normal UI, module/peripheral removal actions
open this review; their legacy bounded backend commands remain compatible and guarded.

## Implementation and boundaries

`ResourceService` owns at most 16 ten-minute observations under the canonical source guard.
`resource_graph.py` indexes source occurrences, containment and literal references, including
property labels and binding path strings. A reference retains every ambiguous target, so a
node label and property label on the same owner cannot collapse into a false unique match.
Unique local pin states/groups below one unambiguous `&pinctrl` can be reviewed as source
subtrees; root reference overrides remain source-only. `graph.pin_state_consumers` retains
known controller/state dependencies from the verified adapter independently of allocation
knowledge. This does not resolve the complete inherited tree.

The graph respects existing 512-node/4096-property visual limits, caps references at 4096
and verified pin-state consumer rows at 256,
and explicitly marks truncation. Diagnostics are capped at 256, including during potentially
large pin-collision construction. Truncated dependency graphs block removal. Incomplete
source remains inspectable with unknown coverage and no actionable graph. Optional pin
inspection failures preserve local diagnostics. Current local binding files and any observed
pin evidence are revalidated at review and apply; stale context, source, schema or evidence
cannot authorize a mutation. Diagnostics themselves never globally block Save or newer drafts.

`RemovalService` retains at most 16 ten-minute reviews, each with at most 64 selected repair
properties. Review bodies include the exact draft/context guard, resource `schema_id`, target
`node_id` and selected repair IDs. The response contains candidate text/diff, known effects,
choices, blockers and uncertainty. `remove_reviewed` uses only its review ID and the shared
canonical command guard. The source plan is applied once as one draft revision/history entry.

External includes, macro-generated references, other overlays and inherited consumers outside
the supported adapter remain unknown. Some unknowns are scoped warnings; target-related
unsafe dependencies block a review.
Removing local source may reveal inherited values. No automatic rewiring, consumer deletion,
provider cleanup, Kconfig editing or new hardware adapter is introduced. No new persistence
format or core/cross-feature boundary is added, so D10 needs no new architecture decision.

The browser uses the existing retained-presentation lifecycle. Explicit source selection
reveals its location once; passive refresh preserves focus and scrolling. Removal review
supports cancellation, Escape and returning focus to its trigger. Stale/late responses must
not replace another context or apply an old candidate. The final browser checks confirmed
exact source selection and focus, explicit repair review/apply/Undo, blocked unsafe removals,
and usable narrow and scrolled layouts.

## Verification

| Check | Status |
| --- | --- |
| Resource diagnostics focused tests | Passed: 26 cases, including valid sharing, established conflicts, source locations, alias/chosen/property-label references, opaque/incomplete evidence, known inherited pin-state consumers, stale observations, optional pin failure and construction limits |
| Existing pin service regression tests | Passed: 21 cases after additive allocation metadata and bounded conflict materialization |
| Removal focused tests | Passed: 24 cases, including inherited controller dependencies and indexed-routing repair limits; also included in the final full run |
| Shared HTTP resource/removal integration | Passed: one focused integration case; also included in the final full run |
| Actual local Zephyr bindings | Passed: four read-only cases covering shared I2C/SPI buses, duplicate I2C address, GPIO output collision and explicit GPIO range; catalogue fingerprint unchanged |
| Ruff on resource/pin files and tests | Passed |
| Complete repository checks | Passed: `python scripts/check.py`, including 1031 Python tests and 468 frontend tests, lint, syntax and architecture checks |
| Browser desktop/narrow/scrolled/error/leave-reopen checks | Passed: exact `reg` source selection and focus; unsafe GPIO provider and used pin group blocked; unused pin state removable; two explicit repairs applied atomically and one Undo restored exact source; 600 × 800 layout without horizontal overflow, with fixed summary/footer; incomplete source showed unknown-only coverage; restored source reloaded diagnostics and an empty warnings filter; Home navigation and editor remount restored the exact enabled draft, reset resources to explicit Load diagnostics and allowed a fresh review; no browser console warnings/errors |
| Installed-package assets | Passed: `python scripts/verify_package.py`, including offline wheel installation and runtime asset checks |

Actual-binding smoke evidence is `/tmp/zephyr-d10-bindings-smoke.json`; its harness is
`/tmp/zephyr-d10-bindings-smoke.py`. It reads the existing Zephyr catalogue, uses synthetic
local source and runs no Configure, build, flash or hardware operation. D10 verification
did not run Configure or validate firmware on hardware.

The final browser screenshot is
`/home/vikofed/.codex/visualizations/2026/11/05/01a1acb6-f8a4-7e30-837b-87ae026e4d6b/device-tree-d10-removal.jpg`.
No implementation code changed after the final full check.

## Next stage

When requested, implement one bounded **D11** capability slice from the
[master plan](device-tree-editor-masterplan.md). Preserve source as canonical, explicit
repairs and reviewed Save/Configure separation. Broader target adapters, board maps,
composition and multi-image support remain their separate stages.
