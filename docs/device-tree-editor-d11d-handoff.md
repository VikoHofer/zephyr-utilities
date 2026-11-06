# Device Tree Editor D11d handoff

Status: implemented and verified within the scope below on 2026-11-06; delivered with
the first controller-first foundation.
The previous UI stability work is committed as `6b73fa8`. D11 remains partial.

## Accepted slice

The user requested the next bounded D11 family. D11d covers nominal CAN controller
settings and connection to an existing local active transceiver. Supported controller
compatibles are `zephyr,can-loopback`, `microchip,mcp2515`, and `nxp,flexcan`;
the active-transceiver profile is `can-transceiver-gpio`. These are binding/source
profiles, not claims of physical target or hardware validation.

**Load CAN controllers** explicitly observes the current canonical draft. Select a
controller and check only the fields to set in this action: nominal `bitrate`,
`sample-point`, and/or `phys`. Unselected fields, defaults, handwritten source,
provider nodes and other connections are preserved. One Apply produces one shared
Undo/Redo step. Reviewed Save and Configure remain separate actions.

Nominal bitrate uses the CAN API range 1–1,000,000 bit/s. Sample point is 0–999 per
mille; explicit 0 requests automatic sample-point selection. No clock/prescaler solver
or hardware timing feasibility is claimed. Modern properties do not silently replace
deprecated aliases. A legacy-only `bus-speed` requires source/generic repair before
using the specialized nominal bitrate field.

Selected active PHY limits take precedence over the exact passive `can-transceiver`
child. Explicit local minimum/maximum rates are checked; missing facts stay unknown.
Malformed or opaque evidence withholds related rate/connection changes. Sample-point
edits remain independent where supported. Existing explicit CAN-FD data rates are
considered when connecting a transceiver, but FD settings are not edited.

Active transceivers require explicit `#phy-cells = <0>` and at least one existing
`enable-gpios` or `standby-gpios`. Each control must be a single literal specifier
with a local binding-valid GPIO provider, `pin, flags` cells, pin 0–31 and flags
0–65535. This checks representation, not electrical or physical readiness; inherited,
opaque and unsupported control declarations remain source-only. Passive children
whose names normalize to `can_transceiver` but differ from the exact documented
`can-transceiver`, or collide with another such child, withhold related visual edits
when passive limits are effective. A valid active `phys` still takes precedence.

## Contracts and boundaries

`CANService` reuses the binding observation and canonical draft command path.
`POST /api/devicetree/editor/drafts/can` takes the usual workspace/context/draft
revision/hash guard and returns binding information plus controller/transceiver
evidence. Explicit literal compatible identity, unique local declarations, matching
bindings and per-field constraints govern editing capability.

`can_controller_set` takes exactly `kind`, `schema_id`, `node_id`, and `values`.
`values` is a nonempty subset of `bitrate`, `sample-point`, and `phys`; the first two
are literal integers and `phys` is a proven local reference. It does not delete
properties. Generic binding/source workflows retain unsupported edits. Local binding
evidence is revalidated before and after planning.

Resource diagnostics reuse the same CAN evidence. Known contradictions are source
linked; unsupported evidence and shared-transceiver suitability remain unknown.
Removal reviews protect passive child dependencies that lack a phandle and prevent
optional repairs from deleting the final GPIO control of a surviving GPIO transceiver.
An active `phys` repair must not silently expose an unreviewed passive configuration.
The existing explicit repair selection and final canonical command remain authoritative.

Routine loads, selection and edits preserve scroll and focus. Inline status/error
feedback is repeated in a fixed notice when offscreen. Source navigation and removal
dialogs are deliberate actions. Late results remain bound to the current draft,
workspace and mounted view. No new storage schema, cross-feature dependency or process
boundary is introduced.

## Verification

Focused service/resource checks pass: 32 CAN service cases and 22 CAN resource/removal
cases. The 31 editor HTTP integration cases pass, including authenticated CAN access,
guarded command history, stale-command rejection, reviewed Save and reopened evidence.
Cross-review found no remaining material backend or removal issues.

Actual-binding smoke passes for all three controller profiles using the local Zephyr
checkout's 3,725 binding files. It verifies exact selected-property patches, active
transceiver switching, invalid-rate rejection, explicit zero sample point, inverse
source restoration, reopened projections and unchanged shared providers. The binding
catalogue fingerprint remained unchanged. Evidence is in
`/tmp/zephyr-d11d-bindings-smoke.json`; the temporary smoke script never runs project
tools. Profiles cite the local binding, driver and CAN/GPIO API paths in
`can_profiles.py`; no external version or board-support claim is made.

Final checks passed:

- `python scripts/check.py --quick`: 1,189 Python tests; 25 loopback cases excluded;
  all 38 frontend test files passed. Lint, syntax and architecture checks passed.
- `python scripts/check.py`: 1,214 Python tests and 580 frontend tests passed, including
  the 26 new CAN frontend cases. Loopback HTTP tests ran with local socket permission.
- `python scripts/verify_package.py`: installed-wheel imports, entry point and browser
  assets passed, including both new CAN modules.
- Browser: explicit load and controller selection preserved exact scroll position;
  Apply preserved exact scroll position and returned focus to its button. Checked
  selected-field updates, explicit sample point 0, unchanged unselected properties,
  transceiver switching without provider changes, exact Undo/Redo, reviewed Save and
  reopened values. Deprecated bitrate remained unavailable, empty results were clear,
  and leaving/reopening reset observations while retaining the canonical draft.
- Browser at 600 pixels: no horizontal overflow. A local range error retained focus
  and the Apply button's visible position within one pixel; browser anchoring adjusted
  scroll for the inline error above. Known transceiver-limit violations now fail locally
  with editable inputs and no reconciliation or draft revision change. Server-side
  rejection/reconciliation was also exercised before adding this local usability check.
- Browser: CAN resource diagnostics appeared, passive-transceiver removal was blocked
  with its dependency explanation, and Escape cancelled whole-controller removal and
  returned focus to its trigger. No browser console errors were recorded after the final
  reload. The temporary browser/server were closed and the viewport restored.

Logs are `/tmp/zephyr-d11d-{quick,full,package,frontend,http}.log`. Screenshot evidence
is `/home/vikofed/.codex/visualizations/2026/11/06/01a1b17f-ec4c-7c72-acc3-00b8803fe932/device-tree-d11d-can.jpg`.
No Configure, build, flash or physical hardware operation is claimed.

## Remaining D11 work

CAN-FD data timing forms, other CAN profiles, inherited controllers, transceiver
creation/limit editing, GPIO wiring, pinctrl and timing feasibility remain planned.
I2S/audio, USB, clocks/resets/DMA, power resources, memory partitions, broader PWM/ADC/
interrupt GPIO support and provider disabling/chosen-impact review remain in the
master plan. Completing this slice does not complete the full D11 matrix.
