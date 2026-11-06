# Controller-first editor handoff

Date: 2026-11-06. First bounded slice implemented and verified; broader controller,
pin-routing and integrated visual-workspace gates remain open. This delivery includes
the previously verified D11d work alongside the controller-first foundation.

## Accepted direction

The user's priority is broad controller discovery and a beginner connection workflow,
before further advanced D11 families. The reported board is Nucleo F446RE. The revised
[plan](device-tree-editor-controller-first-plan.md), master plan, roadmap and agent guide
record this order. [ADR 0023](decisions/0023-board-hardware-observation.md) records the
new explicit preprocessing boundary and public Projects context extension.

## Implemented behavior

- Selected-board discovery starts with local board metadata, DTS/SoC include closure,
  selected module/custom roots and bindings. An empty application overlay and missing
  `led0` aliases do not hide controllers. Disabled hardware remains visible.
- A read-only effective observation has stable path identities, source origins,
  binding/bus/GPIO evidence, existing devices, connector maps and known pin allocations.
  Unsupported inputs return a board card and explanation.
- BME280 connections create a child under an inherited I2C controller, creating its
  overlay reference block when needed. The current board routing is reused. Enabling
  a disabled controller requires an explicit choice.
- LED connections create/reuse an application-owned `gpio-leds` group and a GPIO
  consumer. GPIO index and polarity are explicit. Known uses and reserved pins are
  excluded; connector mappings alone do not occupy GPIOs.
- Commands check inherited and local conflicts, bindings, source contributors and
  observation freshness. They preserve handwritten overlay bytes, never modify board
  files, and create one canonical Undo/Redo action. Save remains a separate review.
- The hardware workspace and live overlay source precede collapsed Advanced overlay
  tools. All hardware restores the controller list after recipe filtering. Repeated
  recipe/controller clicks give nearby/offscreen feedback without resetting staged
  fields. The legacy Place module action also gives visible guidance.

Discovery uses fixed GNU preprocessing arguments and staged copies of the selected
Zephyr `dtlib` Python sources. It does not execute CMake, Configure, builds or firmware.
Input closure, tool output, runtime, process memory and retained observations are bounded.
Literal includes and provenance are checked before execution; file probes, unsafe
pragmas, binary includes and unsupported source composition are rejected. Selected
tool/parser code is executable tooling, not sandboxed data.

## Verification

Run from the repository development environment:

| Check | Result |
| --- | --- |
| `python scripts/check.py --quick` | Passed during implementation: 1,229 Python tests and all 39 frontend test files |
| `python scripts/check.py` | Passed on final production code: 1,257 Python tests and 610 frontend tests, including lint, syntax, architecture and HTTP checks |
| `python scripts/verify_package.py` | Passed: installed wheel imports, launcher and runtime assets |
| Focused board tests | Discovery/context guards, source preservation, conflicts, command/Undo, authenticated HTTP and UI lifecycle/feedback passed |
| Browser | Nucleo discovery, sensor/LED addition, Undo/Redo, reviewed Save, navigation and fresh reload passed |

Full checks used approved local-loopback execution. No physical hardware was exercised;
Configure/build were not run for the new board workflow.

Actual local source observations used the same discovery implementation:

| Exact target | Effective nodes | Binding bus controllers | GPIO providers | Connector entries |
| --- | ---: | ---: | ---: | ---: |
| `nucleo_f446re/stm32f446xx` | 151 | 25 | 8 | 72 |
| `nrf52840dk/nrf52840` | 139 | 11 | 2 | 22 |
| `frdm_k64f/mk64f12` | 118 | 16 | 5 | 22 |

These counts describe blank-overlay observations, not physical package pin counts or
supported-board totals. A second Nucleo service smoke added a BME280 on I2C2 and an LED
on GPIOA1, verified exact Undo/Redo and left saved source unchanged.

The browser used an isolated registered Nucleo application through the normal Projects
context provider and real local Zephyr/module sources. It added BME280 on I2C1 address
0x76 and LED on GPIOB0, saved the generated overlay, left/reopened the feature and
reconstructed both devices after a server/page restart. Original overlay comments were
retained. Board/SoC sources and user registrations were not changed.

The LED action stayed at scroll position 1109 before submission and after completion.
At 600×800, the page had no horizontal overflow; missing pin choices produced nearby
feedback and kept focus on the action. Directory-only context retained a visible board
setup explanation. Repeated recipe selection retained a staged address; All hardware
restored UART cards. Browser warnings/errors were empty. Late/stale responses and passive
notification suppression are covered by the frontend suite.

Local verification evidence:

- `/tmp/zephyr-controller-first-quick.log`
- `/tmp/zephyr-controller-first-full.log`
- `/tmp/zephyr-controller-first-package.log`
- `/tmp/zephyr-board-nucleo-connection-smoke.json`
- `/tmp/zephyr-board-family-smoke.json`
- `/tmp/zephyr-board-family-final-smoke.json`
- Browser image: `device-tree-controller-first.png` in this chat's visualization folder.

## Remaining work in priority order

1. Extend the shared discovery path to board revisions/extensions, multi-part qualifiers,
   overlays, shields/snippets and additional preprocessor cases. Current discovery needs
   one exact SoC qualifier, unambiguous HWMv2 source, one selected overlay and recorded
   Python/GNU tools. Unsupported cases stay visible with reasons.
2. Add verified family pin-routing and board/header mappings. Current STM32 allocation
   decoding is tied to the observed header encoding; other routing families remain
   unknown, so GPIO edits can be withheld while board discovery and independently
   supported I2C connections stay available. Logical `ngpios` defaults are not proof
   that every slot is bonded or accessible. Arbitrary bus-pin reassignment is not implemented.
3. Build the integrated canvas/inspector/module-library workspace toward the reference
   design. This slice has controller/device cards and connection forms, not drag-to-wire
   pins, a board drawing or the final three-panel editor. Source follows below.
4. Expand common modules one complete connection workflow at a time, including software
   configuration evidence. Adding a DTS node alone does not establish driver readiness
   or functioning electrical wiring.
5. Resume advanced D11 and later composition/release stages after the basic controller
   and visual connection gates are met. Do not return to a board-specific feature-first
   delivery order.
