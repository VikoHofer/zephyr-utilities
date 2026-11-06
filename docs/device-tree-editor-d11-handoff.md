# Device Tree Editor D11 handoff

Status: D11a PWM consumer/LED slice complete within its documented scope on 2026-11-06.
Full repository, browser and installed-package checks passed.
D10 was committed as `6403e43` before this work. The full D11 stage is incomplete;
its family matrix is recorded in the master plan.

## Accepted slice

The user requested committing D10 and continuing D11. The D11 prompt explicitly selects
one family-sized slice at a time. D11a delivers vendor-independent forms for one literal
PWM specifier on a unique local consumer and creation of local PWM LED groups/children.
Providers need a matched local binding, explicit matching `#pwm-cells`, and the ordered
cells `channel, period` or `channel, period, flags`. This applies by source/binding
capability rather than a vendor or board allowlist.
Provider count and compatible values must satisfy supported binding constraints.

**Load PWM consumers** inspects the current canonical draft. Select a consumer to change
its provider, channel, positive period in nanoseconds and optional flags. Polarity editing
preserves all other numeric flag bits. Creation requires explicit values, and additional
required group/child properties outside this form withhold creation. D10 reviews handle
removal and preserve shared providers. A provider used by a consumer with local
`pwm-names` requires explicit consumer repair; deleting only `pwms` is not offered as
a whole-property repair. Each accepted mutation is one source/history
operation; reviewed Save and explicit Configure remain separate.

Existing multiple specifiers, expressions, ambiguous declarations, unresolved providers,
unsupported cell layouts and incompatible `pwm-names` remain source/generic-binding-only.
No inherited controller, routing, pinctrl, Kconfig, duty cycle or runtime state is generated.
A provider's `channels` property is a count, not proof of index base or allowed identifiers.
Different channels may share timer constraints, so independent timing is not inferred.
Missing electrical, clock, driver and hardware information remains unknown.

## Contracts

`PWMService` extends the existing `BindingService` observation lifecycle. The shared
authenticated route `POST /api/devicetree/editor/drafts/pwm` receives the standard
workspace/context/draft revision/hash guard. Its response includes the base binding
projection, provider rows, consumer rows, PWM LED groups and group-creation capability.
It retains at most 16 observations for ten minutes and revalidates local binding evidence.
Provider metadata includes canonical reference, source path, ordered cells, local status
and an optional supported literal channel count. Consumers include their observed field,
single entry and editing capability/reason. Unsupported local declarations remain visible.

The existing draft command endpoint accepts:

- `pwm_set`: `schema_id`, `node_id`, `entry`.
- `pwm_group_add`: `schema_id`, `name`.
- `pwm_led_add`: `schema_id`, `parent_id`, `name`, `label`, `entry`.

An entry is one `{reference, cells}` specifier. Its cells must match the chosen provider
and remain unsigned 32-bit literals; the period must be positive. The service validates
exact command fields and observation identity before source planning and revalidates
binding files before returning the plan. Source changes enter the existing draft commit,
Undo/Redo and recovery path; there is no parallel generated-source model.

`pwm_resources.py` supplies bounded source-linked diagnostics to the D10 resource service.
Established invalid literal periods are distinct from unknown channel sharing, timer
coupling and incomplete evidence. Disabled or unresolved activation does not establish
simultaneous use. Diagnostics do not automatically rewire consumers or block Save.
Existing D10 reference graphs protect PWM providers during removal.
`pwm_providers()` supplies the same validated provider evidence to forms and diagnostics.
Diagnostic construction is bounded at 256 findings and explicitly reports truncation.

The PWM browser panel uses shared draft observations and retained presentation. Source
changes invalidate mutation authority while old rows remain visible; local inputs and
focus survive unrelated renders. The parent renders the PWM panel before resource reviews
so its trigger exists when removal reviews restore focus. Explicit actions get nearby results. Navigation,
cancellation and workspace changes invalidate late responses. No persistence format,
cross-feature dependency, process boundary or new architecture decision is introduced.

## Evidence and verification

PWM units and the polarity bit follow the upstream
[PWM binding definitions](https://docs.zephyrproject.org/latest/doxygen/html/dt-bindings_2pwm_2pwm_8h.html).
Implementation decisions were checked against the selected local checkout's
`dts/bindings/pwm/pwm-controller.yaml`, `dts/bindings/led/pwm-leds.yaml`, representative
two-/three-cell bindings, and `drivers/pwm/pwm_stm32.c`, `pwm_nrfx.c` and
`drivers/led/led_pwm.c`. These expose differing channel numbering, shared period
constraints and the PWM LED driver's use of its first specifier.

| Check | Status |
| --- | --- |
| PWM service regression tests | Passed: 24 cases covering supported cell layouts, count/identity constraints, unknown channel numbering, positive periods, numeric flags, source preservation, creation limits, stale evidence and atomic history |
| PWM diagnostics/removal regression tests | Passed: 20 cases, including sharing and timer uncertainty, period validity, bounded construction, provider evidence and named-consumer removal safety |
| HTTP create/edit/Undo/Save/reopen integration | Passed in the final full run |
| Frontend state, form and lifecycle tests | Passed: 17 new cases; complete frontend run passed 485 cases |
| Actual local binding smoke | Passed: three read-only cases using `zephyr,fake-pwm` with two cells, `nordic,nrf-pwm` with three cells and `st,stm32-pwm` with vendor flags preserved from 256 to 257; edited source/reopen retained the specifier and catalogue fingerprint was unchanged |
| Full repository checks | Passed: final `python scripts/check.py` run after the removal-focus render-order fix, including 1076 Python tests, 485 frontend tests, Ruff, syntax and architecture checks |
| Desktop/narrow/scrolled/error/navigation browser checks | Passed: zero-period error beside its form; flags 256 to 257 retained; exact source edit and handwritten property preservation; one-step Undo; group and two-cell LED creation; opaque/multiple specifiers shown as source-only; removal-review Escape returned focus to the PWM trigger; 600 × 800 layout had no horizontal overflow and showed the nearby error; Home/reopen restored the exact enabled draft and reset PWM to explicit loading |
| Browser empty state and console | Passed: empty local root showed no local PWM consumers/groups, one Undo restored the exact prior source, and the browser console had no warnings/errors |
| Installed-package runtime assets | Passed: `python scripts/verify_package.py` |

Actual-binding evidence is `/tmp/zephyr-d11-bindings-smoke.json`; its harness is
`/tmp/zephyr-d11-bindings-smoke.py`. These inspect the existing local checkout using
synthetic source and execute no project code.

Final full-check evidence is `/tmp/zephyr-d11-full-final.log`. The final browser screenshot is
`/home/vikofed/.codex/visualizations/2026/11/05/01a1acb6-f8a4-7e30-837b-87ae026e4d6b/device-tree-d11-pwm.jpg`.

No Configure, build, flash or physical hardware validation is claimed for D11a.

## Remaining D11 work

The next slice should be a bounded ADC channel workflow. Interrupt GPIOs, CAN, I2S/audio,
USB, clock/reset/DMA providers, power resources, memory partitions and broader provider
impact review remain planned. Expanded PWM source and inherited-target coverage also
remain planned. Continue to state the selected family's support boundary and leave D11
partial until its agreed matrix is met.
