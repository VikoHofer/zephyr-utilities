# Memory Explorer implementation contract

The implementation lives in `zephyr_utilities.features.memory`; the original
`zephyr_utilities.memory` imports remain compatibility aliases. Shared transport,
workspace, jobs and feature loading are specified in the [platform contract](platform-contract.md).

The application uses Python's standard-library HTTP server, PyYAML for build metadata,
and a bundled, buildless English HTML/CSS/JavaScript frontend. Report generation uses
the Python interpreter and footprint script associated with the selected Zephyr build.

## Python interfaces

- `memory.discovery.discover_builds(root) -> {builds: [Build], warnings: [str]}`
- `memory.discovery.inspect_build(path) -> Build`
- `memory.discovery.list_folders(path) -> {path, parent, folders: [{name, path}]}`
- `memory.model.normalize_report(raw, prefixes=None) -> Report`
- `memory.compare.compare_snapshots(baseline, candidate) -> Comparison`
- `memory.snapshots.SnapshotStore(root)` methods:
  - `capture(build_path, name='', notes='', emit=None, cancel=None) -> Snapshot`
  - `import_reports(ram, rom, name='', notes='') -> Snapshot`
  - `list() -> [SnapshotSummary]`, `load(id) -> Snapshot`
  - `rename(id, name, notes='') -> Snapshot`
  - `delete(id) -> {id, deleted: true}` removes only that snapshot directory, including
    raw reports and annotations. Shared content-addressed objects are retained because
    other snapshots and active captures can reference them; no garbage collection runs.

`emit` accepts one string log message. `cancel` is a `threading.Event`.
Validation failures raise `ValueError`; missing paths/snapshots may raise `FileNotFoundError`.

`Build`: `path`, `application`, `board`, `domain` (nullable), `elf_path`, `zephyr_base`,
`zephyr_version`, `workspace`, `python`, `toolchain`, `ready` (bool), `error` (nullable).
Metadata not available is null, not guessed. Additional fields are allowed.

`Report`: `total_size` (integer bytes), `tree` (normalized root), `leaves` (flat leaf records),
`warnings` (string array). Node fields: `id` (unique within report), `name`, `size`,
`identifier`, `path` (normalized hierarchical string), `section`, `address`, `children`.
Leaves use the same fields without `children`. Preserve hidden/unattributed bytes.

`Snapshot`: `schema_version`, `id`, `name`, `notes`, `created_at` (UTC ISO string),
`build` (Build or null for import), `reports` (`ram` and `rom`, either can be null),
`config` (dict), `metadata` (artifact hashes/provenance), `warnings` (string array).
Summary omits trees/config and adds `totals: {ram: int|null, rom: int|null}`.

`Comparison`: `baseline: {id,name}`, `candidate: {id,name}`,
`totals: {ram,rom}` (each `{before,after,delta,percent}`; nulls when unavailable),
`changes: {ram:[Change],rom:[Change]}`, `warnings: [str]`,
`config_changes: [{key,before,after}]`.
`Change`: `name`, `path`, `section`, `before`, `after`, `delta`,
`status` (`added`, `removed`, `changed`, `unchanged`), `ambiguous` (bool).

## HTTP interface

Memory endpoints are now namespaced under `/api/memory/` (for example,
`GET /api/memory/snapshots`). The original endpoints below remain supported aliases.
Session, folders, workspace and job observation/cancellation belong to the shared core.
Use `POST /api/memory/jobs` for new report captures; `GET /api/jobs` lists jobs across
features and includes each job's feature identity.

All responses are JSON except static assets and export downloads. Failures are
`{error: 'human-readable English message'}` with an appropriate HTTP status.

- `GET /api/session` -> `{token, initial_path, storage_path, version}`
- `GET /api/folders?path=...` -> list_folders response
- `GET /api/builds?path=...` -> discover_builds response
- `POST /api/jobs` `{build_path,name?,notes?}` -> `{id,status}` (202)
- `GET /api/jobs/:id` -> `{id,status,logs:[str],snapshot_id?,error?}`
  Status: `queued`, `running`, `completed`, `failed`, `cancelled`.
- `POST /api/jobs/:id/cancel` `{}` -> job state
- `GET /api/snapshots` -> `{snapshots:[SnapshotSummary]}`
- `GET /api/snapshots/:id` -> Snapshot
- `PATCH /api/snapshots/:id` `{name,notes?}` -> Snapshot
- `DELETE /api/snapshots/:id` `{}` -> `{id,deleted:true}`; missing snapshots return 404.
  The canonical route is `DELETE /api/memory/snapshots/:id`. Both require the session token
  and same-origin checks. The browser confirms the named snapshot before deletion and
  clears deleted IDs from the open report and comparison selections.
- `POST /api/import` `{ram:rawReport|null,rom:rawReport|null,name?,notes?}` -> Snapshot
- `GET /api/compare?baseline=ID&candidate=ID` -> Comparison
- `GET /api/snapshots/:id/export?format=json|csv` -> download

Frontend sends `X-Session-Token` from `/api/session` on all API calls other than session.
Same-origin and loopback Host checks protect local file and command operations.
No remote scripts/fonts/CDN dependencies. UI must support loading, empty and error states.
