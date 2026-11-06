# Device Tree Editor D9 handoff

Status: local module catalogue, logical canvas and separate layout persistence implemented
on 2026-11-06; verification passed. D8 was committed as `cfaf10e` before this stage.
D10 resource diagnostics and broader removal planning is next
when requested. This handoff does not claim hardware behavior or complete target support.

## Delivered workflow

Open an application overlay and choose **Load modules**. The editor observes the current
canonical source and the selected local binding catalogue without running project tools.
The logical canvas shows unique local buses, GPIO groups/providers, child devices and
read-only unmatched compatible declarations. Known parent and GPIO relationships are
shown without inventing inherited connections or physical wiring.

Choose **Place module**, a recipe and an existing bus or GPIO group. Enter the node name,
optional label, address/chip select and required binding values before placing the module
in the draft. The initial data-only, versioned catalogue contains:

| Recipe | Connection and explicit parameters |
| --- | --- |
| BME280 I2C sensor | Existing local I2C controller, address and any selected-binding required fields |
| BME280 SPI sensor | Existing local SPI controller, chip-select index and required SPI frequency |
| GPIO indicator | Existing gpio-leds group, resolved local provider and named provider cells |
| GPIO input button | Existing gpio-keys group, resolved local provider/cells and input event code |

The selected environment's actual bindings constrain these forms. Missing or ambiguous
bindings and parents show unavailable states. GPIO groups can be prepared explicitly in
Peripherals. Controller enablement, pin assignments and software configuration remain
their own actions. Software requirement notes do not edit Kconfig or prove driver readiness.

Each placement adds one leaf through the canonical draft command service. Address, node
name and label collisions produce a clear conflict. Updates preserve unrelated source
properties and comments through the existing source plans. Removal deletes only a safe
local leaf; known references, path consumers and nested declarations prevent removal.
Shared controllers, GPIO groups, providers and chip-select arrays are retained.

Handwritten devices appear as source nodes. Matching recipes are informational, without
conversion, provenance claims or automatic upgrades. No ownership metadata is needed:
these recipes create no parent or dependency they could later remove. The graph is
reconstructed from source after Save/reopen, including with an empty utility data directory.

Canvas selection and directional movement are keyboard accessible. Card positions use
separate presentation state, saved with **Save layout** and reloaded with **Reload layout**.
They do not affect overlay text or shared Undo/Redo. Hardware edits remain unsaved until
**Review and Save**; Configure and Projects Build retain separate explicit actions.

## Implementation and boundaries

`recipes.py` contains four built-in recipe records with catalogue version 1 and individual
version 1. It contains no executable recipe hooks or downloads. `ModuleService` extends
the D7 observation, validates the exact recipe version and delegates module commands to
the existing peripheral source plans. Observations retain context/draft revision/hash,
catalogue dependency and expiry guards. `module_add`, `module_update` and `module_remove`
use the shared `/drafts/command` route and one canonical history operation per action.

This is a vendor-independent local-source workflow. It does not require the D8 Nordic
pin adapter and does not broaden that adapter's inherited hardware support. Unique
declarations with supported local schemas are needed for editable connections. Unknown
inherited controllers, physical wiring, address straps, electrical suitability, package
pads and runtime behavior remain unknown. A recipe match alone does not establish them.

Unique canonical source paths provide `node:/...` graph keys. Repeated contributors do
not receive stable keys; source command IDs remain revision-bound. The canvas contains
at most 256 graph nodes, with a visible truncation diagnostic and filtered edges. Other
source remains available through source and peripheral forms. Renaming a source node can
reset its presentation position without changing its hardware meaning beyond the source edit.

`LayoutService` uses guarded module observations at `/drafts/module-layout` and
`/drafts/module-layout/save`. `LayoutStore` writes schema-version-1 JSON records under the
feature-owned `module-layouts` directory. Scope includes workspace path/identity,
application and overlay paths, project/configuration IDs and requested target. Separate
optimistic layout revisions, directory `flock` and atomic replacement prevent overwriting
a newer layout through a stale request. Unknown versions and malformed records are
preserved. There is no earlier layout schema to migrate. Limits are 256 layouts, 256 KiB
per record, 256 positions and integer coordinates from 0 to 31 on each axis. See
[ADR 0022](decisions/0022-module-layout-and-source-identity.md).

The browser refresh work retains prior observation presentation while loading replacements;
only current guarded metadata can authorize commands. It also avoids rebuilding unchanged
forms during unrelated updates. Reviewed Save can replace the context UUID while retaining
the same physical application/document/draft presentation and layout. Exact context and
source guards still invalidate commands. A different document, target or workspace clears
the old presentation. Explicit actions restore an equivalent control with `preventScroll`
only if the user has not moved focus elsewhere; passive refresh never starts focus movement.
Placement selects the newly added module, address edits follow its new path, and feedback
stays beside the form. Reconciliation and explicit selection clear obsolete action feedback.

## Verification

| Check | Status |
| --- | --- |
| Module service focused tests | Passed: 17 tests, including actual binding/provider constraints, collisions, source/history synchronization, safe removal, stale observations, graph limits and real reviewed Save followed by a fresh application/data-directory reopen |
| Ruff on module service, recipes and module tests | Passed |
| Layout persistence/service focused tests | Passed: 8 tests for source/history independence, restart/context identity, stale graph/draft guards, conflicts, invalid values, failed replacement and unknown-version/symlink refusal |
| Module and refresh frontend focused tests | Passed: 22 module tests; shared focus and observation regressions are included in the complete frontend run |
| `python scripts/check.py --quick` | Passed during implementation: 960 Python tests; final full checks supersede this intermediate run |
| `python scripts/check.py` | Passed: Ruff, syntax, architecture, 980 Python tests including loopback integration and 449 frontend tests; final feedback correction passed the complete 450-test frontend suite |
| `python scripts/verify_package.py` | Passed: installed wheel imports, launcher and runtime assets, including the new browser modules |
| Actual Zephyr binding catalogue smoke | Passed: all four recipes against actual local bindings, 12 add/update/remove source plans, nine software symbols verified, unchanged catalogue fingerprint |
| Browser desktop/narrow/scrolled/error/leave-reopen checks | Passed: I2C/SPI placement, required parameters, live source, reviewed Save, removal/Undo, persisted positions, feature reopen, conflict/reconciliation feedback, focus and scroll retention; 600-pixel viewport had no horizontal overflow |

Focused module tests establish that graph reconstruction needs no recipe ownership or
layout metadata. They do not establish compilation, effective inherited topology,
physical wiring or hardware operation. D9 did not run Configure, compile, flash or use
hardware. The actual-checkout smoke only read selected local binding/Kconfig files and
applied in-memory source plans.

Full-check logs are `/tmp/zephyr-d9-final-check.log`,
`/tmp/zephyr-d9-frontend-complete.log` and `/tmp/zephyr-d9-package.log`.
Actual binding evidence and its harness are `/tmp/zephyr-d9-bindings-smoke.json` and
`/tmp/zephyr-d9-bindings-smoke.py`. The browser used a disposable synthetic application
under `/tmp/zephyr-d9-browser`; no project tools ran. During a deep-scrolled UART update,
`window.scrollY` remained 3730 and the form top stayed 392.09375 pixels; focus returned to
**Enable controller**. After reviewed Save, all ten graph cards remained mounted with
their positions intact and stale editing controls disabled. The final browser console
contained no captured errors. The temporary browser tab and server were closed.

The captured preview is
`/home/vikofed/.codex/visualizations/2026/11/05/01a1acb6-f8a4-7e30-837b-87ae026e4d6b/device-tree-d9-modules.jpg`.

## Next stage

When requested, implement **D10 only** from the
[master plan](device-tree-editor-masterplan.md): unified resource diagnostics and explicit
removal planning. Preserve the canonical source command path and Save/Configure separation.
Broader pin adapters, custom board maps, multi-overlay composition and multiple images
remain their separate stages.
