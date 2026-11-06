# Device Tree Editor D8 handoff

Status: bounded pin implementation complete and verified on 2026-11-06. D7 was committed
as `4caf359` before this stage. Full checks, browser verification,
installed-package verification and real Configure passed. D9 module recipes and visual
placement is the next stage.

## Delivered workflow

Open a registered `nrf52840dk/nrf52840` configuration and its sole selected application
overlay, then choose **Load pin assignments**. The first adapter checks the supported local
Zephyr evidence before showing controller states, GPIO consumers and a pin table.
Inspection is explicit and cancellable; it does not run Configure, build or hardware tools.

Choose a UART, I2C or SPI controller, `default` or `sleep`, and an observed signal role.
**Assign signal to draft**, **Reassign signal in draft** and **Unassign signal from draft**
update the canonical overlay source and shared Undo/Redo history. A selected inherited
state is copied into the overlay before modification. When a state is shared, the form
requires **Split this shared state for the selected controller** before a change. The
original state and its other consumers remain intact.

The accessible pin table lists 48 SoC pins, available Arduino connector names, board
reservations and observed assignments. Selecting a table row fills the current form;
source changes only after its Apply action. Reserved pins retain an explanation and can
be selected for an evidenced owning controller; the backend checks that ownership on
each command. The table is evidence, not a board drawing or an electrical guarantee.

GPIO forms edit supported local `gpios`/`*-gpios` entries with direct `gpio0`/`gpio1`
providers, an explicit pin and symbolic GPIO flags. Multiple entries have separate indices.
Unassign removes the selected entry while retaining the others; removing the final entry
emits `/delete-property/` so an inherited assignment cannot silently return. Deleting a
required GPIO property may leave work to complete before Configure. A supported deleted
property can be assigned again through the same form.
Shared `cs-gpios` entries cannot be removed through this form because changing their
indices can change existing bus-device wiring.
Chip-select pin reassignment preserves its index; `cs-gpios` removal stays source-only
because it could invalidate or reorder existing bus-device indices.

All changes remain unsaved until **Review and Save**. Configure and Projects Build retain
their separate reviews. Loading never enables a controller, relocates another signal or
inserts binding defaults. Source editing, shared history and recovery remain available.

## Implementation and exact support

`NordicEvidence` provides versioned, read-only target facts. `NordicSource` projects the
supported source and plans atomic changes. `PinService` registers
`POST /api/devicetree/editor/drafts/pins` and the four pin command kinds on the existing
draft command path. Observations are bounded, expire, and bind to the context ID/revision,
draft ID/revision/hash and evidence fingerprint. Commands revalidate evidence before
planning and before the canonical draft commit. No new persistence schema or cross-feature
dependency is introduced.

The runtime profile is deliberately narrow:

- Exact official target `nrf52840dk/nrf52840`, from the selected local checkout. Directory
  opening alone cannot establish the required board/environment identity.
- Profile `nrf52840dk-zephyr-4.4.2-v1`: 41 evidence files have recorded hashes from local
  Zephyr commit `fbab2f598d545ad043ce50a75fefa4550b053f0b`, version 4.4.2. Matching source
  supplies the actual board/SoC declarations, include closure, pin encoders, GPIO layout,
  reservations, connector mappings and relevant binding/driver evidence. The adapter
  observes missing shadow paths too; adding an overriding file invalidates old metadata.
  The local binding catalogue must uniquely resolve the Nordic GPIO/pinctrl bindings
  and their exact dependency hashes; duplicate compatibles or helper basenames are not
  treated as equivalent. Cached evidence still revalidates source and catalogue inputs.
  This does not certify every release that reports version 4.4.2.
- Exactly one selected application overlay, with no custom board/SoC root, shield, snippet
  or unresolved additional overlay. Changed, missing or shadowed profile inputs are
  explained as unsupported. Other configurations retain source editing.
- Known UART/I2C/SPI controllers expose `UART_TX/RX/RTS/CTS`, `TWIM_SCL/SDA` and
  `SPIM_SCK/MOSI/MISO` respectively. The actual controller descriptor constrains roles.
  PWM/QSPI routes are read-only allocation evidence, not editable signal forms.
- Literal default/sleep state names and supported symbolic `NRF_PSEL` entries. GPIO uses
  direct `gpio0`/`gpio1` pin/flag cells and accepted symbolic flags from the selected header.
  Other pin encodings, GPIO nexus mappings in editable local properties, opaque
  expressions and ambiguous contributors require source editing.
- Source includes are limited to the supported GPIO/pinctrl headers for this observation.
  Other preprocessing, node deletions, absolute reference blocks, shadowed board labels,
  provider capability overrides and modified read-only routes make this bounded map
  unavailable. D8 does not add a general DTS preprocessor or effective-tree resolver.

Local expression edits preserve symbolic roles, existing includes, comments and unrelated
bytes. New required headers are inserted explicitly by applicable commands. Copying a
shared state preserves its other signals and properties; unsupported descendant-label
dependencies or comment-removing transformations are rejected. Unassign cleans up only
supported exclusive groups. An empty selected pinctrl state uses an explicit empty reference
list, keeping its inherited state suppressed. One action is one draft/history revision.

Known enabled pin consumers, board reservations and shared controller instances constrain
new assignments. Default and sleep of one controller can reuse the same pin without a
false concurrent conflict. Unknown enabled allocations cannot establish a free pin.
Package pads, solder bridges, external wiring, electrical suitability and runtime ownership
remain unknown. Pin assignment does not prove that a driver or Kconfig setting is enabled.
Inherited GPIO devices are not all editable rows; a supported local property or override is
needed. Broader pin adapters, custom boards and multi-overlay closure remain later stages.

The general UI uses **Pins and signals** and **Load pin assignments**. The product remains
vendor-independent; the first supported adapter is identified inside that panel. D13
introduces a second family, planned as STM32, and later adapters can extend coverage.

The frontend reuses `createDraftObservationSession`, with explicit loading, cancellation,
nearby failure/reconciliation, context/draft invalidation and late-response guards.
Selectors preserve keyboard focus on explicit changes; background renders do not scroll.
The table and forms fit the existing responsive layout and shared application palette.

## Verification

| Check | Status |
| --- | --- |
| Pin frontend focused suite | Passed: 25 tests covering observations, canonical commands, states, explicit split, reservations, GPIO flags/indices, chip-select preservation, selection, focus and lifecycle |
| Evidence, source and pin service focused suites | Passed: 13 evidence, 34 source and 21 service tests, including alias/path contributors, unknown status, binding shadows and metadata invalidation |
| `python scripts/check.py --quick` | Passed during implementation: 921 Python tests with 20 loopback tests excluded, all 30 frontend files; the final full check below includes later regression additions |
| `python scripts/check.py` | Passed: 955 Python tests, 410 frontend tests, lint, syntax and architecture |
| `python scripts/verify_package.py` | Passed: installed wheel imports, entry point and runtime assets, including both new browser modules |
| Desktop/narrow/scrolled/error/leave-reopen browser checks | Passed: inherited/local UART reassignment, source edit reflected in reloaded pin forms, GPIO pin/flags, GPIO unassign/Undo, reserved-pin rejection and nearby reconciliation, retained source after leaving/reopening, explicit metadata reload; no horizontal overflow at 600 pixels |
| Real selected-environment Configure smoke | Passed: 23 commands, seven rejected mutations, reviewed Save and explicit Configure; no firmware compilation or flash |

The smoke also covered a generated project name containing hyphens. Overlay prediction
now recognizes that literal CMake project name, matching Project Creator's accepted names;
the focused overlay-usage regression suite passed all 17 tests.

### Real selected-environment Configure

The disposable application used the exact `nrf52840dk/nrf52840` target, local Zephyr
4.4.2 checkout and SDK 1.0.1 through the public Projects and editor services. Its
configuration explicitly selected one generated board-named application overlay.
The real evidence observation accepted all 41 profile files and discovered 10 controller
descriptors, 20 inherited states, 48 pins and nine board GPIO consumers.

The run applied 23 canonical pin commands and rejected seven invalid changes. It split
UART1's shared inherited UART0 default state only after the explicit split choice, then
reassigned UART1 signals and removed its copied RTS/CTS signals. Shared Undo/Redo restored
the exact draft source. UART1, I2C0 and SPI1 default/sleep assignments were changed through
the command path; GPIO assignment, unassignment and reassignment retained symbolic flags.
The final UART1 sleep state had every signal unassigned, leaving `pinctrl-1 = <>` to
suppress inherited routing. Zephyr Configure accepted that source. Rejections covered an
unapproved shared-state change, invalid pin range, reservation, existing pin consumers,
invalid controller signal role and a concurrent shared hardware instance.

Draft commands left the application overlay unchanged until reviewed Save. Explicit
Configure then returned `configured: true`, `overlay_used: true` and `stale: false`.
The overlay hash remained unchanged by Configure; generated DTS recorded the intended
states and GPIO specifier. No `zephyr.elf` was produced and no firmware build or flash ran.
The exact disposable application and its build directory were removed and their absence
was verified. Only isolated verification records remain under `/tmp`.

Local evidence is retained in `/tmp/zephyr-d8-real-3f162ff2/`, including
`verification.json`, `candidate.overlay`, `generated.dts`, `initial-pins.json`,
`final-pins.json`, `commands.json`, `rejections.json`, `timings.json`, reviewed Save and
Configure records, the Configure job/result, `cleanup.json` and the harness. The first
pin load took 5.819 seconds; validated cached loads took 0.826–1.038 seconds and accepted
commands took 0.849–1.887 seconds in this fixture. These observations are not general
performance guarantees. Successful Configure does
not establish compilation, driver readiness, valid external wiring or hardware behavior.

No hardware behavior, firmware compilation or flashing is claimed by this handoff.

## Next stage

When requested, implement **D9 only** from the
[master plan](device-tree-editor-masterplan.md): a small tested module recipe catalogue
and logical placement/connection workflow using D6–D8 commands. Reuse canonical source
and explicit Save/Configure boundaries. Additional pin families, custom board mappings,
multi-overlay composition and broader removal planning remain their separate stages.
