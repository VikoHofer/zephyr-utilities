# Device Tree Editor: automatic chip workspace

Date: 2026-11-07. Implementation progress under the
[chip and pin redesign](device-tree-editor-pin-workspace-plan.md), replacing the old
sensor/module workflow. This completes the initial automatic inspection and retirement
slice. It does not complete the first-stage pin editing product.

This handoff records the initial inspection slice and its verification. The subsequent
[pin activation handoff](device-tree-editor-pin-activation-handoff.md) adds existing-overlay
GPIO/UART/I2C/SPI editing for both exact profiles, including Undo/Redo and reviewed Save.
The later [new-overlay handoff](device-tree-editor-new-overlay-handoff.md) adds automatic
absent-destination drafts, first Save, recovery and configuration inclusion. Behavior
and verification below describe the original inspection slice.

## Delivered behavior

Selecting an application/configuration or entering from Projects automatically opens
one unambiguous predicted existing overlay and starts chip discovery. There are no
separate hardware, binding or pin extraction steps. The primary view contains a logical
chip diagram, pin selection, search, zoom/pan, a keyboard-accessible list and a pin
inspector. Source/diff, draft options and saved-source Configure are secondary disclosures.

Two source-backed logical pin profiles are implemented:

| Profile | Inventory | Current evidence |
| --- | --- | --- |
| STM32F446 R(C/E)Tx pin-table profile | 50 logical pins | Package-specific local HAL pin table, fixed alternate functions, decoded controller states and GPIO consumers |
| nRF52840 QIAA | 48 logical pins | Selected SoC/package sources, local Nordic pin encoding, decoded default/sleep states and GPIO consumers |

The model preserves package holes, alternatives omitted from the effective tree,
source locations, known reservations, board names and connector annotations. Diagram
positions are logical, not physical pad locations. Configured labels describe source
settings, including possible uses of disabled controllers; they do not prove electrical
activity or conflict-free firmware operation.

An empty overlay still shows inherited assignments. With no usable overlay, discovery
can show board defaults. Invalid or unsupported application composition may also fall
back to board-only coverage, with its exact reason shown. Unsupported chip/package
profiles show a specific unavailable state and Retry. Opening never creates a file,
runs application CMake, configures, builds or flashes firmware.

The canonical source editor, Undo/Redo, reviewed existing-file Save, draft recovery and
explicit saved-source Configure remain. Source synchronisation refreshes the observation;
stale and cross-context responses cannot replace the current chip state.

## Runtime and compatibility changes

`ChipWorkspaceService` captures context/draft identity under the existing locks and runs
bounded discovery outside them through the shared job manager. Context, source and tool
guards are revalidated before serving completed results. The UI disposes its polling
without cancelling jobs just because the feature closed. Explicit cancellation reaches
the preprocessing worker. Rapid acknowledged revisions of the same context/draft coalesce
into one shared job: obsolete tool work stops and only the latest captured inputs finish.
Other contexts and feature jobs remain independent. No new server, runtime network
service or dependency is added.

New authenticated endpoints are `POST /api/devicetree/editor/chip/start`, `/result` and
`/cancel`. Start takes the editor binding and either all current draft guards or none;
result/cancel take the binding and job ID, plus the same complete draft guards when a
draft exists. Binding-only requests remain supported; the browser uses full guards to
reject stale reads/cancellations after coalescing. Existing read-only `/drafts/board` remains,
without recipe-based connection options.

Removed runtime components:

- `board_connections.py`, `modules.py`, `recipes.py` and `layout.py` and their registered
  services; the old board/module panels, graph, browser assets and mounts.
- `/editor/drafts/modules`, `/editor/drafts/module-layout` and
  `/editor/drafts/module-layout/save` endpoints under `/api/devicetree`.
- Commands `board_module_add`, `module_add`, `module_update`, `module_remove`,
  `peripheral_add`, `gpio_group_add`, `gpio_add`, `pwm_group_add` and `pwm_led_add`.
- Old peripheral/PWM/ADC/CAN/interrupt consumer-authoring browser panels and their
  unused sessions/assets. Shared interpretation,
  diagnostics and existing-source protection helpers remain where still needed.

Retired commands fail without changing history or discarding Redo. Existing sensor/LED
declarations and durable source recovery remain intact. Legacy module-layout records
remain inert and unchanged. Recovery schema v1 and the existing-file publisher are not
migrated in this slice. Inspector compatibility aliases and Projects behavior remain.

## Verification

- `python scripts/check.py --quick` passed during development (1,244 Python tests;
  frontend checks passed). The final full suite below includes the subsequent regression
  additions and completed legacy-asset removal.
- Final `python scripts/check.py` passed: 1,277 Python tests and 465 frontend tests,
  including local HTTP integration, lint, syntax and architecture checks.
- Final `python scripts/verify_package.py` passed: the installed wheel contains the new
  chip assets, excludes retired assets and passes import/console checks.
- `git diff --check` passed.
- Browser verification used isolated registered applications and storage. Selecting
  Nucleo F446RE and nRF52840 DK loaded their chips without a second action; empty overlays
  showed inherited assignments. A missing-overlay Nucleo project showed 50 board pins
  with Save unavailable, and RP2040 showed its unavailable profile reason and Retry.
- Pin selection, search, keyboard navigation, same-target leave/reopen, zoom/Fit and
  desktop/600 px/390 px layouts passed. Fit had no internal overflow; narrow layouts
  had no horizontal page overflow. Source synchronisation and keyboard Undo/Redo retained
  source focus on a scrolled page. Reviewed Save and reopen preserved an explicit I2C
  status source edit in the isolated fixture. Browser diagnostics had no warning/error
  entries at the final check; no Configure/build outputs were created.

Real local source preprocessing used Nucleo F446RE and nRF52840 DK. The observed
inventories contained 50/48 pins and 18/29 pins with configured assignments respectively,
with zero undecoded pin-state entries for these inputs. These checks used the selected
local Zephyr/HAL sources; they did not configure, build or exercise hardware.

## Next required gates

1. Existing-overlay GPIO application references and UART/I2C/SPI routing for both initial
   profiles are now implemented in the [activation increment](device-tree-editor-pin-activation-handoff.md),
   including required companions, shared-instance checks, understood default/sleep states,
   source conflicts and atomic Undo/Redo. Its verification is recorded in that handoff.
2. Absent-destination drafts, versioned recovery, guarded new-file publication and public
   Projects configuration inclusion are now implemented in the
   [new-overlay increment](device-tree-editor-new-overlay-handoff.md), with separate verification.
3. Complete ESP32, K64 and RP2040 profiles using the same contracts, with source round
   trips and representative Configure/build checks. Major-brand scope is not a support
   guarantee for every family/package.
4. Verify the full five-profile editing acceptance matrix in the redesign plan before
   calling the first-stage product complete. Do not restore sensor/module composition or
   add advanced peripheral authoring ahead of these gates.
