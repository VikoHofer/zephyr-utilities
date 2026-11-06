# Agent guide

## Current product priority

The user's 2026-11-06 direction supersedes the old sequential D-stage order:
**board/controller support and the beginner visual connection workflow come first**.
The subsequent MVP decision focuses delivery/testing on ST/STM32, Nordic, Espressif,
NXP and Raspberry Pi. The existing generic discovery foundation is sufficient to move
to the integrated visual workspace; do not delay it for exhaustive vendor, revision or
composition coverage. Add discovery depth when it blocks a concrete MVP workflow.
Unsupported controllers/configurations or connections must show a visible under-construction
notice, the specific reason and unavailable actions. Support is determined by observed
capabilities, not a blanket brand guarantee or a board-vendor blacklist.
Start from the selected board/SoC DTS, included sources, module/custom roots and
bindings across vendors. An empty application overlay must not imply empty hardware;
aliases such as `led0` are optional annotations, never discovery prerequisites.
The first reported acceptance board is `nucleo_f446re`. Use representative other
vendors to verify the shared discovery path before expanding advanced peripherals.
Connecting a supported device must generate the scoped application overlay changes;
users must not manually recreate board controllers to begin. Preserve upstream board
files. Keep advanced D11 work secondary until this basic workflow works. See the
[controller-first plan](docs/device-tree-editor-controller-first-plan.md).

The first controller-first slice now observes inherited board/SoC hardware and creates
BME280 I2C / GPIO LED overlay connections. Actual discovery was checked on Nucleo
F446RE, nRF52840 DK and FRDM-K64F; Nucleo connections and browser Save/reopen passed.
This is not universal pin routing. See the
[controller-first handoff](docs/device-tree-editor-controller-first-handoff.md) for limits
and the next acceptance gates.
The foundation is committed as `a0fa84b`, including previously verified D11d work.
The qualified-target continuation shares pure metadata parsing with Projects and supports
CPU/core and nested-variant DTS resolution; see the
[qualified-target handoff](docs/device-tree-editor-controller-targets-handoff.md).
It is committed as `cc32fb1`. The next feature priority is the integrated module
library, connection canvas and inspector with live source, under the scoped MVP plan.
The [MVP support handoff](docs/device-tree-editor-mvp-support-handoff.md) records the
implemented construction notices, capability blockers and complete/browser/package checks.

Zephyr Utilities is one local application with a Python backend and an English,
offline browser interface. `zephyr-utils` starts the entire application. Memory
Explorer and Project Creator's environment analysis, minimal application creation,
configuration and single-image builds are implemented. Build Inspector owns advanced
read-only build, source and binding inspection. Device Tree Editor supports existing-overlay
status/property and source edits, local binding-aware property/reference forms, UART/I2C/SPI
and GPIO topology forms, reviewed Save, draft recovery and explicit saved-source Configure
through Projects. D8 adds Nordic pin forms for the exact verified nrf52840dk/nrf52840
profile; other inherited hardware remains unknown. D9 adds vendor-independent local
module recipes, a logical source-derived canvas and separately saved presentation layout.
D8 is committed as `cfaf10e`; D0–D10 are implemented within their documented scoped limits.
The D9 handoff records full checks, actual-binding smoke and browser verification.
D9 is committed as `3cf6db1`. D10 adds source-linked resource diagnostics and guarded
removal reviews with explicit optional-property repairs; full checks, browser verification
and installed-package checks passed as recorded in the D10 handoff. D10 is committed as
`6403e43`. D11a local single-specifier PWM consumers and PWM LED forms are complete
within their scoped limits; full/browser/package checks passed in the D11 handoff.
D11a is committed as `24e79e6`. D11b adds bounded local ADC consumers and common channel
configuration; full/browser/package checks passed in the D11b handoff. D11b is committed as
`2f070ad`. D11c adds three explicit interrupt GPIO consumer profiles; full/browser/package
checks passed as recorded in the D11c handoff. D11c is committed as `40b3c0a`.
The UI scroll/focus fixes are committed as `6b73fa8`. D11d adds scoped local CAN
nominal settings and existing-transceiver selection; its handoff records verification.
The editor application picker excludes unavailable
registrations while Projects retains them for reconnect/removal. D11 remains partial;
its remaining family matrix is in the master plan. Broader pin adapters, advanced composition
and TinyML experiment analysis remain planned.

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
