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
    devicetree/          # Read-only resolved tree inspection
    projects/            # Environment analyser, creator, build attempts, API and web assets
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
- Device Tree source navigation uses `POST /api/devicetree/sources`, `/bindings` and
  `/source-file`. Requests retain expected workspace/inspection/domain; binding/file
  reads also carry a transient `catalog_id` and a node path or opaque file ID. No arbitrary
  source-path read route is exposed. See the [inspection contract](build-inspection.md).
- `POST /api/memory/inspection` previews an inspected image without starting work.
  `POST /api/memory/jobs` optionally accepts `selection: {workspace, inspection_id, domain}`
  alongside `build_path` to preserve that image's verified ELF and context through capture.
  The new preview is namespaced only; existing aliases retain their behavior.
- Job `metadata.view` can optionally name a relative subview in that job's feature,
  such as `create`. The shell validates this value before using it for the running-job
  badge; absent/invalid values fall back to the registered feature entry path. It does
  not name another feature or load a URL, and introduces no feature-specific shell import.
- `GET /api/projects/environment` returns the selected workspace's saved environment
  observation and latest retained analysis job without starting analysis.
- `POST /api/projects/analyze` submits offline environment discovery through the shared
  job manager. Its completed result contains `snapshot`. See the
  [environment API/data contract](environment-analyser.md) for options, schema and limits.
- `GET /api/projects/creator` reads the selected workspace's saved projects/attempts and
  retained creator jobs. Creation and execution use separate preview/apply endpoints:
  `POST /api/projects/preview` and `/create`, then `/build-preview` and `/build`. Mutation
  requests include the expected workspace. See the [creator API/data contract](project-creator.md).
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
and observers on dispose. The shared workspace picker re-invokes `navigate(view)` on the
active feature after changing the workspace; the view name may be unchanged. Leaving a
feature does not cancel server jobs. On re-entry,
memory can resume its last job through context.state and the shared jobs API. Projects
reloads the current workspace's saved observation/latest retained job and keeps discovery
path options separately per workspace in browser-session state. Its Environment and
Create application and Builds subviews dispose their local requests/timers when switching. Creator
form choices are workspace-scoped; reviewed plans are discarded on remount. Projects and
attempts persist separately from the shared job manager's in-memory retention.

The Home view is a working feature catalog. Planned features are clearly labeled and do
not pretend to implement editors or model training. Project Creator exposes environment
analysis and a minimal workspace-app creation/configure/build workflow. Advanced project
composition remains planned. Existing-build inspection hands a workspace-bound shared
observation ID and domain to the read-only Device Tree reader and Memory's explicit
report review (`memory/inspect/<id>/<encoded-domain>`). See the
[build inspection contract](build-inspection.md).
UI remains English and offline. No runtime CDN or hosted service dependency.
