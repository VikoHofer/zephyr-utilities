# ADR 0018: Registered custom board roots and reviewed reference scaffolds

## Context

Environment analysis accepts explicit board roots, but ordinary project work also needs
persistent registrations, folder lifecycle handling and a safe way to start a board
port. An application overlay changes an existing board configuration; it cannot replace
the board definition. Supported SoC metadata does not establish PCB wiring.

## Decision

Project Creator owns a workspace-scoped, version-1 registry of custom board roots under
its feature data directory. Registration, rename/reconnect and removal are reviewed
metadata changes. Stored directory identities distinguish an original directory from
a replacement at the same path. Missing or replaced roots are retained for management
and excluded from active registered discovery. Removal leaves source files untouched.
Affected saved configurations are reported rather than silently retargeted.

The environment service merges available registered roots with explicitly requested
analysis paths. Saved observations retain a registry fingerprint. Reads derive a stale
state when registration identity changes; they do not rewrite immutable observations.
Build rechecks use the same effective root selection. Existing observations with no
registrations remain compatible. Active workspace jobs prevent conflicting registry
mutations. Workspace changes do not retarget submitted jobs.

The guided creator uses the selected checkout's supported local reference ports. A
SoC-first flow filters matching references; a reference-first flow selects one directly.
Both retain explicit source provenance and inherited hardware choices. The bounded
adapter renames board identities and their local references while retaining SoC
identities, license evidence and the reference hardware configuration. Unsupported
layouts produce an explanation instead of an incomplete generated port.

The review contains all proposed source files and their new destination. Source and
destination observations are rechecked before publication, and the shared feature-owned
file publisher stages the reviewed bytes with no symlink traversal and atomically
publishes without replacing any existing directory. Metadata-only discovery and preview
never execute board build code. Configure and Build remain explicit existing actions.

## Consequences

Existing and generated custom boards use the same environment, profiles and build
services as built-in boards. Registrations and generated sources have distinct
lifecycles: registration removal cannot delete an externally maintained hardware tree.
Generated roots can be used independently of Utilities with ordinary Zephyr board-root
configuration.

Reference hardware settings are visible assumptions, not validation of a new PCB. New
SoC ports, arbitrary reference-layout conversion, visual hardware editing, flashing and
debugging remain separate workflows. Publication followed by a metadata-storage failure
leaves the created source intact and reports its location for explicit registration.
