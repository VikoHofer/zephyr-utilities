# Device Tree Editor D11b handoff

Status: D11b ADC slice complete within its documented scope on 2026-11-06.
Full repository, browser and installed-package checks passed.
D11a is committed as `24e79e6`. The full D11 stage remains incomplete.

## Accepted slice

The user asked to continue where the previous agent stopped. The completed D11a
handoff selects a bounded ADC channel workflow next. D11b adds vendor-independent
single-specifier local ADC consumers and common ADC channel configuration forms.

A supported provider needs a unique local matched binding with the common ADC
child schema, ordered `io-channel-cells: [input]`, and explicit binding-valid
`#io-channel-cells = <1>`. A generic I/O channel provider is not automatically an ADC.
Channel creation additionally requires explicit `#address-cells = <1>` and
`#size-cells = <0>`. Local channel unit-address and single-cell `reg` must agree.

The form changes one consumer's `io-channels`, creates a `channel@<hex-id>` child, or
edits its gain, reference and encoded acquisition time. Gain/reference choices come
from the local binding. The ADC API representation permits channel IDs 0–31 and
unsigned 16-bit acquisition values; these bounds do not prove hardware support.
Acquisition value zero selects the driver's default. Existing channel IDs are fixed
in the form because consumers implicitly identify channel children by unit-address.
Optional configuration and unrelated source are preserved. Unsupported additional
required fields withhold channel creation.

Multiple specifiers, macros, repeated/ambiguous declarations, unresolved providers,
unbound `/zephyr,user` consumers and incompatible companion names remain source or
generic-binding workflows. This slice does not generate providers, pins, Kconfig,
physical input mappings, voltage limits or runtime sampling configuration.

## Contracts

`ADCService` uses the existing binding observation lifecycle and exact canonical
workspace/context/draft guards. `POST /api/devicetree/editor/drafts/adc` returns base
binding information, provider rows, consumer rows and channel rows. The existing
draft command route accepts `adc_set`, `adc_channel_add` and `adc_channel_set`.
Each accepted action enters one source/history operation; Undo/Redo, recovery,
reviewed Save and explicit Configure retain their existing boundaries.

`adc_providers()` supplies the same observed ADC identity to forms and diagnostics.
Resource observations include bounded source-linked ADC findings. Removal reviews
account for implicit channel consumers and refuse unsafe companion-name repair.
Shared providers survive consumer/channel edits. Diagnostic findings do not create
global Save locks. No persistence format, shared interface or process boundary changes.
Opaque mixed references cannot establish that a channel is unused. Arbitrarily named
children and children with their own binding under I/O channel providers receive
conservative dependency review; unsupported provider evidence remains unknown.
The current ADC macros select children by numeric unit-address, while the common
binding documents `channel` naming. The form deliberately requires both that naming
profile and a matching `reg`.

The browser panel retains prior observations while invalidating stale mutation
authority, shows action feedback beside the form, and rejects late responses after
navigation or workspace changes. Existing optional channel settings stay available
through source and generic binding forms.

## Evidence and verification

Implementation evidence comes from the local checkout's
`dts/bindings/adc/adc-controller.yaml`, representative emulator, Nordic and STM32
bindings, `include/zephyr/drivers/adc.h`, and
`include/zephyr/dt-bindings/adc/adc.h`. The ADC API's five-bit channel identifier,
16-bit acquisition representation and child selection macros establish the stated
representation/identity constraints. Driver-specific channel availability and
electrical behavior remain unknown.

| Check | Result |
| --- | --- |
| ADC service regressions | Passed: 27 cases covering schema identity, count constraints, API bounds, explicit enum values, source preservation, creation, fixed channel identity, stale evidence and atomic history |
| ADC diagnostics/removal regressions | Passed: 24 cases, including numeric and multiple-specifier dependencies, companion names, aliases/paths, opaque mixed references, arbitrary child names, unsupported counts and bounded findings |
| HTTP workflow | Passed: authenticated create/assign/edit/Undo/reviewed Save/reopen; source and shared providers preserved; no project jobs created |
| Frontend regressions | Passed: 21 cases, including stale responses, navigation/workspace/disposal, retained forms, explicit offscreen feedback and creation focus |
| Quick repository checks | Passed before final review fixes; the later full run includes all final changes |
| Final full repository checks | Passed: `python scripts/check.py`, 1128 Python tests and 506 frontend tests, plus Ruff, syntax and architecture checks; loopback tests ran with sandbox escalation |
| Actual local bindings | Passed for `zephyr,adc-emul`, `nordic,nrf-saadc` and `st,stm32-adc`, each with the actual `voltage-divider` binding; consumer edit, channel edit/create, inverse restoration and reopen preserved handwritten/optional properties; the 3725-file catalogue fingerprint was unchanged |
| Browser workflow | Passed: assignment, channel settings/create, exact source preservation, one-step Undo, source-only multiple specifiers, empty source and restoration, Home/reopen, reviewed Save and fresh-process reopen |
| Browser removal and focus | Passed: consumed channel removal blocked with both single/multiple consumers listed; Escape returned focus to the trigger; successful creation focused its surviving result |
| Narrow/scrolled feedback | Passed at 600 × 800: no horizontal overflow; acquisition range error and completed action result visible beside the form after explicit reveal; ordinary edit focus retained |
| Browser console | No warnings/errors in the final browser run. One earlier temporary server exit produced a fetch error; relaunch and fresh-context verification passed |
| Installed-package assets | Passed: final `python scripts/verify_package.py` after the feedback/focus fix |

Full-check evidence: `/tmp/zephyr-d11b-full.log`.
Package evidence: `/tmp/zephyr-d11b-package-final.log`.
Actual-binding harness/evidence: `/tmp/zephyr-d11b-bindings-smoke.py` and
`/tmp/zephyr-d11b-bindings-smoke.json`.
Browser screenshots: `/home/vikofed/.codex/visualizations/2026/11/06/01a1b17f-ec4c-7c72-acc3-00b8803fe932/device-tree-d11b-adc.jpg`
and the adjacent `device-tree-d11b-adc-narrow.jpg`.

No Configure, build, flash or physical hardware validation is claimed.

## Remaining D11 work

Expanded ADC optional-field and symbolic/multi-specifier forms, inherited providers,
interrupt GPIOs, CAN, I2S/audio, USB, clocks/resets/DMA, power resources, memory
partitions and broader provider impact reviews remain planned. The next bounded
family should be interrupt GPIO consumers. Keep D11 partial until its agreed matrix
is met.
