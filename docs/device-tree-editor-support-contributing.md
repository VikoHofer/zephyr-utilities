# Device Tree Editor: board support and contributions

Open **Device Tree Editor → Supported boards** to see the evidence bundled with the
installed application. The page separates boards checked through the editor from
broader automatic family coverage and adapters that remain under construction. It is
available without selecting a project or running discovery.

## What the list means

The catalogue lives in
[`support-data.js`](../src/zephyr_utilities/features/devicetree/web/support-data.js).
It records exact targets, implemented functions, source/edit/Save evidence, build
results and relevant restrictions. A passed build is separate from an editor check;
neither establishes physical hardware operation. A retained route is separate from
testing a changed route. Keep those distinctions when adding evidence.

This catalogue documents the installed version. It does not enable or disable pin
editing and is not a complete list of boards in the user's Zephyr checkout. Runtime
availability comes from the selected board/SoC sources, revision, package table,
bindings, current assignments and the routing rules understood by the adapter.
An unlisted chip using an understood source format can already work automatically;
a listed board can still have unavailable pins or configurations with a specific reason.

STM32 currently uses selected package pin-table alternatives. Nordic uses logical
GPIO slots; these do not establish package bonding or board header positions. The
latest recorded source/build checks are in the [automatic support handoff](device-tree-editor-automatic-chip-support-handoff.md),
[board revision handoff](device-tree-editor-board-revisions-handoff.md) and
[nRF54L handoff](device-tree-editor-nrf54l-handoff.md). Later handoffs supersede the
earlier limitations they explicitly address.

## Contribute evidence or report a gap

You can help without writing an adapter. Open a small application for your exact
target, including its revision and CPU/core qualifiers, and record:

- Board name, SoC/package when known, exact target, Zephyr revision and relevant module versions.
- Whether a chip and current assignments appear automatically with an existing overlay
  and without an overlay. Include the exact unavailable reason if discovery or an action stops.
- The controller, pins and function attempted, and the relevant minimal DTS, binding or
  pin-table source. Explain whether the pins are logical slots, package pads or board headers.
- Which Apply, Undo/Redo, reviewed Save, reopen and restart-recovery checks passed.
  Record a Configure/build result separately, including whether the generated overlay was selected.
- The scope of any hardware check actually performed, or explicitly that no hardware test ran.

Use a sanitized minimal reproducer and small synthetic or appropriately sourced
fixtures. Do not commit firmware builds, personal paths, credentials or captured device
data. A missing board in the information page alone does not require an implementation
change: first check whether the shared adapter already accepts its selected sources.

## Add a source format or vendor adapter

Follow [Contributing](../CONTRIBUTING.md), [architecture](architecture.md) and the
[chip and pin plan](device-tree-editor-pin-workspace-plan.md). Choose one complete
GPIO/UART/I2C/SPI workflow with explicit acceptance targets and limits. Espressif, NXP
and Raspberry Pi chip/pin adapters are still planned; the old sensor/module workflow
is outside this stage.

The relevant files are under `src/zephyr_utilities/features/devicetree/`:

| Area | Extension point |
| --- | --- |
| Selected inputs | `board_selection.py`, `board_sources.py` and `board_topology.py` resolve and observe the selected source closure. Preserve module/custom roots, revisions, source fingerprints and composition guards. |
| Pin inventory | `chip_catalog.py` identifies an understood source format, decodes pins and inherited assignments, and retains provenance. Discover from selected sources rather than copying a board's pin list into code. |
| Routing rules | `chip_stm32.py` and `chip_nordic.py` expose profiles, pin restrictions and route plans. `chip_nrf54l.py` demonstrates a bounded architecture-specific rule module. Validate encodings, ownership, complete signals and electrical properties before offering actions. |
| Activation | `chip_activation.py` selects the adapter, validates current evidence and applies canonical `chip_assign` / `chip_gpio` commands. Review its GPIO provider assumptions and `chip_gpio.py` when adding a different binding or port model; a new dispatcher branch alone is insufficient. |
| Source updates | `chip_source.py` applies validated route plans to the application draft. New pin headers or state forms need explicit support and validation; preserve unrelated source, inherited electrical settings and existing consumers. |
| Draft lifecycle | Reuse `drafts.py`, `recovery.py`, `saving.py` and `publication.py` for history, recovery and reviewed writes. Keep the existing public Projects handoff for new-overlay inclusion. |
| Visible evidence | Update `web/support-data.js` after recording verification. This entry documents coverage; it must never become a runtime allowlist. |

Keep unsupported formats, ambiguous sources, cross-domain ownership and resource
conflicts unavailable with useful reasons. Do not infer package pads, clock use or
electrical suitability from a family name. Extend only the source assumptions needed
by the chosen workflow; do not bypass discovery guards to make an acceptance board pass.
Opening a project must remain automatic and offline. Discovery and activation must
leave upstream board, SoC and HAL files unchanged.

## Verify the change

Use focused adapter and catalogue tests during implementation. Existing examples include
`tests/test_devicetree_automatic_catalog.py`, `test_devicetree_chip_stm32.py`,
`test_devicetree_chip_nordic.py`, `test_devicetree_chip_nrf54l.py` and
`test_devicetree_chip_activation.py`. Cover rejected encodings and ambiguous or missing
evidence as well as successful routes. Include conflicts and stale-source rejection
without a draft or file write.

Exercise canonical commands through one-step Undo/Redo, existing-overlay Save, missing-overlay
first Save, reopen and restart recovery. Use the missing-overlay, publication and recovery
tests as examples. Verify that Undo after first Save changes the draft without deleting
the created file. For a new adapter, check representative real Zephyr sources, and when
available configure/build a small isolated application using its saved overlay. Record
the exact environment and any unverified part instead of treating synthetic tests as
board validation.

Run the repository checks from its root with the development environment active:

```sh
python scripts/check.py --quick
python scripts/check.py
python scripts/verify_package.py
```

The full check includes local HTTP integration; package verification is required when
adding or moving browser assets. For UI changes, check keyboard access, narrow widths,
filter/empty states, leaving/reopening the view, and preservation of scroll and focus.
Include the evidence and remaining limits in the change description and update the
support catalogue only to the level actually verified.
