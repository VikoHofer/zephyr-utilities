# Architecture

Zephyr Utilities runs as one local Python process serving one browser application. The
shared shell owns navigation and workspace selection. Features supply domain services,
HTTP routes and scoped browser modules through an explicit registry.

Memory Explorer and Project Creator's environment analyser are working features.
Project generation, Device Tree editing and TinyML experiments remain planned workflows
inside the same application rather than separate servers.

## Dependency direction

```text
CLI
 └─ app.py (composition)
     ├─ core/ (HTTP, feature contracts, workspace, jobs)
     └─ explicit feature registry
         ├─ features/memory/
         ├─ features/devicetree/  [planned]
         ├─ features/projects/    [environment analysis; creation planned]
         └─ features/tinyml/      [planned]

Browser shell → registered feature modules → shared authenticated API
```

`core/` does not import features. A feature may depend on public core contracts, but not
another feature's internal modules. The composition root supplies shared services. When
two implemented features need the same behavior, extract a focused core service rather
than copying it or coupling their private implementations.

## Source layout

```text
src/zephyr_utilities/
  cli.py                    # Arguments, default workspace and browser launch
  app.py                    # Application composition and server creation
  core/
    features.py             # FeatureDefinition and FrontendDefinition
    context.py              # ApplicationContext and feature storage
    http.py                 # Router, Request, Response and shared transport
    jobs.py                 # Shared job execution and cancellation
    workspace.py            # Shared selected workspace
  features/
    __init__.py             # Explicit built-in registrations
    memory/                 # Report discovery/generation/model/snapshots/comparison
      web/                  # Feature entry module, template and stylesheet
    devicetree/             # Planned editor scope
    projects/               # Environment analysis, metadata adapters, storage and UI
    tinyml/                 # Planned model/experiment-analysis scope
  web/                      # Shared HTML shell, styles and JavaScript helpers
  memory/                   # Compatibility imports for the first release
tests/                      # Python and frontend behavior checks
scripts/                    # Repeatable checks and feature generator
docs/                       # Contracts, decisions, contributor guides and roadmap
```

## Backend feature boundary

`FeatureDefinition` describes an ID, title, description, status, entry path, frontend
assets and a registration callback. `FrontendDefinition` identifies a feature's bundled
asset directory and entry filenames. `features/__init__.py` explicitly selects the built-in
definitions; there is no implicit import of arbitrary workspace code as an application plugin.

An available feature's `register(router, context)` callback registers its routes and owns
its service instances. A planned definition has no registration callback or frontend.
`GET /api/features` serializes the catalog for the shell.

Routes use `Router.add(method, path, handler)`, with named parameters such as
`/api/memory/snapshots/:id`. A handler receives a `Request` with method, path, query,
parameters and parsed body; `query_value(name, default)` reads a query value. It returns
a JSON-compatible value or a `Response` for explicit status, content type or downloads.
Feature handlers do not implement another HTTP server or bypass shared request checks.

New feature APIs use `/api/<feature-id>/...`. Memory's original unnamespaced endpoints
remain compatibility aliases. The old `zephyr_utilities.memory` Python imports also
forward to the current implementation. These compatibility surfaces are deliberate;
removing them requires a documented migration and regression coverage.

## Shared context and jobs

`ApplicationContext` provides:

| Interface | Purpose |
| --- | --- |
| `workspace.get()` / `workspace.set(path)` | One validated selected workspace for future operations |
| `jobs.submit(feature, task, resource_key=None, metadata=None)` | Queue cancellable work through the shared manager |
| `jobs.get(id)`, `jobs.list(feature=None)`, `jobs.cancel(id)` | Observe or cancel shared jobs |
| `services` | Feature-owned service instances assembled during registration |
| `feature_data(feature_id)` | Stable persistent storage namespace |

A task receives `(emit, cancel)` and returns a result mapping. Progress goes through
`emit`; long-running work checks the cancellation event and releases subprocesses and
resources on exit. Raise `concurrent.futures.CancelledError` when stopping for cancellation
so the job records a cancelled outcome. Resource keys prevent conflicting jobs from
operating on the same resource concurrently. A task captures its input paths at submission so changing the
active workspace cannot redirect an in-flight operation.

The application owns job-manager shutdown. Leaving a browser view disposes that view,
not its server job. Job results and logs remain available through the shared API within
the manager's retention limits; durable feature results belong in feature storage.

## Storage and compatibility

`--data-dir` retains its original meaning as the Memory Explorer snapshot directory.
`context.feature_data("memory")` returns that directory. Other feature namespaces use
`<data-dir>/.features/<feature-id>/`. This keeps existing snapshot IDs, `objects/`, raw
reports and annotations available without moving data during a normal launch.

The default memory directory remains under `$XDG_DATA_HOME/zephyr-utilities/memory`
or `~/.local/share/zephyr-utilities/memory`. It does not depend on the working directory.
Features must not write directly into another feature's namespace. Persistent formats
need an explicit schema/version and migration strategy; UI state and a filesystem path
alone are not adequate experiment provenance.

Project environment observations use schema version 1 under
`feature_data("projects") / "environments"`, with immutable snapshot files and an atomic
per-workspace pointer. Reopening serves saved evidence without rescanning; explicit
analysis checks fingerprints and can reuse an unchanged catalogue. Future formats are
rejected without overwriting their data. See [ADR 0003](decisions/0003-environment-observations.md)
and the [environment analyser contract](environment-analyser.md).

## Browser feature boundary

The shell fetches the session and feature catalog, loads a registered template and
stylesheet, imports its module, then calls `mount(root, context)`. The returned lifecycle
object supplies `navigate(view)` and `dispose()`.

The context supplies authenticated `api`, session metadata, shared workspace/catalog,
shell `navigate`, `notify`, feature-local in-memory `state`, and `refreshWorkspace`.
Only the shell owns `window` hash navigation. Feature code controls its local views and
scopes selectors/styles to its root. It removes listeners, timers and observers during
dispose and can restore state when mounted again.

The canonical paths are `/#/home` and `/#/memory/overview` (with other memory views below
the same prefix), plus `/#/projects/environment`. Earlier hashes such as `#overview` map to the equivalent memory view.
All browser assets are bundled with the Python package and served by the same process.

## Local access boundary

The HTTP service binds to loopback. Shared transport validates host/origin and requires
the session token for APIs other than session bootstrap. Request bodies are bounded and
parsed as JSON. Static assets come from registered package directories, not arbitrary
workspace paths. Keep these checks centralized when adding routes.

Selecting a Zephyr build selects its local tooling environment. Report generation runs
the selected footprint script against an existing ELF; it does not invoke a build or
flasher. Future editor and creator operations need a separate, explicit write path with
reviewable output and tests for destination handling.

Environment analysis reads workspace/module, hardware model v2 and SDK metadata through
feature-owned adapters. Local version/manifest/Git probes are bounded subprocesses;
analysis does not execute project CMake, configure firmware or install/update tools.
Sources, declared/discovered evidence, unknowns and diagnostics remain distinct. Only
`EnvironmentSnapshot` is implemented; shared configured-build and editor contracts are
still proposed and must not be inferred from this metadata catalogue.

## Measurement boundary

Memory Explorer preserves upstream totals and uncertainty. Static reservations, runtime
occupancy, observed peaks and physical capacity are different measurements. RAM and ROM
may both contain initialized-data costs. Duplicate aliases and source attribution can
change without a corresponding footprint change.

TinyML features will connect immutable build/model identities to runs, datasets and
observation conditions. Memory data alone does not prove timing, accuracy, schedulability
or worst-case resource demand. The [research roadmap](roadmap.md) and
[memory master plan](memory-explorer-masterplan.md) describe those later measurements.

## Decisions and extension points

- [ADR 0001: One process and explicit features](decisions/0001-one-application-explicit-features.md)
- [ADR 0002: Preserve evidence and storage compatibility](decisions/0002-evidence-and-storage-compatibility.md)
- [ADR 0003: Versioned environment observations](decisions/0003-environment-observations.md)
- [Adding a feature](adding-a-feature.md)
- [HTTP/browser integration contract](platform-contract.md)

The architecture is a maintained contract, not a promise that every future requirement
is already abstracted. Add shared interfaces for concrete needs and record material
changes in a short decision note.
