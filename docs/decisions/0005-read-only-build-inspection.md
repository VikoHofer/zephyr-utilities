# ADR 0005: Shared read-only build observations

Status: accepted

## Context

P2 records reviewed creation/build attempts inside Projects. P3 needs to open existing
outputs, including builds created independently, and give Projects and Device Tree the
same image, target and file identity. Device Tree is now a second consumer. Importing
Projects' private services or loading arbitrary build pickles would violate the feature
and data boundaries.

## Decision

Add a focused `BuildInspectionService` in `core/builds.py`, composed by `app.py` and
provided as `ApplicationContext.builds`. Projects owns the open/re-inspect workflow and
its routes; Device Tree owns structural DTS reading and presentation. Neither imports
the other's private implementation. Existing Memory discovery and P2 attempt capture
keep their contracts; this change does not migrate evidence or introduce execution.

An explicit open request captures an existing workspace-contained build using bounded
regular-file reads. Support known CMake/build-info outputs and sysbuild domain metadata.
Keep source selection/order as reported by metadata, and label source freshness unknown.
The service neither executes tools nor recursively searches or modifies applications.

Retain a bounded, expiring server-session observation with original context and SHA-256
identities. Revalidation reports live, changed, missing or unavailable output states
without replacing the original metadata/hashes. Verified text reads select a captured
domain and allowlisted output kind, not an arbitrary client-supplied file path. Changed
context metadata or text requires a new observation. Reject symlink traversal and
directory replacement; reject metadata-selected domain paths outside the sysbuild root.

The initial Device Tree reader parses generated DTS as data. Preserve property text and
report unsupported structure with a raw-text fallback. Do not claim binding validation,
source attribution, driver enablement or hardware correctness. There are no write
routes or editor controls in this slice.

The shell remains responsible for navigation. Projects hands off an observation ID and
domain in the feature route; the receiving feature validates them through the shared
service against the selected workspace. After workspace selection, the shell calls the
active feature's existing `navigate(view)` hook again so it can immediately clear or
reload workspace-bound state without a polling timer. Feature requests are disposed and late results
ignored after navigation or workspace changes. An expired reference is an actionable
error, not permission to silently reopen a changed build.

## Consequences

- Existing builds can be inspected without a creator profile or environment rescan.
- Projects and Device Tree agree on the image, target and captured output identity.
- Current output availability is distinct from source freshness and build success.
- No new persistent schema or background filesystem watcher is needed.
- Reopening after server restart/expiry requires a new inspection.
- Full P3 source/binding navigation and Memory handoff remain separate slices. P4
  source edits need their own review, validation and conflict handling.
