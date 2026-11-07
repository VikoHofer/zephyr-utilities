# Device Tree Editor: automatic board revisions

Date: 2026-11-07. The preceding chip-editor redesign and automatic chip support were
committed as `b0ff964`. This slice implements the user's subsequent request for board
revision support, including nRF9160 DK and nRF9161 DK without manual setup.

## Behavior

Opening a project resolves the board's standard declared default when its target omits
`@revision`. An explicit declared revision remains the selected revision. The editor
reports the effective revision beside the chip; it does not rewrite the saved target.
There is no additional revision setup or extraction button.

Discovery composes the selected base DTS, its applicable revision overlay, then the
application overlay or unsaved draft. Revision overlays and their includes are read
from the selected board's source tree. Existing default/older hardware differences,
including DTS deletion directives, are interpreted by the selected Zephyr parser.
When no revision overlay exists, the base DTS remains valid for the selected revision.

Projects uses the same pure resolver for automatic application-overlay prediction and
safe draft creation/inclusion. Existing application overlays named for the effective
default revision are considered before the generic fallback. Saved profiles retain
the requested target, and Configure delegates to Zephyr using that original target.
Creation and build previews use the matching captured environment. Missing or mismatched
evidence leaves automatic selection unknown; explicit replacement remains usable.
Historical Configure evidence is not rewritten.

GPIO/UART/I2C/SPI editing, canonical Undo/Redo, reviewed Save and recovery use the existing
workflow. Source guards cover current revision metadata, selected overlays/includes,
ambiguous names and previously absent candidates. Upstream board files are never edited.

The shared evidence contract is recorded in [ADR 0026](decisions/0026-automatic-board-revisions.md).

## Scope and limits

Standard `major.minor.patch`, `letter` and `number` metadata supports automatic declared
defaults and explicit declared revisions. Projects retains its existing target catalogue;
this slice does not add arbitrary unlisted revision inputs. The pure resolver also models
verified semantic-version/letter compatible selection for its callers.

Custom `revision.cmake` is not executed during discovery. Board extensions, additional
unsupported board composition and conflicting source candidates remain unavailable with
specific reasons. Numeric compatible selection remains guarded because the inspected
local CMake implementation does not establish the same predecessor behavior as its
version/letter branches. This does not prevent declared numeric defaults or exact revisions.

Revision support does not establish Nordic cross-core or secure-image GPIO permissions.
Non-secure targets retain their existing ownership restriction for pin editing. Pin
inventories are logical GPIO slots; physical wiring and runtime electrical behavior remain
outside source-level validation.

The implementation was checked against the selected local Zephyr revision helpers and
the official [board revision documentation](https://docs.zephyrproject.org/latest/hardware/porting/board_porting.html#multiple-board-revisions).
The inspected checkout's `cmake/modules/boards.cmake`, `cmake/modules/dts.cmake` and
`cmake/modules/extensions.cmake` define the source-selection behavior; no runtime network
dependency is introduced.

## Verification

`python scripts/check.py` passed: 1,484 Python tests and 514 frontend tests, including
local HTTP integration, lint, syntax and architecture checks. The full check includes
the quick check's coverage. `python scripts/verify_package.py` passed installed-wheel
imports, launcher and runtime-asset checks. Final logs are
`/tmp/board-revisions-final-full.log` and `/tmp/board-revisions-final-package.log`.

The real-source matrix used the selected local Zephyr environment and SDK:

| Requested target | Effective revision | Logical GPIO slots | Edit / Save / build |
| --- | --- | --- | --- |
| `nrf9160dk/nrf9160` | `0.14.0` (automatic default) | 32 | Passed |
| `nrf9161dk/nrf9161` | `0.9.0` (automatic default) | 32 | Passed |
| `nrf9160dk@0.7.0/nrf9160` | `0.7.0` | 32 | Passed |
| `nrf9161dk@0.7.0/nrf9161` | `0.7.0` | 32 | Passed |

Each row exercised GPIO, UART, I2C and SPI changes, Undo/Redo, first overlay creation,
reviewed Save and saved-source reread. All four saved application profiles configured
and built to ELF through their normal command recipes, with no additional overlay,
driver or Kconfig overrides. Configure selected the generated application overlay and
expected board revision overlay. App-source hashes remained unchanged during builds.

Fresh editor observations matched the generated `zephyr.dts` revision differences:
nRF9160 default includes the disabled PCAL6408A expander, while revision `0.7.0` does
not; nRF9161 default contains GD25WB256, while revision `0.7.0` deletes it and selects
GD25LB256. Four topology comparisons passed. Both `/ns` targets loaded their default
revision and 32 GPIO slots, while visibly retaining the secure-image permission
restriction for pin activation. These are source and build checks, not hardware tests.

Browser verification covered automatic opening of both DKs. nRF9160 started without
an overlay and passed GPIO assignment, Undo/Redo and reviewed creation/Save. nRF9161
automatically selected `boards/nrf9161dk_nrf9161_0_9_0.overlay`, saved a GPIO assignment,
preserved its existing UART baud rate and left the generic `app.overlay` unchanged.
Leaving/reopening retained the saved assignment and revision. At a 390-pixel viewport,
the revision label remained readable and the page had no horizontal overflow. Browser
warnings and errors were empty. Verification tabs and the temporary server were closed.

Fourteen isolated cases were compared with the local CMake revision helper. Regression
tests cover malformed/custom metadata, defaults and explicit revisions, overlay
composition/deletion, stale source evidence, draft inclusion/recovery, and missing
environment evidence. Independent review identified and corrected missing-snapshot
fallbacks in live build preview and editor capture, so absent revision evidence cannot
silently select the generic application overlay.

Local evidence is retained in `/tmp/board-revisions-results`,
`/tmp/board-revisions-builds-pa_3cjap/summary.json`,
`/tmp/board-revisions-topology.json` and `/tmp/board-revision-cmake-parity.json`.
Temporary application fixtures were moved out of the repository to
`/tmp/board-revisions-fixtures-yorg1bnf`; receipts retain their original paths.
No test firmware or build artifacts were added to Git, and the Zephyr checkout remained
clean.
