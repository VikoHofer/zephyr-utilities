# Device Tree Editor MVP support handoff

**Historical handoff:** the next-step direction below was replaced on 2026-11-07 by
the [chip and pin redesign](device-tree-editor-pin-workspace-plan.md). Its recorded
implementation/check results remain historical evidence; module integration is no
longer the next feature.

Date: 2026-11-06. The qualified-target continuation is committed as `cc32fb1`.
This slice implements the user's smaller MVP scope and visible support blockers.

## Accepted scope and next step

Focus development and representative testing on ST/STM32, Nordic, Espressif, NXP and
Raspberry Pi. This is a delivery priority, not a blanket support guarantee for every
product or a vendor whitelist. Generic source/binding discovery and actual connection
capabilities remain authoritative, including for custom boards and other vendors.

The next feature is the integrated visual workspace: persistent context/actions,
module library and device tree, connection canvas, selection-driven inspector, and
source/diff below. Keep the basic add/connect workflow usable while making these
panels coherent. Add pin-routing depth and common modules as concrete MVP workflows
need them. Exhaustive revisions, extensions, composition and remaining D11 depth are
deferred; follow the [controller-first plan](device-tree-editor-controller-first-plan.md).

## Implemented behavior

- Unsupported controller/configuration observations retain board identity and show:
  “Device Tree Editor is under construction. More controller support will be added
  over time.” The concrete reason and retry/Projects path remain visible.
- Unavailable recipe buttons are disabled. Hardware with no addable BME280/LED
  connection remains inspectable and explains the limitation. A blocked individual
  connection does not disable independently usable connections.
- Unsuccessful discovery no longer announces successful inspection. A single banner
  contains the construction explanation; busy, canceled, stale and error feedback
  remain separate. Offscreen results use the existing notification without scrolling.
- Context changes and late responses cannot carry a blocker or placement authorization
  into another draft. Missing setup remains distinct from observed support limits.
- Existing server checks reject unsupported direct commands. New HTTP regressions
  verify canonical draft/redo history and source bytes remain unchanged on rejection.
  Source editing, Undo/Redo, reviewed Save and recovery remain available.

No backend schema, persistence format or support allowlist was introduced.

## Verification

- Quick checks passed before the final warning-text deduplication: 1,266 Python tests
  and the frontend quick runner. Final complete checks passed afterwards: **1,295
  Python tests and 618 frontend tests**, including lint, syntax, architecture and
  loopback integration checks. The focused board UI suite passed 33 tests.
- Installed-wheel verification passed: imports, console entry point and runtime assets.
- Browser used isolated registered applications and actual local metadata/DTS/bindings:
  `nrf54h20dk/nrf54h20/cpuapp` correctly blocks its currently unsupported revision
  selection, retains the source and offers Projects/retry. No false completion text.
  Leaving for Projects and reopening the editor worked.
- `esp32_devkitc/esp32/procpu` shows the unavailable GPIO reason while its I2C BME280
  form works. Adding at `0x76` updated the canonical draft and connected-device card;
  Undo restored the empty overlay draft. Nothing was saved to the project.
- Switching to `nucleo_f446re/stm32f446xx` cleared the blocker and enabled BME280 and
  LED actions. No browser warning/error logs were observed.
- Retrying from a scrolled page preserved focus and scroll exactly at both 1280 px
  and 390 px widths; neither layout overflowed horizontally. Narrow offscreen
  feedback was visible as a notification. The viewport override was restored.
- Frontend cases cover no-addable topology, same-kind partial support, custom vendors,
  supported/unsupported transitions, passive rendering and late responses. HTTP cases
  reject blocked GPIO while independently supported I2C succeeds.

Logs: `/tmp/zephyr-controller-mvp-quick.log`, `/tmp/zephyr-controller-mvp-full.log`,
`/tmp/zephyr-controller-mvp-package.log`. Browser proof: `device-tree-mvp-blocker.png`
in this chat's visualization folder. Temporary fixtures were outside tracked sources.

These are software checks. This slice adds no controller adapter, general pin rewiring,
Configure/build result or physical hardware validation. The final integrated visual
workspace is the next implementation, not a completed feature of this slice.
