# Device Tree Editor: pin activation

Date: 2026-11-07. This extends the [automatic chip workspace](device-tree-editor-pin-workspace-handoff.md)
under the [chip and pin redesign](device-tree-editor-pin-workspace-plan.md).
This handoff preserves the original existing-overlay activation scope and its checks.
The subsequent [new-overlay handoff](device-tree-editor-new-overlay-handoff.md) adds
automatic absent-destination drafts, first Save, recovery and configuration inclusion.

## Delivered behavior

Selecting a pin now offers supported Application GPIO, UART, I2C and SPI assignments for
the exact STM32F446 R(C/E)Tx source profile and Nordic nRF52840 QIAA. The reference targets
are `nucleo_f446re/stm32f446xx` and `nrf52840dk/nrf52840`. Capabilities depend on the selected
local chip/package sources and current configuration; these are not whole-brand guarantees.
Automatic project loading, discovery, source synchronisation and assignment refresh remain.

Choose a function, review its required companion signals, then Apply. Valid existing
companion pins are prefilled; incomplete routes require an explicit choice. UART flow
control and STM32 hardware NSS companions appear when the profile requires them.
Cancel changes no source. Source/context changes invalidate an unfinished proposal,
and late responses cannot publish success for a different draft. Apply feedback stays
beside its controls, with keyboard focus preserved during ordinary updates.

One Apply generates one canonical source/history change in the existing application
overlay. Source preview, Undo/Redo, recovery and Review and Save use the same draft.
An already configured assignment is a no-op. Save publishes only the reviewed overlay;
it does not run Configure, builds or firmware.

Application GPIO creates a collision-safe named reference in `/zephyr,user` and enables
its understood GPIO provider when needed. Application code sets direction and runtime
value. Peripheral assignments enable the controller and generate its complete pin route.
Required includes and fresh pinctrl labels are generated, understood default/sleep
settings are preserved or completed, and inherited/shared pin groups are not rewritten.
SPI preserves existing GPIO chip selects and creates no external device. Verified
one-level GPIO connector maps participate in ownership checks, including Nordic
Arduino D10's SPI3 chip select on P1.12. Unknown or chained maps withhold activation.
Unrelated
overlay declarations, comments and board/SoC/HAL files remain unchanged.

New conflicts with active pin consumers, reservations or shared peripheral hardware are
blocked with a concrete reason. Other controllers are never disabled implicitly. An
unchanged active pin/signal can retain an existing reservation or source conflict, with
a notice that board wiring is unchanged. Package drive/frequency recommendations are
shown for the chosen pins as advice, separately from route legality. Unknown encodings,
ambiguous settings and incomplete source evidence withhold affected edits.

The inspector shows `CONFIG_GPIO`, `CONFIG_SERIAL`, `CONFIG_I2C` or `CONFIG_SPI`
requirements as appropriate. Neither Kconfig nor application code is changed. An enabled
devicetree route does not establish runtime electrical activity or hardware suitability.

## Implementation boundaries

`chip_activation.py` adds activation capabilities to each current chip observation and
dispatches `chip_gpio` and `chip_assign` through the existing draft command endpoint.
`chip_stm32.py` and `chip_nordic.py` validate exact-profile routes; `chip_source.py` applies
small source edits. `chip_gpio.py` resolves direct and verified connector GPIO references.
Changed candidates are preprocessed and their resulting routes are
verified before the canonical commit. Observation, workspace/context, draft revision/hash,
source and tool guards are rechecked before mutation. Rejected commands leave history
unchanged. No new server, persistence format or cross-feature write interface is added.

`web/chip-assignment.js` keeps proposals local until Apply and uses the existing draft
session. It supports required companion controls, unavailable reasons, cancellation,
busy/error feedback and deduplicated pin recommendations without a second save/history
model. The old sensor/module workflow remains removed; saved declarations remain user data.

## Verification

- `python scripts/check.py` passed: 1,347 Python tests and 490 frontend tests, plus
  lint, syntax and architecture checks. Quick checks also passed during implementation.
  Cases include incomplete routes, no-op, cancellation, stale source/context, concurrent
  edits, rejected candidates, connector chip selects, keyboard focus and pin advice.
- `python scripts/verify_package.py` and `git diff --check` passed.
- Real local-source smoke checks passed GPIO/UART/I2C/SPI for each reference target:
  eight flows exercised Apply, Undo, Redo, no-op and reviewed existing-overlay Save.
- Configure and full builds passed for both reference targets using local Zephyr 4.4.2
  and SDK 1.0.1, with no warnings/errors and both ELF outputs produced. The corrected
  Nordic SPI2 route uses P1.11/P1.04/P1.00; SPI3 retains its connector chip select on P1.12.
- Browser checks passed automatic loading, GPIO/UART/I2C/SPI forms and Apply, cancellation,
  protected/unavailable choices, Undo/Redo, reviewed Save, retained drafts on feature
  leave/reopen and 390/600-pixel layouts. Apply and exhausted Undo/Redo preserve focus.
  Browser Save results were also checked on disk. Test applications and build outputs
  were isolated; the upstream Zephyr checkout remained clean.

No physical hardware operation has been validated.

## Remaining gates

- The missing-overlay gate from this increment is completed by the subsequent
  [new-overlay handoff](device-tree-editor-new-overlay-handoff.md), with its own evidence.
- ESP32, K64 and RP2040 profiles and the full five-profile acceptance matrix remain open.
  Other STM32/Nordic chips, unsupported source shapes and broader composition are not
  implied by the implemented profiles.
- Advanced peripheral authoring, external-device composition and GPIO startup hog modes
  remain outside this increment. Do not restore sensor/module recipes or placement UI.
