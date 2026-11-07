# Contributing

Contributions should extend a feature inside the existing application. Users activate
their Zephyr environment and run `zephyr-utils` once; every tool shares that process and
browser shell.

## Development setup

Use Python 3.10 or later and the workspace's existing Zephyr virtual environment:

```sh
source /path/to/zephyrproject/.venv/bin/activate
cd /path/to/zephyrproject/zephyr-utilities
python -m pip install -e '.[dev]'
zephyr-utils
```

The editable installation picks up Python and browser-source changes on the next
launch/reload. Reinstall after changing dependencies, packaging metadata or entry points.
Node.js 22 or later is needed for frontend development checks, not for running the
installed app. The development extra installs the Python lint and packaging tools.
There is no separate frontend development server or runtime CDN dependency.

To use an isolated data directory during manual testing:

```sh
zephyr-utils --data-dir /tmp/zephyr-utilities-check --port 0 --no-browser
```

The launcher prints its local address. Keep normal user snapshot data out of destructive
tests; automated tests create temporary data and fixtures.

## Find the right place to change

Read [architecture](docs/architecture.md) and the feature's existing code/tests. The
shared layer lives in `core/`, feature implementations in `features/<id>/`, and the
application shell in `web/`. The old `memory/` package supports compatibility imports;
do not add new implementations there.

Read the [feature guide](docs/adding-a-feature.md) before adding a tool. Use the
[roadmap](docs/roadmap.md) to understand planned ownership, then choose an explicitly
requested or agreed slice. Device Tree Editor, Project Creator and TinyML are development
areas, not completed products.

For Device Tree Editor board evidence or a new vendor/pin encoding, use the
[board support contribution guide](docs/device-tree-editor-support-contributing.md).
It identifies the discovery and routing extension points and the checks needed before
adding a board to the informational **Supported boards** submenu.

For a substantial feature, a short proposal can record its trigger, expected outcome,
contracts, test evidence and excluded work. The [proposal template](docs/templates/feature-proposal.md)
is optional for small changes. A focused bug fix usually needs a reproducer and a test,
not an architectural document.

## Implement a complete slice

1. Describe the user-visible outcome and acceptance cases.
2. Keep domain logic independent of HTTP and browser rendering.
3. Add feature-owned routes and frontend modules through the registry. Reuse the shared
   workspace, authenticated client, jobs and storage namespace.
4. Handle empty input, missing dependencies, failure, cancellation and repeated use.
5. Preserve existing saved data, public entry points and documented compatibility paths.
6. Add focused regression tests and update the relevant user/developer documentation.

An operation that writes firmware/configuration files should show the exact proposed
change and make application an explicit user action. Report inspection and navigation
must not silently change the selected project. Design editor/project-creation features
around reviewable writes and a clear destination rather than automatic rewrites.

## Checks

The canonical commands are:

```sh
python scripts/check.py --quick
python scripts/check.py
```

Use quick checks during iteration; run the full command before handing off a change.
Both modes run Ruff, Python/JavaScript syntax checks, architecture checks and unit tests.
The quick mode skips test classes marked `requires_loopback = True`; the full suite also
includes those local HTTP integration tests and therefore needs loopback socket access.
If a host blocks those tests, report exactly what ran and what remains unverified.

Also validate the paths affected by the change:

| Change | Additional evidence |
| --- | --- |
| Python algorithm or persistence | Focused unit/regression cases; old snapshot compatibility when relevant |
| HTTP route or shared service | Integration test through the shared server, including invalid input |
| Feature registration | Same-server test with an injected feature definition |
| Browser UI or lifecycle | Browser smoke test, keyboard access, empty/error states, leave/re-enter behavior |
| Asset paths or packaging | Build a wheel and verify the installed application includes the assets |
| Zephyr integration | A small real build when available; prove analysis did not rewrite its inputs |

For packaging changes, `python scripts/verify_package.py` checks the built distribution
and its installed import/asset paths in an isolated temporary directory.

Tests should assert observable behavior. Use small synthetic fixtures for boundaries,
malformed inputs, missing values and duplicate identifiers. Keep machine-specific paths
out of fixtures. Do not require a local SDK, board or network service for the normal unit
suite; mark and document separately any future hardware integration tests.

## Concurrent human and agent work

Start with `git status --short`. Preserve unrelated working-tree edits. Assign concrete
files or feature directories when splitting work, and agree on contracts before both
sides start implementing them. Communicate changes to shared interfaces immediately.
The [handoff template](docs/templates/task-handoff.md) keeps long-running work resumable
without treating an unfinished plan as a completed feature.

Keep changes reviewable. Avoid unrelated formatting, dependency upgrades or large
abstractions without a concrete need. Documentation and task notes should distinguish
implemented behavior, accepted decisions and proposed work.

## Pull requests and issues

Describe the concrete problem and resulting behavior first. Include the validation you
actually performed and any material migration or compatibility effect. For UI changes,
include a representative screenshot; for a regression, include the reproducer or test.
The repository templates provide prompts, not requirements to fill irrelevant sections.

Do not include captured builds, private configuration, tokens, full device logs or model
datasets in issues or PRs. Prefer minimal sanitized examples. This repository does not
assign contributors or reviewers automatically; coordinate ownership with the people
working on the affected area.
