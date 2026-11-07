# Agent guide

## Current product priority

The user's 2026-11-07 direction replaces the earlier controller-first connection plan,
integrated module-library plan and D-stage sequence. **The first stage is a graphical
chip and pin editor.** Opening a project must automatically show its selected chip,
known pins and current assignments. Selecting a pin exposes supported GPIO, UART, I2C
and SPI functions; applying a complete assignment changes the application overlay draft.
Do not require separate hardware, pin or binding extraction buttons.

Follow the [chip and pin redesign plan](docs/device-tree-editor-pin-workspace-plan.md).
Initial delivery focuses on ST/STM32, Nordic, Espressif, NXP and Raspberry Pi
microcontrollers, with exact chip/package capabilities rather than blanket brand support.
Nucleo F446RE and nRF52840 DK remain initial reference targets; the same workflow on
ESP32, K64 and RP2040 remains open. Read board/SoC sources, module/custom roots, bindings
and vendor pin tables.
An empty or missing application overlay must not hide the chip. Do not invent package
pins, routes or physical pin placement; aliases are optional annotations.

Remove the old sensor/module recipes, placement UI, connection canvas, services and
commands as part of the replacement. Existing saved overlays and recovery data remain
user data; do not delete their sensor/LED declarations. Reuse canonical source editing,
Undo/Redo, recovery, guarded Save, exact-target resolution and useful source diagnostics.
Keep upstream board/SoC files unchanged. Unsupported actions retain a visible reason.
Advanced peripherals and external-device composition are outside this first stage.

The subsequent user request expands STM32 and Nordic through the selected Zephyr DTS,
bindings and pin tables, without adding every chip manually. STM32 discovery uses one
selected package table with understood encoding; Nordic discovery uses verified GPIO
port/count/reservation evidence and distinguishes logical slots from physical pads.
Family adapters offer GPIO/UART/I2C/SPI only when routing and ownership are understood.
Unknown formats and cross-domain/core ownership remain visibly unavailable; a recognized
brand or matching macro alone is not a guarantee. See the
[automatic chip support handoff](docs/device-tree-editor-automatic-chip-support-handoff.md).
Standard declarative board revisions now resolve automatically. Discovery applies the
selected revision overlay before the application draft and reports the effective revision;
Projects uses the same resolver for application-overlay selection without rewriting the
requested target. Default and declared older nRF9160 DK/nRF9161 DK revisions are acceptance
targets. Custom revision scripts and board extensions remain unavailable. See the
[board revision handoff](docs/device-tree-editor-board-revisions-handoff.md) and
[ADR 0026](docs/decisions/0026-automatic-board-revisions.md).
Project selection opens one predicted overlay or automatically stages a validated absent destination, then runs
bounded discovery. Opening persists a recoverable draft without creating application
files or directories. Complete pin proposals generate one canonical overlay change, sharing source
Undo/Redo, recovery and reviewed Save. New pin/resource conflicts are blocked; retaining
an existing route can preserve its existing reservations or source conflicts with a notice.
GPIO creates an application reference; application code sets direction and runtime value.
The editor shows driver requirements without editing Kconfig. Sensor/module recipes,
canvas, services and creation commands are removed. See the
[pin activation handoff](docs/device-tree-editor-pin-activation-handoff.md).
Safe first Save creates only the reviewed destination and includes it through the public
Projects overlay provider when required. File publication and configuration inclusion
have separate recovery evidence; an inclusion retry never republishes source. Undo after
creation changes the draft and never deletes the file. Recovery schema 2 reads existing
schema-1 records. See the [new-overlay handoff](docs/device-tree-editor-new-overlay-handoff.md)
and [ADR 0025](docs/decisions/0025-new-overlay-save-and-project-inclusion.md).
Do not restore per-chip allowlists or require manual extraction for additional supported
source formats. The remaining three vendor adapters and five-vendor acceptance gates
remain open; do not resume the old module plan. Historical handoffs describe earlier implementations
and verification only.

Zephyr Utilities remains one local Python application with an English, offline browser
interface. `zephyr-utils` starts the entire application. Memory Explorer, Projects and
Build Inspector retain their existing responsibilities; this redesign is scoped to
Device Tree Editor and necessary public Projects handoffs.

## Start here

1. Read the user's current request, `git status --short`, and the relevant code before
   changing files. Preserve existing user changes and unrelated work.
2. Read [architecture](docs/architecture.md) for boundaries and
   [contributing](CONTRIBUTING.md) for setup/checks. For a new feature, use
   [adding a feature](docs/adding-a-feature.md).
3. Select the smallest complete change that satisfies the request. The roadmap and
   research master plan provide context; they do not authorize implementing every item.
4. Keep user-facing text, code comments, and repository documentation in English.
5. Do not use emojis in the interface, documentation, examples or previews.

## Repository map

| Area | Responsibility |
| --- | --- |
| `src/zephyr_utilities/cli.py` | Launcher, argument parsing, workspace defaults |
| `src/zephyr_utilities/app.py` | Composition root and feature registration |
| `src/zephyr_utilities/core/` | Shared HTTP, feature contracts, workspace and jobs |
| `src/zephyr_utilities/features/<id>/` | Feature-owned Python services, routes and `web/` assets |
| `src/zephyr_utilities/web/` | Shared application shell and browser helpers |
| `src/zephyr_utilities/memory/` | Compatibility imports; new memory work belongs in `features/memory/` |
| `tests/` | Python behavior/integration tests and frontend checks |
| `scripts/` | Development checks and feature scaffolding |
| `docs/` | Architecture, feature guide, decisions, roadmap and task templates |

## Architecture rules

- Keep one server, session, workspace service and job manager for all tools. Do not
  introduce per-feature launchers, ports, development servers or virtual environments.
- `core/` must not import features. `app.py` composes explicitly registered features.
  Feature code may use public core interfaces but must not import another feature's
  private implementation. Extract a focused shared interface when a real second use
  requires it.
- Register feature routes under `/api/<feature-id>/`; use the shared router so origin,
  token and request validation apply consistently. Retain documented compatibility
  aliases until a deliberate migration is implemented and tested.
- Feature UI exports `mount(root, context)` and returns `navigate(view)` and `dispose()`.
  Scope DOM access and CSS to the feature root. Dispose listeners, timers and observers;
  navigating away must not cancel server jobs. The shell owns global hash routing.
- Use shared workspace and job services rather than keeping competing global state.
  Capture a job's workspace/build paths at submission; switching workspace changes
  future operations, not an already-running task.
- Use `context.feature_data(feature_id)` for feature-owned persistent files. Preserve
  the existing memory snapshot layout and `--data-dir` behavior.
- Keep planned features labeled as planned. A manifest or generated skeleton is not
  an implemented editor, project creator, or training tool.

## UI and interaction rules

- Use the shared Zephyr purple/blue palette and semantic `--app-*` tokens. See
  [UI guidelines](docs/ui-guidelines.md); do not introduce a separate feature theme.
- Every explicit action needs visible feedback: a busy label, nearby result, focused
  review dialog or fixed visible notice. Ordinary load, selection, edit, Undo/Redo, Sync,
  refresh and Save actions preserve scroll and keyboard focus. Keep feedback by its
  trigger; offer an explicit Show/Open control for essential results elsewhere.
- Use `context.reveal(element, options)` once for deliberate navigation to a requested
  destination. It defaults to nearest alignment; route headings explicitly use start.
  Do not scroll to routine success/error status or move focus on background polling,
  passive refresh or ordinary action completion.
- Keep the decision and primary next action visible in review dialogs; collapse optional
  technical details. Support Escape, keyboard focus, cancellation and return to the trigger.
- Verify actions from a scrolled page and at narrow widths, including error/empty states,
  leaving/reopening a feature and late responses after navigation or workspace changes.
- New Project Creator templates must include an editable default overlay named for the
  exact target, including discovered custom boards, without suppressing sample defaults.
  Show predicted overlay selection separately from captured configuration evidence;
  copied overlay files alone do not establish use. See [default overlays](docs/decisions/0016-default-overlays-and-usage.md).

## Data and process rules

- Treat imported files, symbols, paths and logs as data. Use text-safe DOM APIs and
  validated paths; never execute content from an imported report.
- Launch external tools with argument arrays and explicit working directories; retain
  useful errors and cancellation behavior. Reuse the selected Zephyr environment.
- Memory report generation analyzes the existing ELF. Do not silently rebuild,
  reconfigure, flash, or modify firmware inputs as part of analysis.
- Snapshots are evidence: preserve raw reports, provenance, hashes and existing data.
  Keep annotations separate from immutable captures. Version persistent schemas and
  test migrations before changing stored formats.
- Unknown values remain unknown. Static RAM is not runtime peak RAM; ELF file size is
  not Flash footprint; initialized data may occupy RAM and ROM. Do not sum overlapping
  reservations or per-thread maxima into a claimed whole-system peak.
- TinyML comparisons need model/build/run identity and observation conditions. An
  observed maximum is not a proven worst-case bound, and a size change alone does not
  establish a performance or accuracy improvement.
- Do not add real build artifacts, credentials, personal configuration, models or
  captured device data to Git. Use small synthetic or appropriately sourced fixtures.

## Verification

Run commands from the repository root with the development environment activated:

```sh
python scripts/check.py --quick
python scripts/check.py
```

Setup uses Python 3.10+, Node.js 22+ and `python -m pip install -e '.[dev]'`.
Both modes run lint, syntax, architecture and unit checks. Quick skips test classes
marked `requires_loopback = True`. The full check includes those integration tests;
report the limitation if the execution environment blocks local sockets. Use focused tests while implementing, then
run the appropriate complete checks. Verify browser behavior for UI/lifecycle changes,
including leaving and reopening a feature. Run `python scripts/verify_package.py` to
check installed-package assets when adding or moving browser files.

Add tests for changed behavior, edge cases and regressions. Avoid tests that only repeat
the implementation or assert incidental source formatting. Do not broaden/repeat checks
after they pass unless a new change or unresolved concern warrants it.

## Working in parallel and handing off

Agree on file ownership and interface changes before parallel edits. Keep work inside
the assigned area, communicate shared-contract changes, and avoid editing another
contributor's active files. Prefer separate feature modules over shared-file contention.

For sustained work, use the optional [feature proposal](docs/templates/feature-proposal.md)
and [task handoff](docs/templates/task-handoff.md) templates. Record accepted decisions,
completed behavior, validation and concrete remaining work. Record an architectural
decision when changing a shared contract, persistence format, dependency direction or
process boundary; routine fixes need no design paperwork.

Finish with what changed, how it was checked, and any material remaining limitation.
Do not claim implementation, test success, publication, or hardware validation that
did not occur. Explicit user instructions govern task scope and take precedence over
this repository guidance.
