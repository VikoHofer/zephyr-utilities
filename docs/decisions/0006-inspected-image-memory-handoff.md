# ADR 0006: Inspected image handoff to Memory Explorer

Status: accepted

## Context

P3 can share an inspected image with Device Tree. Memory Explorer already discovers
builds and captures durable reports, but a directory alone cannot identify the ELF the
user inspected. A sysbuild default, overwritten output or expired observation must not
silently redirect report generation.

## Decision

Extend the public build inspection service with `select_artifact`, returning a detached
selection with original workspace/build/domain, artifact identity and context checks.
Memory uses this public interface without importing Projects or Device Tree internals.
The selection has a verification operation independent of the expiring observation
cache. It retains the original directory identities and relevant metadata watches,
including absent metadata, rather than re-inspecting into a new identity.
Memory also pins the configuration, map and resolved DTS evidence it archives, including
expected absence. Unrelated binary/HEX changes do not invalidate a selected ELF capture.

Projects navigates to `memory/inspect/<observation>/<encoded-domain>`. Memory's preview
endpoint validates the observation and checks report readiness without executing tools.
Normal Memory discovery must resolve the same build directory and ELF as the selection.
An alternate filename or missing file is an error, not a fallback to another image.
Technical identity is available under a closed disclosure. Generating reports remains
an explicit user action.

The existing Memory job endpoint accepts an optional `selection` object. At submission
it checks the expected workspace and freezes the verified selection. Capture verifies
that selection before running tools, checks archived evidence against the observation,
and runs reports using the archived ELF. Original paths remain in snapshot build and
artifact metadata; recorded commands identify the actual archived input. Capture also
revalidates the original outputs before publication. Workspace changes, eviction and expiry cannot
retarget an accepted job. A changed selected ELF or context prevents publication.

Keep the version-1 Memory snapshot schema: the existing archived artifact hashes are
the durable evidence. Do not make the transient observation ID a persistent dependency.
Manual discovery, legacy endpoints, cancellation and normal snapshot handling keep their
current contracts. Browser disposal never cancels a server job.

## Consequences

- The same inspected image can be read in Device Tree and analyzed in Memory Explorer.
- Opening a handoff performs no report generation, configure, build or flash action.
- Reports remain tied to an existing ELF, with source freshness explicitly unknown.
- Queued work survives observation expiration while preserving its original identity.
- The inspector currently supports the standard `zephyr/zephyr.elf` path; custom output
  names remain usable through Memory's independent discovery workflow.
- No migration of saved snapshots, project records or attempt evidence is needed.
