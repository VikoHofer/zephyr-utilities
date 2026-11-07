# Device Tree Editor: projects without an overlay

Date: 2026-11-07. This extends the [pin activation handoff](device-tree-editor-pin-activation-handoff.md)
under the [chip and pin redesign](device-tree-editor-pin-workspace-plan.md).
[ADR 0025](decisions/0025-new-overlay-save-and-project-inclusion.md) records the public
Projects write handoff and versioned recovery/publication decisions.

The subsequent [automatic chip support handoff](device-tree-editor-automatic-chip-support-handoff.md)
replaces the two exact-chip catalogue gates with discovery from selected STM32/Nordic
sources. The profile scope and verification below record this earlier new-overlay increment.

## Delivered behavior

Opening a registered/imported project with an unambiguous missing overlay destination
automatically opens an empty canonical draft and loads the selected chip. The editor
shows **New overlay · Unsaved** and the Save destination. It needs no Create overlay or
extraction action. Opening persists private recovery data but creates no application
overlay or missing directories.
Reopening an untouched empty draft reuses its recovery record when the destination and
selection evidence still match. Repeated restarts therefore do not consume new recovery
slots for that draft. Any draft with edits or history, including one undone back to
empty, remains separate and requires explicit recovery.

GPIO/UART/I2C/SPI activation uses the same source/history path as existing overlays for
STM32F446 R(C/E)Tx and Nordic nRF52840 QIAA. Source edits, pin assignments and Undo/Redo
remain available before first Save. Undo to blank leaves an unsaved new-file draft;
Reset new draft clears source/history without creating a file. This increment adds no
other silicon profile, device recipe or advanced peripheral.

**Review and Save** identifies the exact new path, candidate source and any required
configuration inclusion. **Create overlay** publishes that reviewed candidate only if
the destination and its directory identities still match. Even an empty draft is a
real creation. A competing file, including an empty file or link, is preserved. Missing
parent directories are staged with the overlay and become visible only during Save.

After Save, the same document and canonical history use the existing-file baseline.
Undo changes the draft, not the file's existence. A later Save uses the existing guarded
replacement flow. Neither Save nor opening runs Configure, builds or firmware.

Projects includes the saved overlay in the selected configuration when its existing
rules do not already predict use. It preserves other overlays, Kconfig inputs, CMake
and other configurations. File creation and inclusion are separate results: a failed
second step keeps the file, reports pending inclusion and offers **Retry configuration
inclusion**. That retry never republishes overlay source. Changed source/context needs
a fresh review; a fresh review after restart can also find pending inclusion evidence.
Configure remains a separate action to establish captured build participation.

Acknowledged new drafts retain source and Undo/Redo history through restart. **Find
recoverable drafts** compares the retained record with the current destination before
explicit restoration. Restore changes no application files. Existing recovery records
remain readable; unknown or malformed records remain preserved. A destination that
appeared since drafting requires reconciliation rather than silent overwrite.

## Implementation boundaries

- `ProjectsEditorContextProvider` supplies a proposed exact-target destination;
  `EditorService` exposes only a validated absent virtual document. An ordinary missing
  file is not permission to create it. Unregistered directory contexts lack the saved
  target/configuration required for this automatic flow.
  If an append selection requires both a missing custom overlay and a different missing
  default overlay, source editing and Save remain available but pin activation is
  withheld. The reason names the other missing input; restoring it or adjusting the
  configuration is required before an authoritative single-overlay observation.
- `DraftService` retains schema-1 browser payloads with explicit baseline existence;
  durable schema-2 records preserve absent identity and read schema-1 existing baselines.
- `OverlayPublisher` uses Linux no-replacement rename for creation, including missing
  parent subtrees. Schema-2 creation journals remain distinct from existing schema-1
  replacement journals. Uncertain publication retains evidence without replay/rollback.
- `ProjectOverlayProvider` is a public core protocol composed by `app.py`; Projects
  owns inclusion planning, revision checks, idempotent updates and its schema-1 durable
  registration records. Device Tree Editor does not access Projects storage directly.
- The browser automatically binds the draft, distinguishes first Save and Reset, guards
  recovery/retry responses, and preserves focus through ordinary actions. Unsupported
  composition remains visibly restricted; no hardware capability is inferred from a
  successful file creation.

## Verification

- Final `python scripts/check.py` passed: 1,407 Python and 508 frontend tests, including
  loopback integration, lint, syntax and architecture checks. Quick checks also passed
  during implementation.
- Input-composition and untouched-draft reuse changes passed 68 focused backend tests:
  20 provider, 9 absent-draft, 20 ordinary-draft and 19 recovery cases. Publication,
  Save and missing-overlay integration passed 52 focused cases after the final fix.
- `python scripts/verify_package.py` passed installed-wheel imports, launcher and
  runtime-asset checks.
- Six focused frontend suites passed all 161 cases, including absent versus existing
  empty files, first Save cancellation, history before/after creation, recovery,
  inclusion-only retry, changed drafts, navigation races and keyboard focus.
- Real local-source smoke checks passed for `nucleo_f446re/stm32f446xx` and
  `nrf52840dk/nrf52840`: automatic empty drafts discovered 50/48 logical pins, activation
  and history survived explicit restart recovery, and first Save created and included
  each overlay. STM32 needed an appended selection; Nordic's existing automatic rules
  predicted its file. These checks did not establish runtime hardware behavior.
- Added backend cases cover absent/empty baseline separation, durable blank drafts,
  recovery versions, directory and leaf races, links, parent replacement, interrupted
  creation, registration failure/retry, Original/named configurations and pending
  inclusion after restart. Existing source and unrelated records are preserved.
- Actual one-level and two-level missing-parent creation passed on tmpfs and Btrfs.
  Fresh directory descriptors verify staged contents before and after publication,
  preserving the unexpected-content guard across filesystem directory-offset behavior.
- Configure and full builds passed for both reference targets using their persisted
  project recipes, local Zephyr 4.4.2 and SDK 1.0.1. Configure discovered the newly saved
  overlays; generated DTS retained STM32 GPIO/UART and Nordic GPIO/SPI assignments.
  Both ELF outputs were produced and application sources remained unchanged by the
  checks. The captured append/automatic selection, rather than an extra test overlay
  override, supplied the new files.
- Browser checks passed automatic loading, GPIO Apply and Undo/Redo while the destination
  file and parent remained absent, plus restart recovery with restored Undo/Redo.
  Home/feature navigation preserved the unsaved draft. After the filesystem guard fix,
  recovery preserved the failed Save's candidate and a fresh reviewed Save created the
  file, added the selected append recipe and displayed success. Undo to blank left the
  published bytes unchanged; Redo returned to a clean draft. Leaving and reopening the
  feature after Save retained the selected overlay and clean source, with no browser
  console errors. At 520 × 820 pixels, review
  footer actions remained visible without horizontal overflow. Earlier activation
  checks remain historical evidence in their own handoff.

No physical hardware operation has been validated.

## Remaining gates

ESP32, K64 and RP2040 profiles and the five-profile acceptance matrix remain open.
Other STM32/Nordic chips were outside this increment's two exact profiles; the subsequent
automatic chip support handoff records their source-driven extension. Broader or opaque
input composition, advanced peripheral authoring, external-device composition
and GPIO startup hog modes remain outside this stage. Do not restore the old
sensor/module workflow.
