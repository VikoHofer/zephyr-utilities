# Device Tree Editor chip and pin redesign

Date: 2026-11-07. Product direction accepted from the user's request; implementation
design and delivery gates below are proposed. This replaces the controller-first
connection plan, integrated module-library plan and D0–D15 delivery sequence. Earlier
handoffs remain records of existing code and verification, not future requirements.

The first stage is a graphical chip and pin editor. Opening a project automatically
shows its selected chip and known pin assignments. Selecting a pin exposes its supported
GPIO and peripheral signal choices; applying a complete assignment updates the application
overlay draft. Remove the previous sensor/module placement and connection workflow.
The screenshot establishes the chip-centred interaction and perimeter pin labels; it
does not establish hardware pin mappings, a physical package drawing or vendor support.

Automatic chip loading and retirement of the old sensor/module workflow are delivered
in the [first-slice handoff](device-tree-editor-pin-workspace-handoff.md). GPIO/UART/I2C/SPI
activation for STM32F446 R(C/E)Tx and Nordic nRF52840 QIAA generates canonical overlay
source with Undo/Redo and reviewed Save; see the [activation handoff](device-tree-editor-pin-activation-handoff.md).
Automatic absent-destination drafts, guarded first Save and Projects inclusion are now
implemented in the [new-overlay handoff](device-tree-editor-new-overlay-handoff.md).
The later request to expand STM32/Nordic without registering every chip is implemented
through selected DTS, bindings and pin tables in the
[automatic chip support handoff](device-tree-editor-automatic-chip-support-handoff.md).
That source-format approach supersedes the initial exact-chip catalogue gates; the
reference profiles below remain regression and acceptance examples.
The full five-profile gate remains open.

## First-stage scope

Use the previously selected major silicon vendors as the initial delivery boundary:
ST/STM32, Nordic, Espressif, NXP and Raspberry Pi microcontrollers. Exact representative
chips/packages established the first adapters; grow STM32/Nordic support automatically
through selected source formats rather than adding chip names to a supported list.
Support follows the selected silicon and observed capabilities, including custom boards
using that silicon; a board manufacturer's name does not establish support.

Deliver GPIO, UART, I2C and SPI pin assignment, current-assignment display, conflicts,
Undo/Redo, source preview and Save. The visual workspace is the primary screen. No
sensor catalogue, module placement, connection wires between external devices or
requirement to recreate board controllers belongs in this stage. PWM, ADC, CAN, USB,
interrupt-consumer forms, automatic firmware generation and additional vendors are
outside this stage. Existing uses of those functions still appear where decodable and
must be protected when editing supported pins.

Peripheral assignment means configuring its devicetree route and enabled status. It
does not establish that firmware has enabled its driver, executed it or electrically
activated the pins. Kconfig and firmware requirements are visible where relevant;
the editor does not silently edit application code or Kconfig.

## The intended interaction

1. Open an application from Projects or select it in the editor. Reuse the selected
   configuration and exact target. Automatically resolve the chip, source inputs,
   overlay destination, current draft, pin capabilities and current assignments.
   No separate Open overlay, Load hardware, Load pins or Load bindings steps remain.
2. Show a large chip with pin labels around its perimeter. The centre identifies the
   SoC and verified package, with board/target information in a compact toolbar. Known
   board assignments are visible even with an empty application overlay. Search finds
   a pin or signal. Fit, zoom and pan keep larger chips usable.
3. Click or keyboard-select a pin. An adjacent inspector shows its identity, current
   assignment, source, known restrictions and available functions such as
   `I2C1 SCL`, `SPI1 MOSI`, `UART2 TX` or `GPIO`. Unavailable choices have concrete reasons.
4. A multi-signal function opens one small assignment form. Selecting I2C SCL also
   exposes SDA candidates for that controller; SPI exposes its required signal set
   and supported chip-select mode. Preserve valid existing companion pins and show
   every proposed change. Never silently pick other pins or activate an incomplete bus.
   Incomplete selections remain temporary inspector state: Cancel leaves source unchanged,
   and a source/context change invalidates a stale proposal.
5. Apply commits the complete route and associated controller properties as one
   reversible source command. Labels and the source diff update together. Selecting
   an already configured function without a change is a no-op. No sensor is created.
6. Save writes the reviewed overlay to the displayed destination. Ordinary pin edits
   need no modal confirmation; a Save review or consequential reassignment can show
   the exact changes. Reopening reconstructs assignments from source.

Keep the toolbar, chip and selection inspector together. Source/diff is a collapsible
drawer, not a large form preceding the chip. Diagnostics are attached to affected pins
and summarised nearby. Existing Configure remains an explicit optional action; it is
not a prerequisite for loading or using the editor.

The default drawing is a **logical pin view** with stable ordering, similar to the
reference. It must not imply physical package pad placement. Display actual pad/ball
numbers or a package geometry only when verified data exists. Keep GPIO identifiers,
package pads and board header positions distinct. Power, reset, debug, oscillator and
other non-routable pins are informative when known; they are not ordinary GPIO choices.

At narrow widths the inspector becomes an adjacent sheet with the selected pin still
identifiable. Provide an equivalent searchable pin list, keyboard navigation, Enter to
select and Escape to close. Meaning uses labels and shapes as well as colour. Use the
shared application palette and bundled SVG/HTML/CSS, with no runtime network dependency.

## Research findings that change the implementation

The repository audit used Utilities commit `e673aef` and the local Zephyr checkout at
`fbab2f598d5` (`VERSION` reports 4.4.2). These identify inspected source, not a tested
compatibility range.

| Finding | Consequence |
| --- | --- |
| `web/board-view.js` renders controllers and device forms; `web/module-view.js` renders a local declaration graph | Replace the main view and its interaction model; recolouring the existing canvas is insufficient |
| `web/board.js` decides availability through BME280/LED recipes | Remove recipe-based support checks and make capabilities pin/function-specific |
| `board_sources.py` resolves real board/SoC sources, roots and tooling | Reuse its guarded reader and preprocessing foundation |
| `BoardSources` requires one existing selected overlay and excludes revisions, shields, snippets and larger composition | Separate chip discovery from draft existence; declare composition coverage and expand it for the accepted targets |
| `BoardTopologyService.inspect()` holds editor/draft locks across subprocess execution | Snapshot inputs, release locks, run discovery as a shared job, revalidate before publication |
| Generic topology contains configured controllers and some routes, not a complete package/function catalogue | Add family-specific capability data and decoders |
| `NordicEvidence` is tied to one nRF52840 DK profile and exact source hashes | Reuse proven source-edit patterns, but replace this gate with explicit chip/package/encoding capabilities |
| Recovery records store source text and source history | Removing module commands need not replay those commands to recover users' edits |

Zephyr deliberately uses vendor-dependent pin encodings and state representations.
Its documentation also distinguishes current configurations from optional package-level
mapping catalogues. Therefore the effective DTS alone cannot enumerate every legal
alternative. Read referenced pin tables and their provenance separately, including
entries omitted from an effective tree by `/omit-if-no-ref/`.
See [Zephyr pin control](https://docs.zephyrproject.org/latest/hardware/pinctrl/index.html).

GPIO counts establish controller slots, not necessarily bonded package pins or board
accessibility. A pin can be present on the chip but wired to onboard hardware or absent
from a connector. The capability model needs all three facts independently.

### Initial silicon profiles and automatic expansion

The first two profiles established existing and new-overlay pin activation, subject to
their observed source, pin and controller constraints. The automatic extension reads
one selected STM32 HAL pin table or the Nordic DTS GPIO/PSEL model and derives the
inventory without a per-chip entry. Nordic GPIO slot counts do not establish package
bonding or cross-domain/core ownership. Unknown formats retain visible limits.
The other three rows remain
research and implementation candidates. The full five-profile acceptance matrix is
still required before claiming the five-vendor stage complete.

| Vendor | Initial target | Data and adapter needed |
| --- | --- | --- |
| ST/STM32 | `nucleo_f446re/stm32f446xx` | STM32F446RE package pinctrl alternatives from the selected STM32 HAL module, STM32 pinmux decoding, board wiring and current assignments |
| Nordic | `nrf52840dk/nrf52840` | nRF52840 source GPIO slots, `NRF_PSEL` signals, peripheral-instance sharing, board reservations and default/sleep states; physical package bonding needs separate evidence |
| Espressif | `esp32_devkitc/esp32/procpu` | Exact ESP32 variant/module, GPIO matrix alternatives, input-only/reserved pins, flash/boot restrictions and binding-specific I2C properties |
| NXP | `frdm_k64f/mk64f12` | Exact K64 package header and mux definitions, PORT/GPIO relation, electrical configuration and board wiring |
| Raspberry Pi | `rpi_pico/rp2040` | RP2040 signal mapping tables, distinct GPIO/QSPI pads, Pico board assignments and exposed pins |

Concrete inputs inspected in the local Zephyr workspace:

| Profile | Local catalogue and current-assignment sources | Supplemental primary reference |
| --- | --- | --- |
| STM32F446RE | `zephyr/boards/st/nucleo_f446re/nucleo_f446re.dts`; `modules/hal/stm32/dts/st/f4/stm32f446r(c-e)tx-pinctrl.dtsi` | [ST open pin data](https://github.com/STMicroelectronics/STM32_open_pin_data), a candidate for additional physical-package metadata |
| nRF52840 | `zephyr/dts/arm/nordic/nrf52840_qiaa.dtsi`; `zephyr/boards/nordic/nrf52840dk/nrf52840dk_nrf52840-pinctrl.dtsi`; `zephyr/include/zephyr/dt-bindings/pinctrl/nrf-pinctrl.h` | [Nordic product pin documentation](https://docs.nordicsemi.com/r/bundle/ps_nrf52840/page/pin.html), to verify when preparing the supplemental package profile |
| ESP32 | `zephyr/dts/xtensa/espressif/esp32/esp32_wrover_e_n4r8.dtsi`; `zephyr/include/zephyr/dt-bindings/pinctrl/esp32-pinctrl.h`; `zephyr/dts/bindings/i2c/espressif,esp32-i2c.yaml` | [Espressif GPIO restrictions](https://docs.espressif.com/projects/esp-idf/en/v5.1.3/esp32/api-reference/peripherals/gpio.html) |
| K64 | `modules/hal/nxp/dts/nxp/kinetis/MK64FN1M0VLL12-pinctrl.h`; `zephyr/boards/nxp/frdm_k64f/frdm_k64f-pinctrl.dtsi` | [NXP K64 datasheet](https://www.nxp.com/docs/en/data-sheet/K64P144M120SF5.pdf) |
| RP2040 | `zephyr/include/zephyr/dt-bindings/pinctrl/rpi-pico-rp2040-pinctrl.h`; `zephyr/include/zephyr/dt-bindings/pinctrl/rpi-pico-pinctrl-common.h`; `zephyr/boards/raspberrypi/rpi_pico/rpi_pico-common.dtsi` | [RP2040 datasheet](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf) |

These paths are relative to the workspace containing the Zephyr and module checkouts.
Online references support development research; runtime discovery stays offline and uses
the selected local version. Supplemental package profiles require verified pin tables;
macro syntax alone is insufficient capability evidence.

Prefer machine-readable tables already in the user's selected Zephyr/HAL checkout.
Use bounded parsers for known formats; do not execute arbitrary macros or scrape online
data at runtime. Do not turn supplemental hardware restrictions into a required manually
curated entry for every supported chip. Record source revision, encoding version, package coverage and
licensing for any redistributed data. A changed source format invalidates the affected
capability until understood; it does not justify guessing from a similar chip.

Nucleo F446RE and nRF52840 DK remain paired regression targets: fixed alternate
functions and Nordic peripheral-selected routing exercise different hardware models.
Complete the other three representative profiles before claiming the five-vendor stage.
Do not wait for every family, package, revision or board within a brand.

### GPIO semantics

The default GPIO action creates or edits a named application GPIO reference in
`/zephyr,user`, enables the provider when appropriate and preserves existing properties.
Generate a stable, collision-safe default name automatically; an optional friendly name
must not turn pin selection into a node/property naming form.
The inspector labels this **Application GPIO** and explains that application code sets
direction and runtime value. GPIO selection must not require a fictitious LED or sensor.
See [Zephyr application GPIO references](https://docs.zephyrproject.org/latest/build/dts/zephyr-user-node.html).

Startup input/output-high/output-low is a distinct GPIO-hog feature, with driver and
`CONFIG_GPIO_HOGS` requirements. Defer that mode from the initial pin-assignment stage;
do not present a direction selector that only writes an application reference. Zephyr
documents this distinction in its [GPIO guide](https://docs.zephyrproject.org/latest/hardware/peripherals/gpio.html).

## Automatic loading and source authority

Implement one feature-owned workspace coordinator using the existing shared job manager.
Opening or changing the application/configuration starts it automatically. Opening a
project is sufficient authorization for bounded read-only discovery. Application CMake,
Configure, builds, downloads and source writes remain separate explicit operations.
Record this change to the former manual-load boundary in an ADR before implementation.

The coordinator captures workspace, project/configuration revisions, target/core/revision,
roots, ordered input intent, tools and draft identity. Jobs run outside editor locks.
The UI immediately shows loading beside the chip area; one progress/error surface replaces
several discovery buttons. Bound subprocess work, support explicit cancellation, deduplicate
identical requests, and suppress superseded results. Leaving the feature disposes UI
listeners without cancelling shared jobs merely because the view closed.

Cache chip capabilities separately from draft-dependent assignments. Fingerprint the
source closure, absent higher-priority include candidates, bindings, data profiles and
tool identity. Revalidate before mutations and Save. Draft changes, Undo/Redo and source
edits trigger assignment refresh automatically after source synchronisation. Invalid
source retains the last view visibly marked stale and disables affected edits; it must
not silently restore old source or publish stale assignments as current.

Resolve an unambiguous existing overlay automatically from the selected configuration's
input order and default-overlay rules. Show the write destination persistently. When
the project lacks an overlay, automatically open its proposed exact-target destination
as an unsaved canonical draft. This workflow is implemented under
[ADR 0025](decisions/0025-new-overlay-save-and-project-inclusion.md). File creation occurs
only on Save, with no-replacement rename, directory/path and concurrent-change guards.
Missing parent directories are staged with the candidate. Durable recovery schema 2
records baseline existence and absent-destination identity, retaining schema-1 read
compatibility. An absent destination is not equivalent to an existing empty file.

For explicit replacement fragment lists, a new file is not automatically a build input.
The Save review must identify any required configuration registration, performed through
a narrow Projects-owned public interface. Keep file publication and configuration update
outcomes distinguishable; a failed second step leaves the saved file intact and offers a
retry. Do not claim build inclusion before it is established. A straightforward default
overlay must work without making the user visit another feature.
The public project-context provider remains read-only. The separate public
`ProjectOverlayProvider` now owns reviewed inclusion with expected before/after
project/configuration records, idempotent retry and durable outcome receipts. Recapture
and rebind the editor context after successful registration so later edits use the new
configuration identity. A fresh Save review finds pending inclusion after restart.

For multiple known literal overlays, observe their full order but write only the chosen
application overlay. Identify whether later inputs override a proposed assignment and
withhold misleading edits. If target or edit destination is genuinely ambiguous, keep
the chip visible and request one inline selection; resume automatically afterward.
Shields, snippets, opaque composition or unresolved revisions may remain unsupported in
this stage, with their exact reason. Never display a partial composition as authoritative.

## Capability and edit model

Use a compact shared model inside the Device Tree feature, with proposed modules such as
`chip_workspace.py`, `pin_catalog.py`, `pin_routes.py` and a small `pin_adapters/` package.
These are proposed responsibilities, not a requirement to build a general plugin framework.

| Record | Required facts |
| --- | --- |
| Chip identity | Exact SoC/core, package when known, selected board/target, provenance |
| Pin identity | Stable chip/port/index identity, display name, optional package pad and header annotations |
| Pin capability | Available signals/controllers, electrical restrictions, reserved uses, evidence and unsupported reasons |
| Assignment | Signal/controller, referenced pinctrl state, board/application source origin, enabled/disabled configuration and confidence |
| Resource constraint | Pin ownership, shared peripheral instance, state-specific use, GPIO consumer, protected board function |
| Edit authority | Current context/draft/revision/hash, observation fingerprint, selected destination and allowed operations |

Each adapter supplies pin inventory, function alternatives, current-assignment decoding,
constraint checks and overlay encoding. Generic discovery identifies controllers; the
adapter must prove each legal controller/signal/pin combination. Keep unavailable,
unconfigured, configured and unknown distinct. `status = "okay"` is source configuration,
not proof of runtime electrical activity. Incomplete allocation decoding limits edits
on affected resources; it need not hide independently understood pins.
Unknown custom-board wiring must not hide a known chip. Limit affected mutations where
pin ownership or restrictions cannot be established, with the reason beside that pin.

The server validates every mutation against current evidence, independent of the UI.
Use the existing canonical source engine to generate small application overrides,
required includes, pinctrl nodes/references, states and controller properties. Preserve
unrelated source bytes, comments, labels and manual declarations. Never serialize the
effective DTS back over the overlay or modify board/SoC/HAL files.

Default and sleep states must remain consistent where required. Clone shared/inherited
pinctrl groups when necessary instead of changing every consumer. For multi-signal
routes, validate signal completeness, electrical settings and controller-specific
properties as one transaction. ESP32 I2C bus-recovery GPIO properties, for example,
need adapter-specific handling rather than a generic pinmux-only replacement.
Preserve required companion references such as SPI `cs-gpios`, distinguishing GPIO
chip select from hardware-controlled select. Encode GPIO references using provider-local
indices: ESP32 GPIO34 is `gpio1` offset 2, not offset 34.

Show known conflicts with their source and consequence. Board LEDs, console UART,
debug/flash/clock pins and shared controller instances are not automatically available.
The implemented profiles block newly introduced conflicts and do not implicitly disable
other controllers. Source-backed GPIO reservations and evidenced NFC/reset/debug roles
are protected. Missing STM32 oscillator-pad evidence and Nordic low-frequency crystal
selection that depends on Kconfig remain visible notes rather than invented reservations.
Retaining an unchanged active pin/signal can preserve an existing
reservation or source conflict, with a visible notice; this does not establish that the
original configuration is conflict-free. Nordic package drive/frequency recommendations
appear for selected route pins as advice, separately from mux legality and ownership.
Some configurations declare mutually incompatible potential uses; do not infer which
driver actually operates from DTS alone. Offer reassignment only when all affected
changes can be represented and reviewed. Otherwise explain the limitation. Physical
board wiring cannot be removed by deleting a devicetree property.

Disabling or clearing a route must account for inherited defaults. Removing a local
override may restore the board assignment rather than make the pin unused. Present
**Restore board assignment** separately from **Disable controller**. Preserve unknown
consumers and shared groups; there is no automatic dependency deletion.

## Removal and reuse inventory

All paths below are relative to `src/zephyr_utilities/features/devicetree/`.

| Remove or replace | Coordinated changes |
| --- | --- |
| `board_connections.py` | Remove BME280/LED creation, `connection_options`, `BoardConnectionService` and `board_module_add`; first extract any necessary neutral overlay-targeting helper |
| `modules.py`, `recipes.py`, `layout.py` | Remove module recipes, placement commands and graph-coordinate service |
| `web/board.js`, `web/board-view.js`, `web/modules.js`, `web/module-view.js` | Replace controller/device forms and module graph with the chip workspace; remove recipe-based support notices |
| `web/view.html`, `web/index.js`, `web/style.css` | Replace screen orchestration, remove old panels and manual extraction controls, update descriptions in `feature.py` and `preview.py` |
| `api.py`, `drafts.py`, `board_topology.py` | Remove legacy service registrations, command dispatch and discovery's dependency on connection recipes; explicitly retire obsolete editor endpoints |
| Old PWM/ADC/CAN/interrupt/peripheral forms | Remove their authoring UI/registration and consumer-creation commands, including `peripheral_add`, `gpio_group_add`, `gpio_add` and `pwm_led_add`; extract only helpers needed to interpret/protect existing source |

Retain and adapt `service.py`, `board_selection.py`, `board_sources.py`, `board_worker.py`,
binding interpretation, `source.py`, `source_syntax.py`, draft history, recovery, Save and
publication guards. Reuse browser lifecycle/focus/feedback helpers. Inspect dependencies
before pruning: resource/removal services currently import peripheral, ADC, CAN and pin
helpers, and frontend resource tests import the module panel.

Delete obsolete recipe/layout tests after replacing their useful source, guard and
lifecycle coverage. Old module layout records remain inert; do not reinterpret their
coordinates as chip geometry. Existing sensor/LED declarations and old recoverable source
drafts are user data and remain intact. They may appear as existing assignments or
read-only consumers. Preserve Inspector's separate legacy aliases and Projects behavior.

Removal is part of delivery, not a permanent hidden legacy mode. Update runtime imports,
routes, commands, assets, tests and active documentation together. Historical handoffs
may remain explicitly historical so prior verification is not erased.

## Implementation sequence and exit gates

| Step | Deliverable | Gate |
| --- | --- | --- |
| 1. Confirm pin evidence and contracts | Small source-backed capability fixtures for F446RE and nRF52840; workspace/adapter contract and automatic-discovery ADR | Enumerate real unused alternatives as well as configured pins; distinguish package, GPIO and board exposure; no invented pins |
| 2. Replace the primary workspace | Automatic project/overlay resolution, cancellable discovery jobs, chip SVG, pin inspector and source drawer; remove old module/connection runtime | Opening either initial target, including an empty overlay, shows chip and assignments without extraction buttons; a project without an overlay still shows its chip and stages a safe destination |
| 3. Complete the first two editing profiles | GPIO application references and UART/I2C/SPI route commands, source round trip, conflict handling and new-file Save | Pin edits, Undo/Redo, Save/reopen and representative Configure checks pass for both fixed-mux and Nordic routing; no board files change |
| 4. Expand from source evidence | Automatic STM32 selected-table and Nordic GPIO/PSEL discovery; then other vendor adapters through the same view/contracts | New chips using understood formats require no manual chip entry; current assignments and generated routes remain source-checked. ESP32/K64/RP2040 are still separate acceptance gates |
| 5. Release and removal audit | Browser, package, lifecycle and compatibility verification; updated user documentation | No sensor/module creation controls, services or command paths remain; bundled offline app passes full checks |

These steps belong to one first-stage product outcome. A read-only diagram or one-board
demo is progress, not completion. Do not resume the abandoned module plan after step 2
or add advanced peripherals while basic pin assignment remains incomplete.

Current progress: automatic discovery, workspace replacement and runtime retirement are
implemented. Step 3's GPIO/UART/I2C/SPI commands, canonical Undo/Redo and reviewed Save
are implemented for both initial profiles, including safe absent-destination drafts,
publication and configuration inclusion. The subsequent automatic STM32/Nordic extension
removes the exact-chip inventory gates without changing canonical drafts or persistence.
The other three vendor adapters and full five-vendor matrix remain open. Current verification
is recorded in the automatic chip support handoff; earlier handoffs retain their original checks.

Use four parallel work areas after agreeing the contracts: workspace/backend coordinator;
SVG/inspector and UI lifecycle; adapter/overlay implementation; independent evidence and
acceptance tests. Assign disjoint files and an integration owner. Additional adapter work
can proceed in separate files once the first two profiles prove the shared interface.

## Acceptance scenarios and verification

- Open each representative exact target with an empty overlay: chip identity, pins and
  board assignments appear automatically. Missing environment/tools, missing overlay,
  unsupported composition and unknown package have distinct visible outcomes.
- Start from an existing UART/I2C/SPI assignment and from a disabled controller. Select
  a legal alternative, complete its required signals and apply. The overlay encodes
  precisely that route; no external device is fabricated.
- Select GPIO without an existing consumer. A named application GPIO reference appears;
  the UI does not claim a runtime direction or value was set.
- Reject illegal package pins, unsupported signals, shared-instance conflicts and stale
  observations through both UI and direct API calls. Failed edits leave source/history
  unchanged. Existing undecoded consumers cannot be silently displaced.
- Preserve unused `/omit-if-no-ref/` alternatives in the capability catalogue; test
  package holes such as absent PB11 on F446RE, Nordic shared peripheral blocks and
  ESP32 GPIO bank offsets/input-only restrictions.
- Exercise additional STM32 package tables and Nordic GPIO counts/ports without adding
  chip-specific catalogue entries. Ambiguous tables, changed encodings, missing port/count
  provenance and undecoded ownership must remain visible with unavailable actions.
- Exercise board consumers, shared pinctrl groups, sleep states, inherited-default
  restoration, manual source edits, duplicate Apply and source parse failures.
- Save and reopen an existing overlay and a newly created default overlay. Verify source
  inclusion evidence, concurrency failures, interrupted publication, recovery and a
  failed Projects configuration-registration step. No upstream source hash changes.
  Include concurrent configuration edits, lost registration responses and idempotent
  retries; distinguish an absent destination from an existing empty file in recovery.
- Switch projects/workspaces during slow discovery and edits, leave/reopen the feature,
  cancel and retry, change relevant source/tool files and invalidate the cache. Late
  results never acquire authority over a different draft.
- Test keyboard-only use, a scrolled page, 600 px and 390 px widths, and both initial
  loading and errors. Pin selection, Apply, Undo/Redo and Save preserve useful focus and
  scroll. Measure cold/warm discovery and ensure typing does not start repeated full scans.
- Retain source/history/recovery/Save/security regressions. Update existing tests that
  assert no jobs are submitted: read-only discovery jobs are now expected, firmware
  Configure/build jobs still require explicit action.

During implementation run focused tests, then `python scripts/check.py --quick`,
`python scripts/check.py` and `python scripts/verify_package.py` from the activated
development environment. Use real selected-Zephyr preprocessing and Configure for each
supported profile to check generated routes; include representative compilation where
driver assertions add validation. Record target, input hashes, tool versions and result.
Software checks do not establish electrical suitability or physical hardware operation.

The initial research established this design. The [workspace handoff](device-tree-editor-pin-workspace-handoff.md)
and [activation handoff](device-tree-editor-pin-activation-handoff.md) record their original
behavior and checks. The [new-overlay handoff](device-tree-editor-new-overlay-handoff.md)
records the subsequent first-Save extension. The five-profile gates remain open;
software validation does not establish physical hardware operation.
The [automatic chip support handoff](device-tree-editor-automatic-chip-support-handoff.md)
records the later STM32/Nordic expansion and distinguishes table parsing from full-board
editing, Configure/build and browser evidence.
