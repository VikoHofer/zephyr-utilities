# Device Tree Editor D11c handoff

Status: D11c interrupt GPIO forms complete within the scope below on 2026-11-06.
Focused service, resource, HTTP, frontend and actual-binding checks passed. Final full
repository, browser and installed-package verification passed.
D11b is committed as `2f070ad`. The full D11 stage remains incomplete.

## Accepted slice

Continue D11 with one bounded interrupt GPIO consumer workflow. The implementation
supports these exact local compatible/property profiles:

| Compatible | Property | Signal and limit |
| --- | --- | --- |
| `bosch,bmi270` | `irq-gpios` | INT1 only; an additional INT2 specifier requires generic binding or source editing |
| `ti,hdc2010` | `int-gpios` | DRDY/INT; the reference binding describes an active-low open-drain signal, without establishing the actual board connection |
| `adi,max30210` | `interrupt-gpios` | INT1; the reference binding describes a signal defaulting to active low, without choosing the configured driver trigger |

**Load interrupt GPIOs** observes canonical source. Select a property and choose its
local provider, pin and flags explicitly. One literal specifier can be edited, or an
absent supported property can be added. Profiles identify the signal; matching local
bindings still govern the property and provider. The consumer must explicitly declare
a literal compatible matching its profile; matched child-binding metadata alone does
not establish device identity. Other GPIO-looking names do not receive an inferred
interrupt meaning.

A supported provider needs a unique local matched binding, `gpio-controller`, explicit
binding-valid `#gpio-cells = <2>`, and ordered `pin, flags` cells. Pin IDs fit 0–31 and
flags fit unsigned 16-bit values. Known local `ngpios` and `gpio-reserved-ranges` further
constrain selection; missing values remain unknown. Malformed/opaque range declarations
and direct child GPIO hog allocations withhold mutation. Valid comma-separated cell
groups such as `<3 1>, <7 1>` are outside the current literal-array parser; they remain
preserved in source and withhold this specialized form. Inherited providers,
ambiguous declarations, macros and multiple specifiers remain source/generic-binding
workflows. No provider, consumer, pinctrl or Kconfig source is created by this form.

Logical active high/low changes flag bit 0 only and preserves every other numeric bit.
GPIO polarity does not select interrupt edge/level or enable a driver trigger.
Driver configuration, Kconfig, shared electrical behavior, pull resistors, routing and
physical interrupt operation remain unknown. Each edit changes only the selected
property in one canonical Undo/Redo operation. Reviewed Save and Configure remain
separate actions.

## Contracts

`InterruptGPIOService` extends the existing binding observation lifecycle. The
shared authenticated route `POST /api/devicetree/editor/drafts/interrupt-gpios` takes
the standard workspace/context/draft revision/hash guard. It returns base binding
information plus provider and consumer rows, with explicit editing capability/reasons.
Profiles are data in `interrupt_gpio_profiles.py`; runtime reads do not execute driver
or project code.

The existing draft command endpoint accepts `interrupt_gpio_set` with exactly:

- `schema_id`: retained observation identity.
- `node_id` and `property`: the observed consumer property.
- `entry`: one `{reference, cells: [pin, flags]}` specifier.

The service validates the exact fields, consumer/profile identity, provider cells,
numeric bounds and established local restrictions. It revalidates local bindings
before and after source planning. Shared draft history, recovery, Save and Configure
boundaries remain intact. No persistence schema, cross-feature dependency or process
boundary changes.

Existing resource diagnostics and D10 removal reviews already index these literal
GPIO dependencies. Shared input claims remain uncertainty rather than a proven
exclusive conflict. Provider removal offers only existing safe, individually selected
optional-property repairs; required or ambiguous dependencies remain blocked. The
panel's removal action explicitly reviews the whole consumer node and retains shared
providers. It retains form values during refresh, restores focus and reveals offscreen
feedback only for the current explicit action. Late responses cannot retarget another
workspace, context or navigation destination.

## Registered-application picker correction

The editor now normalizes its catalogue into available choices and a separate count of
unavailable saved registrations. Missing, replaced, unknown or unspecified availability
cannot enter the picker or its available count. A notice links **Manage saved
applications** to Projects' existing reconnect/removal workflow. Stale picker choices
and removed configuration IDs are cleared on accepted refresh; active context, source,
draft cache and history remain intact. Rejected stale open requests launch no read and
do not discard the active draft. Restoring an application makes it selectable after
refresh without silently reopening it.

Projects' shared catalogue and capture/revalidation behavior are unchanged. Saved
registrations and history are retained for explicit management; the picker never deletes
records or source files.

## Evidence and verification

The profile table records local binding and driver evidence for BMI270, HDC2010 and
MAX30210. Representation/polarity evidence comes from
`include/zephyr/drivers/gpio.h` and `include/zephyr/dt-bindings/gpio/gpio.h` in the
selected checkout. These sources establish the bounded form's representation and signal
identity, not a configured target's runtime interrupt behavior.

| Check | Result |
| --- | --- |
| Interrupt GPIO service regressions | Passed: 24 focused cases covering exact profiles and explicit compatible identity, provider evidence/ranges, single/opaque specifiers, explicit values, stale evidence and source/history preservation |
| Existing resource/removal regression coverage | Passed: 6 cases covering all three properties, conservative sharing diagnostics, disabled consumers, required/optional provider repair and shared-provider preservation |
| Focused editor HTTP suite | Passed: 30 cases including the interrupt GPIO workflow through the authenticated server |
| Interrupt GPIO frontend regressions | Passed: 20 cases covering cells/flags, explicit missing-property assignment, ranges, retained forms/focus, nearby/offscreen feedback, cancellation, stale responses and reviewed removal |
| Registered-application picker regressions | Passed: 20 editor state cases, including six added cases for mixed/unavailable/unknown lists, stale selection cleanup, retained active drafts, restored applications and out-of-order refresh |
| Actual local bindings | Passed: all three consumer profiles using actual emulator/Nordic GPIO bindings; single-property edit, absent-property assignment, provider switch, flag preservation, inverse source restoration and reopened projection passed; the 3725-file catalogue fingerprint was unchanged |
| Quick repository checks | Passed: `python scripts/check.py --quick`, including 1135 Python tests |
| Final full repository checks | Passed: `python scripts/check.py`, 1159 Python and 532 frontend tests, plus lint, syntax and architecture checks |
| Browser interrupt GPIO workflow | Passed in isolated fixtures: motion pin 2 to 6 and flags 256 to 257 preserved other bits; exact source edit, Undo/Redo, explicit absent MAX30210 property with pin 31/flags 32769, multiple-specifier source-only state, reviewed Save and Home/editor return preserved saved source; empty-source state and Undo passed |
| Browser narrow layout and focus | Passed at 600 pixels: no horizontal overflow, reserved pin 3 error visible, removal-review Escape restored its trigger, and forms remained usable |
| Browser application picker | Passed: mixed list excludes missing registrations; all-unavailable state disables Open; restored application returns after refresh; active source survives refresh; Manage saved applications reaches Projects' unavailable-record management with reconnect/removal controls |
| Browser console | No warnings or errors in the final verification |
| Installed-package assets | Passed: `python scripts/verify_package.py` verified the installed wheel and bundled assets |

Actual-binding evidence: `/tmp/zephyr-d11c-bindings-smoke.json`.
Harness: `/tmp/zephyr-d11c-bindings-smoke.py`.
Focused HTTP evidence: `/tmp/zephyr-d11c-http.log`.
Full-check evidence: `/tmp/zephyr-d11c-full.log`.
Installed-package evidence: `/tmp/zephyr-d11c-package.log`.
Browser screenshots:
`/home/vikofed/.codex/visualizations/2026/11/06/01a1b17f-ec4c-7c72-acc3-00b8803fe932/device-tree-d11c-interrupt-gpio.jpg`
and the adjacent `registered-applications-fixed.jpg`.

No Configure, build, flash or physical hardware validation is claimed for D11c.

## Remaining D11 work

Other interrupt signal profiles, inherited providers, symbolic/multiple specifier
forms and driver/electrical validation remain planned. PWM/ADC expanded coverage,
CAN, I2S/audio, USB, clock/reset/DMA providers, power resources, memory partitions and
broader provider impact review also remain planned. Select one bounded next family;
keep D11 partial until its agreed matrix is met.
