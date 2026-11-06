# Device Tree Editor: controller-first delivery

Accepted product direction: 2026-11-06. This supersedes the earlier sequential D-stage
delivery order. Existing D0–D11 implementations remain useful foundations; their scoped
completion labels do not establish a complete visual editor for beginners.

**MVP revision, 2026-11-06:** focus delivery and representative testing on ST/STM32,
Nordic, Espressif, NXP and Raspberry Pi. The generic discovery and basic-connection
foundation now allow work to move to the integrated visual workspace. Exhaustive
controller coverage is not an MVP prerequisite. Board revisions/extensions, larger
overlay combinations and additional vendors are deferred unless they block a concrete
MVP acceptance case.

## Intended experience

Select a board/controller, see its buses and available GPIO connections, choose a
sensor, LED or other supported module, and connect it visually. That connection creates
the required application overlay declarations. Source and visual state stay synchronized;
Undo/Redo, reviewed Save and explicit Configure remain available. A beginner must not
write controller declarations before the visual editor becomes useful.

The selected board's DTS and SoC includes are the hardware starting point. Discover
across vendors using local Zephyr, module and registered custom roots. Node identity,
bindings, buses, GPIO specifier cells and pinctrl evidence drive capabilities. Aliases
such as `led0` can supply friendly names but never decide whether hardware exists.
Upstream board and SoC files remain unchanged; application connections use scoped
reference overrides in the overlay.

## Why the existing workflow failed

The D9 canvas projects unique declarations in the application overlay. A new or empty
overlay has no local bus/provider declarations, so the canvas can be empty even when
the selected board has usable controllers. The existing module command requires a
local parent and cannot connect to inherited board hardware. The Place module toolbar
also returned without feedback when the already-open placement form was selected;
empty-canvas layout could put that form below the viewport. These are product gaps,
not evidence that the user's board lacks hardware.

The first user-reported acceptance target is ST Nucleo F446RE (`nucleo_f446re`).
Discovery must use the same path for other families, not a board-name allowlist.

The brand list prioritizes development and testing; it does not assert support for
every controller, pin encoding or configuration from those brands. Working generic
discovery may also serve other/custom boards. Actual source/binding/routing capabilities
determine whether a visual connection is available. Do not reject a supported SoC
solely because its board maker has a different name.

## Unsupported-controller behavior

Keep the selected board/controller visible. When the current controller/configuration
or requested connection cannot be used, show a prominent notice:

> Device Tree Editor is under construction. More controller support will be added over time.

Keep the concrete reason nearby, disable unavailable connection actions, and offer a
way to retry discovery or choose another board/connection. Do not report a blocked
discovery as a successful hardware load. Missing setup, stale source or unavailable
tools retain their specific explanations; these do not prove that the silicon is
unsupported. Existing source/draft recovery remains available. Server-side capability
checks must reject attempts to bypass the UI. The notice never authorizes guessed pins.

## Delivery order and acceptance gates

1. **Scoped MVP foundation.** Maintain the delivered shared discovery and basic I2C/GPIO
   connection workflow for the initial brand focus. Read the selected board, its include closure and
   local bindings; merge the current overlay draft for observation. Show the board,
   enabled and disabled controllers, GPIO providers, existing devices and available
   connector/routing evidence. An unsupported input must retain the board identity
   and one actionable explanation rather than a blank canvas. Keep discovered facts,
   binding defaults and inferred physical availability distinct.
2. **Clear support boundaries.** Show unsupported-controller/connection blockers and
   useful next actions. Preserve the demonstrated basic workflow: connect an external LED to an observed GPIO
   or a sensor to an existing I2C bus from an empty overlay. Create/reuse the required
   reference block/group automatically, check known collisions, preserve shared
   hardware, and reconstruct the graph after Save/reopen. Existing board I2C routing
   is a usable first path; arbitrary bus-pin reassignment needs verified pin encoding.
3. **Integrated visual workspace — next feature work.** Persistent context/actions, module catalogue and
   device tree, board/connection canvas, selection-driven inspector, and source/diff
   below. Ordinary actions retain visible position and focus. Optional technical
   evidence belongs in details; supported actions should be obvious. Keyboard and
   narrow-screen panel navigation must offer the same workflow.
4. **Common modules and needed pin routing.** Expand common devices one complete
   workflow at a time for the MVP brands. Add family pin encodings and board/package
   mappings as required by those workflows. Keep discovery vendor-independent. An unsupported
   mux operation must not hide the board or prevent unrelated supported GPIO/bus work.
5. **Later expansion.** Broaden controller/revision/composition coverage and resume
   remaining D11 depth and D12–D15 work after the usable MVP. These are no longer a
   mandatory queue ahead of the visual workspace.

Before calling the foundation complete, verify blank-overlay discovery on the user's
STM32 board and representative boards from other vendors using real local DTS/bindings.
Record which cases were discovered, connected, configured or physically tested; those
are different claims. Scanning the catalogue alone does not prove universal pin routing.
Use the same adapter-independent discovery contract for all cases.

## First implementation boundary

The first implementation added read-only selected-board topology and inherited
GPIO LED / I2C sensor connections, along with the Place module feedback correction.
The [handoff](device-tree-editor-controller-first-handoff.md) records the implemented
first slice and its verification. The broader acceptance gates above remain open.
The foundation is committed as `a0fa84b`. The next discovery slice adds exact CPU/core
and nested-variant target resolution from current SoC/board metadata; its evidence and
remaining gates are tracked in the [qualified-target handoff](device-tree-editor-controller-targets-handoff.md).
That slice is committed as `cc32fb1`; its former discovery-first next-step list is
superseded by the MVP order above.
The [MVP support handoff](device-tree-editor-mvp-support-handoff.md) records the
implemented capability blockers and the verification for this scope revision.
No automatic Configure/build or firmware write is implied by discovery or connection.
Known board-defined bus pins may be shown; a generic GPIO index or default `ngpios`
does not establish a bonded package pin or accessible header location.

Exact board/revision selection, include/binding freshness, draft identity and source
preservation remain mandatory. If source composition cannot be resolved accurately,
show the board and explain the missing input. Do not fabricate hardware, copy all
board definitions into the application, or replace hand-written source wholesale.

The local checkout survey found 973 `board.yml` files across 160 vendor directories
and 1,450 board `.dts` files. These are discovery inputs, not a verified-support count.
