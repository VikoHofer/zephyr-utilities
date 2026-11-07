# Device Tree Editor: nRF54L pin activation

Date: 2026-11-07. This slice follows automatic chip discovery and board revision
support, committed as `b0ff964` and `956f9b5`. The acceptance target is
`nrf54l15dk/nrf54l15/cpuapp`.

## Behavior and boundaries

Opening a project uses the existing automatic DTS, binding and GPIO discovery path.
The nRF54L adapter recognizes the selected shared 05/10/15 architecture and verifies
its secure CPUAPP peripheral address space and GPIO register layout. It does not add
a board-specific pin inventory or require a new extraction action.

GPIO references and complete UART, I2C and SPI routes use the existing canonical
overlay commands, source guards, Undo/Redo, reviewed Save and recovery. Existing
overlays and automatically staged missing-overlay drafts use the same paths.
Upstream board, SoC and HAL sources remain unchanged.

Controller register identity determines the native routing port: serial 20/21/22
use P1, serial 30 uses P0, and serial 00 uses P2. SPI SCK and I2C SCL choices are
restricted to clock-capable pins; SPI00 and UART00 also have dedicated signal choices.
These hardware rules are separate from the source-derived GPIO inventory. Existing
consumers, shared peripheral instances, source reservations and dedicated functions
continue to restrict otherwise legal routes.

Cross-domain routing, non-secure images, secondary-core configurations and other
nRF54 architectures remain unavailable when their ownership or routing requirements
are not established. Cross-domain operation requires firmware power-mode management
that an overlay alone does not provide. This slice does not implement that management.
An active FLPR coprocessor, translated/unknown register layout and UICR domain ownership
remain guarded. SPI00 devices above 8 MHz or with unknown frequency require separate
drive validation; unsupported extra-high-drive pin states are not rewritten.

The diagram shows logical GPIO slots, not confirmed package pads or DK header pins.
The selected source exposes 7 P0, 17 P1 and 11 P2 slots; some slots are not bonded
in every package. Package bonding and external wiring are not inferred from those
counts. GPIO creates an application reference; application code still controls
direction and value. P2 does not provide GPIO interrupt/SENSE functionality.

## Routing references

The local Zephyr `nrf54l_05_10_15.dtsi`, board sources, bindings and Nordic HAL are
the discovery and build inputs. The bounded hardware rules are checked against
Nordic's [clock-pin table](https://docs.nordicsemi.com/r/bundle/ps_nrf54l15/page/chapters/pin.html-clock_pins),
[dedicated-pin and cross-domain rules](https://docs.nordicsemi.com/r/bundle/ps_nrf54l15/page/chapters/pin.html-dedicated_pins)
and [QFN48 pin assignments](https://docs.nordicsemi.com/r/bundle/ps_nrf54l15/page/chapters/pin.html-qfn48).
Runtime discovery remains offline.

## Verification

The final `python scripts/check.py` passed: 1,507 Python tests and 514 frontend tests,
including lint, syntax, architecture and local HTTP integration checks.
`python scripts/verify_package.py` passed installed-wheel imports, launcher and assets.
Logs: `/tmp/nrf54-activation-full.log` and `/tmp/nrf54-activation-package.log`.

Seventeen new adapter tests cover automatic source architecture recognition, native
domains, clock and dedicated pins, renamed labels, register/ancestor-translation
changes, NFC release/deletion and the required `uicr` node label, active FLPR and
forwarded pins, high-speed restrictions,
invalid inherited routes and preservation of electrical settings. Six integration
tests cover canonical commands, single-step Undo/Redo, reviewed Save, Undo after Save,
restart recovery and rejected commands without source writes. The common architecture
also has synthetic coverage for nRF54L05/L10; their complete board workflows were not
individually tested in this slice.

The real-source nRF54L15 DK CPUAPP opened automatically with 35 logical GPIO slots,
without an existing application overlay. Four changed commands each passed Undo/Redo,
then first Save and saved-source observation:

| Function | Selected route |
| --- | --- |
| GPIO | P2.03 application reference |
| UART20 | TX P1.04, RX P1.05, RTS P1.12, CTS P1.07 |
| I2C21 | SCL P1.11, SDA P1.06 |
| SPI30 | SCK P0.03, MOSI P0.01, MISO P0.02 |

The isolated test app deliberately moves UART20 RTS to release P1.06; the console TX/RX
stay unchanged. Its saved profile configured and built to ELF using its normal command
recipe, without extra driver, Kconfig or overlay overrides. Compiled `zephyr.dts`
confirmed all requested default/sleep assignments and preserved SPI00 flash pins/CS,
flash JEDEC/reset data, console TX/RX/CTS/baud, NFC status and LED/button GPIOs.
Application and profile hashes stayed unchanged during the build. The first read-only
verifier used an incorrect dtlib conversion; it was corrected and rerun against the
same successful build, without changing firmware or rebuilding.

Both `/ns` and `cpuflpr` targets discovered 35 slots and retained their explicit
ownership restriction. The nRF54H20 comparison stayed unavailable at its existing pin
alternative limit. No unsupported target was built through a bypass. A fresh observation
after the final source-guard changes reopened the saved CPUAPP overlay successfully.

Real-source receipts: `/tmp/nrf54-activation-results` (the `*-edited-result.json` preserves
the four-edit result), `/tmp/nrf54-activation-smoke.log`, `/tmp/nrf54-activation-reopen.log`
and `/tmp/nrf54-activation-build-suqvei9e/result.json`. No physical hardware operation
was performed.

Browser verification used a separate no-overlay nRF54L15 project. Opening automatically
displayed 35 slots. P2.03 GPIO Apply and Undo/Redo passed, including the visible P2
interrupt limitation. I2C30 SCL showed only clock-capable P0 choices, with the button
pin unavailable; selecting P0.03/P0.02 generated default/sleep groups and passed reviewed
first Save/inclusion. After restarting the final backend, the saved GPIO/I2C assignments
reopened unchanged and survived leaving/re-entering the editor. At a 390-pixel viewport
there was no horizontal page overflow. Browser warning/error logs were empty.

The verification server and browser tab were closed, and the viewport override was
reset. Temporary application fixtures were retained outside the repository at
`/tmp/nrf54-activation-fixtures-nijex4nd`; evidence receipts retain their original paths.
The upstream Zephyr checkout remained clean. Independent review caught and corrected
an NFC release edge case: Zephyr reads the flag from the `uicr` node label, so a flag
on an unlabeled or renamed compatible node cannot authorize GPIO use. SPI00 now also
always reports its 8 MHz-or-below drive-validation scope, even without child devices.
Independent reproduction confirmed the correction, with no remaining actionable review
findings. The full suite and installed-package checks passed again after these fixes.
