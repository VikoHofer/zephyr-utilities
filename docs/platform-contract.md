# Application platform contract

Current integration contract for the modular application. See [architecture](architecture.md)
and the [feature guide](adding-a-feature.md) for implementation and contribution guidance.

## Layout and ownership

```text
src/zephyr_utilities/
  cli.py                 # One launcher; bare command opens Home
  app.py                 # Composition root, registers built-in features
  core/                  # HTTP/router, feature contracts, workspace, jobs; no feature imports
  features/
    __init__.py          # Explicit built-in feature registry
    memory/              # Existing analysis modules, service and HTTP routes
      web/               # index.js, view.html, style.css
    devicetree/          # Planned feature manifest and scope
    projects/            # Environment analyser, adapters, storage, API and web assets
    tinyml/              # Planned feature manifest and scope
  web/                   # index.html, shell.js, shell.css; shared client helpers
  memory/                # Compatibility imports for the original Python package
```

One `zephyr-utils` process owns all feature routes and browser assets. No per-feature
servers, ports or environments. `zephyr-utils memory` opens the same app directly at
`/#/memory/overview`; bare launch opens `/#/home`. Existing snapshot storage and
`--data-dir` meaning must remain compatible.

## Browser/backend integration

- `GET /api/session` retains `token`, `initial_path`, `storage_path`, `version`.
- `GET /api/features` returns `{features: [descriptor]}`.
- Descriptor: `{id, title, description, status, entry_path, frontend}`.
  `status` is `available` or `planned`.
  Available frontend: `{module, template, stylesheet}` URL strings, e.g.
  `/assets/memory/index.js`, `/assets/memory/view.html`, `/assets/memory/style.css`.
  Planned frontend is null. IDs: `memory`, `devicetree`, `projects`, `tinyml`.
- `GET /api/workspace` returns `{path}`. `PATCH /api/workspace` accepts `{path}`.
  Validate that the directory exists; changes affect future operations only.
- `GET /api/folders?path=...` remains a shared folder browser.
- `GET /api/jobs` returns `{jobs: [...]}` with feature identity and existing job status fields.
- `GET /api/jobs/:id` and `POST /api/jobs/:id/cancel` are shared job operations.
- Memory routes are namespaced under `/api/memory`: `builds`, `jobs` (POST),
  `snapshots`, `snapshots/:id`, `snapshots/:id/export`, `import`, and `compare`.
  Preserve legacy unnamespaced memory endpoints as compatibility aliases.
- Legacy memory job fields (`snapshot_id`, logs, status) remain available.
- `GET /api/projects/environment` returns the selected workspace's saved environment
  observation and latest retained job without starting analysis.
- `POST /api/projects/analyze` submits offline environment discovery through the shared
  job manager. Its completed result contains `snapshot`. See the
  [environment API/data contract](environment-analyser.md) for options, schema and limits.
- Shared job cancellation uses `POST /api/jobs/:id/cancel` with a JSON object body `{}`.
- Every API except session requires the existing session token. Preserve loopback host,
  origin and request-body protections. New routes go through the same router checks.

## Frontend module lifecycle

Feature modules export `mount(root, context)` (may be async) returning an object with
`navigate(view)` and `dispose()`. The shared shell loads the declared template into root
before mount, and loads the stylesheet once.

Context provides:

- `api(path, options)` authenticated JSON requests, matching existing options/body behavior;
- `session` (existing session data), `workspace` (`{path}`), `features` (descriptors);
- `navigate(path)` where path is `memory/overview` or `home` (without `#`);
- `notify(message, error=false)` for shared notices;
- `state`: a persistent plain object belonging to this feature, retained across mounts;
- `refreshWorkspace()` to read and publish the latest shared workspace.

Only the shell owns global navigation and hash routing. Memory retains its Overview,
Explorer, Compare and Snapshots views as local feature navigation. Preserve old hashes
`#overview`, `#explorer`, `#compare`, `#snapshots` by mapping them to memory views.
Feature code scopes DOM queries and styles to its root, and releases listeners, timers
and observers on dispose. Leaving a feature does not cancel server jobs. On re-entry,
memory can resume its last job through context.state and the shared jobs API. Projects
reloads the current workspace's saved observation/latest retained job and keeps discovery
path options separately per workspace in browser-session state.

The Home view is a working feature catalog. Planned features are clearly labeled and do
not pretend to implement editors, model training or project creation. The available
Project Creator entry exposes environment analysis only; creation controls remain absent.
UI remains English and offline. No runtime CDN or hosted service dependency.
