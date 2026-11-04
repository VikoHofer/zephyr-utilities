# ADR 0002: Preserve evidence and storage compatibility

Status: accepted

## Context

Memory reports and saved snapshots already exist. The application is becoming a suite,
but a launcher or module refactor must not make prior results disappear or reinterpret
their measurement meaning. Future experiments will also need durable provenance.

## Decision

Keep `--data-dir` as the legacy memory snapshot directory. The shared context returns it
for `feature_data("memory")`; other features receive `.features/<feature-id>/` below that
directory. Preserve memory snapshot IDs, raw reports, content-addressed artifacts and
separate annotations. Normal startup does not migrate or rewrite stored evidence.

Retain original Python import paths and unnamespaced memory HTTP routes as compatibility
adapters while new implementation lives in `features/memory` and uses namespaced routes.
Persistent-format changes require explicit versions, migration behavior and tests.

Measurement records keep their scope and origin. Missing data is unknown, static
reservations differ from runtime occupancy, and independent high-water marks are not
automatically a simultaneous whole-system peak.

## Consequences

- Existing installations and snapshots continue to work after the structural refactor.
- New features have separate storage without choosing directories based on current shell
  location.
- The memory-oriented storage root is retained for compatibility; a later suite-wide
  layout change will require a deliberate migration rather than an implicit rename.
- Compatibility adapters add a small maintenance cost, which is covered by tests.
- Future TinyML records must reference experiment/build/model/run evidence explicitly;
  they cannot infer provenance from the current workspace state.
