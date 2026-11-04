# Agent guide

Zephyr Utilities is one local application with a Python backend and an English,
offline browser interface. `zephyr-utils` starts the entire application. Memory
Explorer and Project Creator's environment analyser are implemented. Project generation,
Device Tree editing and TinyML experiment analysis remain planned feature areas.

## Start here

1. Read the user's current request, `git status --short`, and the relevant code before
   changing files. Preserve existing user changes and unrelated work.
2. Read [architecture](docs/architecture.md) for boundaries and
   [contributing](CONTRIBUTING.md) for setup/checks. For a new feature, use
   [adding a feature](docs/adding-a-feature.md).
3. Select the smallest complete change that satisfies the request. The roadmap and
   research master plan provide context; they do not authorize implementing every item.
4. Keep user-facing text, code comments, and repository documentation in English.

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
