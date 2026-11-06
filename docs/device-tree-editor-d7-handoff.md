# Device Tree Editor D7 handoff

Status: local peripheral/topology implementation complete on 2026-11-06 within the
limits below. D6 was committed as `564fcff` before this stage.
The real Configure smoke, full checks, browser verification and installed-package
verification passed. D0–D7 are implemented within their scoped boundaries;
D8 Nordic pin configuration is the next stage.

## Delivered workflow

Open an existing application overlay and choose **Load peripherals**. An accessible
list shows uniquely resolved local UART, I2C and SPI controllers, their device children,
and GPIO LED/button groups. Select an entry to inspect nearby supported actions and
diagnostics. Inspection can be cancelled and does not modify source or run project tools.

Controller forms change supported status/speed fields. **Add bus device to draft** uses
a binding explicitly qualified for the parent bus and requires the user to enter its
required values. **Apply device changes** updates the existing node rather than inserting
a duplicate. I2C/SPI address changes patch the hexadecimal unit-address and single-cell
`reg` together. Duplicate local addresses/chip selects, including disabled siblings, are
rejected; unresolved sibling addresses prevent proving a new allocation unique.

**Add GPIO group to draft** creates a locally described `gpio-leds` or `gpio-keys` group.
LED/button child forms use actual child-binding fields and the selected local provider's
named GPIO cells. New buttons require an explicit `zephyr,code` for the selected GPIO-key
driver; that requirement is distinguished from the YAML binding's optional field.

**Remove device from draft** removes a supported local leaf after known consumer checks.
It preserves its controller, provider nodes, siblings and shared chip-select array.
All commands use the canonical overlay draft, shared Undo/Redo, diff and durable recovery.
Required fields and defaults are never inserted by inspection. Reviewed Save and explicit
Configure/Projects Build remain separate actions.

## Implementation and support boundaries

`PeripheralService` extends the D6 observation service and is registered as
`devicetree_peripherals`. `POST /api/devicetree/editor/drafts/peripherals` captures the
current context/draft identity and binding fingerprint. Commands revalidate observed
inputs before planning and before committing the draft change, then enter the existing
`DraftService.command` path. `SourceDocument.plan_update_node` performs atomic header/
property edits and protects local path consumers. No new persistent draft schema or
separate generated-source model is introduced.

`BindingCatalog.devices(bus)` discovers UART/I2C/SPI devices by resolved `on-bus`, not
filenames or compatible-name guesses. Candidates are sorted and bounded to 256 per bus;
truncation and ambiguous/unsupported/missing states are explicit. A compatible index and
detached per-observation list cache avoid repeated catalogue scans. Reuse still requires
the existing exact file/root revalidation.

Supported profiles are deliberately bounded:

- Controllers need a unique local declaration and exactly one resolved supported bus.
  Inherited board/SoC controllers and unresolved `&controller` blocks are not resolved.
  UART offers supported status/current-speed; I2C/SPI offer supported status/clock-frequency.
  Schema enums/constants apply; pinctrl and controller defaults are not fabricated.
- I2C/SPI forms require explicit local address/size cell counts of 1/0 and matching
  literal child unit-address/`reg`. I2C supports non-reserved 7-bit addresses
  `0x08`–`0x77`. I3C, multiple-bus controllers, other cell layouts, raw expressions and
  device-specific electrical/address-strap validity remain outside these forms.
- SPI chip-select indices are checked against a resolved existing `cs-gpios` array.
  Its order and entries are preserved across device edits/removal. Without that array,
  native chip-select capacity remains unknown; no capacity or pin assignment is inferred.
- Device creation needs explicit supported required values. Missing/ambiguous schemas
  and required fields outside the supported form prevent creation. Default metadata
  stays informational; optional properties remain available through the binding editor.
- Address changes protect known local absolute-path consumers, including supported
  binding `path` properties. Removal is limited to leaves and refuses known local
  label/path consumers. Cross-file, inherited and macro-generated references remain
  unknown; this is not whole-project removal planning.
- GPIO cells come from an actual local provider and explicit matching cell count.
  Button input codes are explicit because the selected driver requires them for enabled
  children. No GPIO/pinctrl routing, physical conflict validation, module recipe or
  automatic controller creation is included.

The browser uses the shared draft-observation lifecycle for binding and topology forms.
Pending source changes, Undo/Redo, workspace/context switches and navigation invalidate
old metadata; cancelled or late responses cannot restore stale forms. Source ownership
and explicit Save/Configure boundaries remain unchanged.

Regression cases include binding-constrained generated addresses/compatibles, unsupported
GPIO specifier spaces, extra required group fields, repeated bound path consumers and
noncanonical unit-address spellings. These cases cannot silently bypass source guards.

## Verification

| Check | Status |
| --- | --- |
| Binding catalogue focused suite | Passed: 23 tests, including bus filtering, detached caching, malformed/duplicate candidates and explicit 256-entry truncation |
| D7 source/service/frontend focused totals | Passed: 18 atomic source tests, 22 peripheral service tests and 23 frontend tests |
| `python scripts/check.py --quick` | Passed: 866 Python tests, 20 loopback tests excluded; all 29 frontend test files passed |
| `python scripts/check.py` | Passed: 886 Python tests, 385 frontend tests, lint, syntax and architecture |
| `python scripts/verify_package.py` | Passed: installed wheel imports, entry point and assets, including the three new browser modules |
| Desktop/narrow/scrolled/error/leave-reopen browser checks | Passed: I2C add/update/collision, removal/Undo, SPI binding/CS selection, GPIO group/button/provider fields, UART enable, stale-schema rejection/reconciliation, retained source after leaving/reopening; no horizontal overflow at 600 pixels |
| Real selected-environment Configure smoke | Passed: 14 D7 commands, four collision rejections, shared Undo/Redo, reviewed Save and explicit Configure; no firmware compilation or flash |

The disposable real fixture targeted `frdm_mcxn947/mcxn947/cpu0` with the selected
Zephyr 4.4.2 checkout and SDK 1.0.1. Two `bosch,bme280` devices were added to each of
the I2C and SPI emulator buses, selecting the matching real bus-qualified schema.
Duplicate address/CS additions and updates were rejected. Shared Undo/Redo restored
the same source. Removing one device per bus and moving the survivor into the freed
address updated the existing node and `reg`, preserved its label, and retained the
controller and complete `cs-gpios` array.

The same run changed UART speed/status, created real `gpio-leds` and `gpio-keys` groups,
added their children using the `zephyr,gpio-emul` provider's `pin`/`flags` cells, and
updated the LED label. Draft actions left the application overlay unchanged until
reviewed Save. Explicit Configure then returned `configured: true`, `overlay_used: true`
and `stale: false`; generated DTS contained the expected nodes and values. Application
input hashes were unchanged by Configure and no `zephyr.elf` was produced. The disposable
application and its build directory were removed; no other server was stopped.

Exact local evidence is retained under `/tmp/zephyr-d7-real-534fe170/`: `verification.json`,
`commands.json`, `rejections.json`, `two-devices-each-bus.json`, `candidate.overlay`,
`generated.dts`, `configure-result.json`, `configure-job.json`, `run.log` and the harness.
The first topology load took 5.435 seconds; validated cached loads took 0.382–0.479 seconds
and commands took 0.658–0.784 seconds in this fixture. These are observations, not general
performance guarantees. Configure does not establish a working driver, valid wiring or
operation on hardware.

## Next stage

Implement **D8 only** from the
[master plan](device-tree-editor-masterplan.md): the first supported Nordic pin adapter
and accessible pin assignment workflow for the exact selected target. Reuse canonical
source commands, actual provider cells, schema observations and separate Save/Configure
actions. Module recipes, visual module placement and broader cross-file composition
remain later stages.
