# Controller-first qualified-target handoff

Date: 2026-11-06. Implemented and verified; delivered as the qualified-target continuation.
Committed as `cc32fb1`.
The previous foundation, priority revision, visual connections and retained D11d work
were committed as `a0fa84b` at the user's request before this continuation.

## User-visible result

Load board hardware now resolves targets with CPU/core qualifiers and nested variants.
The first slice rejected these even when Projects had discovered them. Examples include
`nrf5340dk/nrf5340/cpuapp/ns`, `esp32_devkitc/esp32/procpu` and
`rpi_pico/rp2040/w/mcuboot`. Resolution is generic; no board-name allowlist was added.
The source and target architecture are recorded in Discovery details.

Current board/SoC declarations must reproduce the captured qualifier set. Removed cores,
renamed variants, unknown metadata fields, ambiguous source filenames or stale platform
metadata produce a visible reason before preprocessing. A missing exact source never
falls back to another core or a parent variant. The single-SoC shortened filename drops
only the SoC segment; mixed full/short forms and shortened multi-SoC files are rejected.

## Implementation and boundaries

- `core/hardware_metadata.py` contains the bounded data-only YAML, SoC and qualifier
  helpers shared by Projects discovery and the editor. Each feature owns source reads,
  provenance and freshness. No private cross-feature imports were added.
- `board_selection.py` reads the current recorded metadata closure, merges captured
  SoC CPU-cluster extensions, checks exact target identity and selects the DTS filename.
  Board extensions and revisions remain outside this slice.
- Platform YAML uses the selected target's architecture. A fallback requires exactly
  one live board-wide architecture matching captured evidence. Other cores' include
  paths are not combined. Platform listing/content changes invalidate authority.
- Existing draft guards, bounded preprocessing, canonical source commands, Save and
  Configure boundaries remain in effect. Invalid selection performs no tool execution
  or source mutation. The new `selection` observation is additive and not persisted.
- The hardware analysis adapter is now `hwm-v2-metadata/2`: qualifier limits cover bare
  CPU-cluster expansion too. Explicit analysis invalidates the old cache; saved profile
  and observation schemas are unchanged and no migration runs on opening.

This extends controller discovery, not pinmux encoding, electrical validation, driver
configuration or multi-image editing. One selected overlay remains required.

## Verification

| Check | Result |
| --- | --- |
| Focused shared helpers | 8 tests passed |
| Focused target selection | 29 tests passed, including stale/ambiguous/malformed input, extensions and architecture cases |
| Existing Projects hardware/scaffold | 20 + 24 focused tests passed |
| `python scripts/check.py --quick` | 1,266 Python tests and all 39 frontend test files passed |
| `python scripts/check.py` | 1,294 Python tests, 610 frontend tests, lint, syntax, architecture and HTTP checks passed |
| `python scripts/verify_package.py` | Installed wheel imports, launcher and assets passed |
| Real-source discovery | All ten targets below passed on final production code |
| Browser | ESP32 processor-qualified project discovery, sensor connection, Undo/Redo, reviewed Save and reopen passed |

The HTTP suite used approved loopback access. No Configure, build, flash or physical
hardware validation ran for this slice.

Actual local Zephyr, module and binding observations:

| Exact target | Nodes | Bus controllers | GPIO providers |
| --- | ---: | ---: | ---: |
| `nucleo_f446re/stm32f446xx` | 151 | 25 | 8 |
| `nrf5340dk/nrf5340/cpuapp` | 147 | 16 | 2 |
| `nrf5340dk/nrf5340/cpunet` | 84 | 3 | 2 |
| `nrf5340dk/nrf5340/cpuapp/ns` | 147 | 16 | 2 |
| `esp32_devkitc/esp32/procpu` | 107 | 12 | 2 |
| `esp32_devkitc/esp32/appcpu` | 84 | 12 | 2 |
| `rpi_pico/rp2040` | 77 | 8 | 1 |
| `rpi_pico/rp2040/w` | 82 | 9 | 2 |
| `rpi_pico/rp2040/w/mcuboot` | 85 | 9 | 2 |
| `rpi_pico/rp2040/mcuboot` | 80 | 8 | 1 |

Counts describe empty-overlay effective observations, not physical pins or universal
support. Metadata came from the real Projects discovery path and module declarations.
The smoke used in-memory empty drafts and left saved board/SoC/application sources alone.

The browser used an isolated registered `esp32_devkitc/esp32/procpu` application with
normal Projects context capture. BME280 at I2C0 address 0x76 generated a scoped `&i2c0`
node, appeared under its connection, and retained original comments. One Undo removed
it; Redo restored it. Reviewed Save wrote the test overlay. Leaving/reopening the editor
retained the source. The add action remained at scroll position 1207 through completion.
At 600×800, expanded Discovery details showed the selected Xtensa architecture and full
source path without horizontal page overflow. Browser warnings/errors were empty.

Evidence files are `/tmp/zephyr-controller-target-{quick,full,package,real}.log`,
`/tmp/zephyr-board-selection-tests.log` and `/tmp/zephyr-qualified-discovery-results.json`.
The browser screenshot is `device-tree-qualified-target.png` in this chat's visualization
folder. These local fixtures/logs are not repository artifacts.

## Next steps

The user's subsequent MVP revision supersedes the original discovery-expansion queue.
Focus on ST/STM32, Nordic, Espressif, NXP and Raspberry Pi, show clear blockers for
unsupported controllers/connections, then deliver the integrated module library,
canvas and inspector. Revisions/extensions, broader composition and deeper D11 work
are later targeted additions. See the [active plan](device-tree-editor-controller-first-plan.md).
