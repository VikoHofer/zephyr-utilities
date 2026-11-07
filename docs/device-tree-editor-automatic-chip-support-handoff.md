# Device Tree Editor: automatic STM32 and Nordic support

Date: 2026-11-07. This implements the user's request to expand STM32 and Nordic support
from the selected Zephyr DTS and pin tables without adding every chip manually. It
extends the [new-overlay workflow](device-tree-editor-new-overlay-handoff.md) under the
[chip and pin redesign](device-tree-editor-pin-workspace-plan.md). Earlier handoffs
retain their original exact-profile scope and verification.

The subsequent [board revision slice](device-tree-editor-board-revisions-handoff.md)
adds standard default/declared revision support. Revision limitations and matrix results
below describe this earlier slice before that implementation.
The later [nRF54L activation slice](device-tree-editor-nrf54l-handoff.md) also supersedes
the nRF54L15 activation blocker below for verified secure CPUAPP native-domain routes.
The nRF54H and non-secure/secondary-core restrictions remain separate.

## Delivered behavior

Opening a project continues to load its chip, current assignments and existing or
new-overlay draft automatically. There are no additional extraction controls or runtime
network lookups. The discovery path uses the selected local Zephyr version, module/custom
roots, board/SoC source closure and matched bindings. A new chip using an understood
source format does not require a new catalogue entry.

Binding discovery uses PyYAML's safe C loader when it is installed, with the Python safe
loader as its fallback. Both paths retain the same input limits and parsing guards.
This reduces automatic loading time without a cache or a change to persisted data.

STM32 discovery locates one included HAL package `stm32*-pinctrl.dtsi` table, validates
the `STM32_PINMUX` encoding and mode constants, and retains unused alternatives omitted
from the effective tree by `/omit-if-no-ref/`. The package table supplies the available
pin/function combinations; the selected target and table supply display identity. Two
package tables, incomplete expressions or changed encodings do not produce a merged or
partly guessed inventory. The route adapter uses the selected table and observed
controller-compatible variants for UART, I2C and SPI.

Nordic discovery derives logical GPIO slots from matched `nordic,nrf-gpio` providers,
explicit port numbers, resolved `ngpios` or its verified binding default, and reserved
ranges. It supports additional ports and differing counts without a hardcoded 48-pin
inventory. Source package metadata is shown only when an unambiguous included wrapper
establishes it. A logical GPIO slot is not a guarantee of a bonded package pad, board
header connection or cross-core ownership. The UI distinguishes this inventory from
STM32 package-table alternatives. Larger inventories expand the diagram and its zoom
range so pin labels keep their spacing instead of overlapping around the original body.

The Nordic adapter validates the selected `NRF_PSEL` format, GPIO providers, peripheral
signals, controller instance sharing, understood default/sleep properties and routing
ownership. The existing nRF51/nRF52/nRF53/nRF91 primary-core architecture can use this
shared path when its selected sources satisfy those checks. No per-chip GPIO inventory
is added. Reserved GPIOs and evidenced NFC/reset/debug roles remain protected, while
clock uncertainty is visible. Inherited assignments and other consumers are not silently
displaced.

Apply still commits one canonical overlay command with source-preserving updates and
one Undo entry. Source editing, Undo/Redo, recovery, guarded existing-file Save and
no-replacement first Save use the established paths. Save includes a newly created
overlay through Projects when needed. No board, SoC, HAL, Kconfig or firmware source is
rewritten by discovery or pin activation.

## Explicit limits

- STM32F1 remap pinmux is a different encoding and has a specific unavailable reason.
  Understood table encoding does not imply support for every controller state or
  electrical property. Special H5/N6 state properties and remapped pins remain guarded.
- Declared GPIO reservations must decode completely and fit the observed controller
  count or understood slot bound. A malformed reservation on a mapped MCU provider
  makes its inventory unavailable; an unrelated GPIO expander does not invalidate it.
- STM32 HAL tables often omit oscillator pad identities. The editor protects evidenced
  debug/oscillator pins, retains the earlier verified F446 package clock protection,
  and exposes missing clock-pad evidence rather than applying F446 pad locations to
  other packages. Check board clock wiring before assigning a physical pin.
- Nordic low-frequency crystal pins carry a note because their use depends on Kconfig
  clock selection, which the editor does not resolve or change. That note is not a hard
  reservation or evidence that the physical pin is free.
- Nordic nRF54 dedicated/cross-domain routes, UICR ownership models, non-secure targets
  and secondary network/radio/FLPR/PPR cores can have visible GPIO inventories while activation stays
  unavailable. Their selected DTS does not establish all routing or forwarding authority.
  Unknown ownership/security declarations and unsupported architectures are not treated
  as ordinary nRF52 routes.
- A Nordic peripheral without a writable source label remains unavailable, but the
  current adapter can omit its specific label-related reason from the controller list.
  Its read-only function evidence remains visible. Standard labeled Zephyr controllers
  are unaffected; explicit reporting for this custom-source case remains to be improved.
- Existing source-composition limits still apply before chip parsing. Board revisions,
  board extensions and unsupported application composition need separate discovery
  adapters. In the local source matrix, `nrf9160dk/nrf9160` stops at its declared default
  board revision with "Board extensions and revision selection require a separate
  discovery adapter". The local `nrf9161dk` board also declares a default revision, but
  was not matrix-tested. Synthetic nRF91 routing tests do not establish that these stock
  board targets can open through the current discovery path.
- Pin functions depend on the current controller, source states and resource conflicts.
  An enabled capability is a source-generation result, not proof of firmware execution
  or electrical suitability. Physical package pad numbers and layout remain unknown.
- Espressif, NXP and Raspberry Pi adapters, advanced peripherals and external-device
  composition are unchanged. The old sensor/module workflow remains removed.

## Implementation boundaries

`chip_catalog.py` supplies dynamic `family`, SoC, source-profile and inventory metadata.
The activation dispatcher selects the family adapter; each adapter rechecks current
source evidence and accepts only its understood routing format. No new persistent schema,
public cross-feature operation, external service or dependency direction is introduced.
Existing job cancellation, source fingerprints, canonical draft guards and stale-response
handling remain authoritative. Source format or capability changes invalidate affected
mutations instead of being interpreted approximately.

The source fingerprint also covers board-platform metadata that is not selected DTS.
Nordic filename-based security/core checks therefore inspect only DTS/DTSI files.
An unselected non-secure platform YAML record no longer blocks a secure CPUAPP target;
actual non-secure and secondary-core source selections retain their ownership checks.

The selected local files are the runtime authority. The development reference describes
[STM32 pinctrl and remap properties](https://docs.zephyrproject.org/latest/build/dts/api/bindings/pinctrl/st,stm32-pinctrl.html),
[Nordic GPIO port/count/reservation semantics](https://docs.zephyrproject.org/latest/build/dts/api/bindings/gpio/nordic,nrf-gpio.html)
and the [Nordic pinctrl/PSEL group format](https://docs.zephyrproject.org/latest/build/dts/api/bindings/pinctrl/nordic,nrf-pinctrl.html).
The [Nordic UICR binding](https://docs.zephyrproject.org/latest/build/dts/api/bindings/arm/nordic%2Cnrf-uicr.html)
documents model-dependent NFC/reset configuration, and Nordic describes the
[nRF54L dedicated and cross-domain pin constraints](https://nrfconnectdocs.nordicsemi.com/ncs/latest/nrf/app_dev/device_guides/nrf54l/pinmap.html).
These references do not replace the source checks or add a runtime internet dependency.

## Verification

- The 25 catalogue tests passed, including additional STM32 families, Nordic port/count
  variants and port 2, optional package metadata, reserved holes, ambiguous tables,
  incomplete expressions, changed mode constants, malformed/out-of-range mapped GPIO
  reservations, unrelated expanders and missing binding provenance.
- The STM32 adapter's 25 focused tests passed. A table-level audit read one actual local
  package table from each of F0, F2, F3, F4, F7, G0, G4, H5, H7, L0, L1, L4, L5, U0,
  U3, U5, WB, WBA, WL, C0 and N6 with the actual `stm32-pinctrl.h`. All 21 parsed and
  supplied valid unreserved UART and SPI route plans using synthetic resolved controller
  fixtures. Twenty supplied I2C1; the selected N6 `stm32n655a0hxq` package lacked a
  verified I2C1 SCL route and remained unavailable. This is table/route evidence, not
  21 complete board builds or a guarantee covering every package in those families.
- The Nordic adapter's 28 tests and 21 shared activation tests passed. Synthetic selected
  source fixtures cover nRF51822, nRF52810, nRF52832, nRF52833, nRF5340 CPUAPP, nRF9160
  and nRF9161 UART/I2C/SPI plans, reserved/forwarded GPIOs and shared peripheral instances
  with 64-bit addresses. Non-secure and secondary-core targets, UICR-v2 and unsupported
  domain routing retain explicit unavailable results. These are adapter tests, not
  firmware builds for every listed chip. The additional regression distinguishes
  unselected platform YAML metadata from selected DTS ownership evidence.
- All 52 frontend chip tests passed, including logical GPIO-slot wording, 144/256-pin
  diagram spacing and preserved focus/refresh/fit behavior. Browser verification remains
  separate from these automated frontend checks.
- Final `python scripts/check.py` passed after the loader and metadata-gate fixes: 1,447 Python tests,
  513 frontend tests, lint, syntax and architecture checks. Loopback integration tests
  were included.
- Final `python scripts/verify_package.py` passed: the installed wheel's imports, console
  entry point and runtime assets were verified.
- The binding loader's 51 focused parser/catalogue/service tests passed, including
  explicit guard parity with the Python fallback. Parsing the same actual 3,725-file
  local Zephyr binding catalogue took 11.914 seconds before and 1.858 seconds after in
  an unprofiled measurement, approximately 6.4 times faster. Source fingerprints and
  parsed-content SHA-256 were identical, with no diagnostics. This measures catalogue
  parsing, not the entire project-opening workflow.
- Browser checks on STM32H743 automatically displayed 114 pins and covered GPIO Apply,
  Undo/Redo, reviewed first creation and Save, leaving/reopening, search and zoom. At
  390 px width there was no horizontal page overflow. Nordic nRF52832 displayed 32 GPIO
  slots with the correct inventory label; UART routing preserved existing default/sleep
  companion signals and passed reviewed first creation and Save. No JavaScript errors
  were observed. After restarting with the final backend, nRF54L15 showed 35 logical
  slots, a disabled function selector and the correct dedicated/cross-domain reason.
  STM32F103 automatically displayed its explicit STM32F1 remap-adapter limitation without
  inferred pins. Final browser warning/error logs were empty. A screenshot was captured
  outside the repository; the temporary browser/server were closed and the viewport reset.

### Real-source target matrix

All 17 selected targets completed without test errors: 12 editable targets reached saved
overlays and five retained specific unavailable outcomes. Counts below mean STM32 logical
pins present in the selected package table or Nordic GPIO controller slots, as labeled;
they are not physical package pin totals. Changed commands exercised Undo/Redo before Save.

| Exact target | Observed inventory | Editing outcome | Configure/build |
| --- | --- | --- | --- |
| `nucleo_f446re/stm32f446xx` | 50 table pins | Saved GPIO/UART/SPI changes; I2C retained as a no-op | Not run in this matrix |
| `nucleo_f401re/stm32f401xe` | 50 table pins | Saved GPIO/UART/I2C/SPI | Passed |
| `nucleo_f411re/stm32f411xe` | 50 table pins | Saved GPIO/UART/I2C/SPI | Not run in this matrix |
| `nucleo_g071rb/stm32g071xx` | 60 table pins | Saved/reopened GPIO/LPUART/I2C/SPI | Passed |
| `nucleo_l476rg/stm32l476xx` | 51 table pins | Saved/reopened GPIO/USART/I2C; SPI retained as a no-op | Passed |
| `nucleo_h743zi/stm32h743xx` | 114 table pins | Saved GPIO/UART/I2C/SPI | Passed |
| `nucleo_u575zi_q/stm32u575xx` | 111 table pins | Saved GPIO/UART/I2C/SPI | Passed |
| `nrf51dk/nrf51822` | 32 GPIO slots | Saved GPIO/UART/I2C/SPI | Passed |
| `nrf52dk/nrf52832` | 32 GPIO slots | Saved GPIO/UART/I2C/SPI | Passed |
| `nrf52833dk/nrf52833` | 42 GPIO slots | Saved GPIO/UART/I2C/SPI | Passed |
| `nrf52840dk/nrf52840` | 48 GPIO slots | Saved GPIO/UART/I2C/SPI | Not run in this matrix |
| `nrf5340dk/nrf5340/cpuapp` | 48 GPIO slots | Saved GPIO/UART/I2C/SPI | Passed |
| `nucleo_f103rb/stm32f103xb` | Unavailable | STM32F1 remap encoding needs its own adapter | Not applicable |
| `nrf5340dk/nrf5340/cpunet` | 48 GPIO slots | Controlling-core ownership/forwarding evidence unavailable | Not applicable |
| `nrf54l15dk/nrf54l15/cpuapp` | 35 GPIO slots | Dedicated/cross-domain routing not decoded | Not applicable |
| `nrf54h20dk/nrf54h20/cpuapp` | Unavailable | Board revision/extension discovery unsupported | Not applicable |
| `nrf9160dk/nrf9160` | Unavailable | Board revision/extension discovery unsupported | Not applicable |

The final G071/L476 pass reopened overlays already published by the earlier successful
assignment/Undo/Redo/Save pass; it made no additional changes. Retained F446 I2C and L476
SPI routes were no-ops, not newly generated route edits. The other changed actions,
including all four H743/U575 and Nordic actions, completed through canonical draft commands.

All nine attempted Configure/build pairs passed. Each used its persisted project's
command recipe, selected the generated overlay through the saved configuration, emitted
`zephyr.elf` and preserved source hashes. No ad hoc driver/Kconfig or overlay overrides
were added for these builds. The blocked targets were not built through a bypass.
Temporary test applications and build evidence were retained outside the repository;
the Zephyr checkout remained clean and the final repository whitespace check passed.

No physical hardware operation has been validated.
