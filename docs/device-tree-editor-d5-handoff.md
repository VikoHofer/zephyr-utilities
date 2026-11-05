# Device Tree Editor D5 handoff

Status: complete within the Save-then-Configure scope, verified on 2026-11-05.
D4 was committed as
`a980b1e` before this stage.

## Delivered workflow

Open a registered application/configuration and an existing overlay. Edit and use the
existing **Review and Save** workflow, then choose **Review Configure**. The dialog
identifies the exact saved candidate, target/configuration, selected environment,
fresh managed output, command and limitations. **Run Configure** submits the existing
Projects configuration workflow through a public provider. Opening, Save and review
do not execute configuration. Compilation remains a separate reviewed Projects action.

This stage uses **Save then Configure** for every supported layout. No faithful unsaved
staging adapter is implemented. Configuring original source paths preserves relative
includes and removal semantics without appending a temporary overlay or restoring
temporary live writes. The editor does not activate an excluded overlay or change
fragment order. Unregistered directories, incomplete/unsaved drafts, unavailable
selected tools and unsupported checkout adapter inputs are explained and blocked.

Progress, cancellation, retained logs and verified source diagnostic links are shown
near the action. **Open in Projects** selects the original application/configuration
and attempt. It does not start Build or automatically open a command review. Missing
registrations/configurations/attempts produce visible feedback. Generated files remain
read-only evidence and never become editor destinations.

## Boundaries and evidence

`core/project_validation.py::ProjectValidationProvider` exposes review, submit, result,
cancel and handoff. Application composition supplies the Projects implementation through
`context.project_validation`; the editor imports no private Projects workflow.
`ValidationService` binds the provider plan to the exact canonical draft revision/hash
and editor selection. Submission retries retrieve the existing job rather than launch
another Configure. A new draft revision cannot execute an old review.

The Projects adapter retains the selected profile, exact candidate bytes/hash and
application content fingerprint, plus conservative source-tree and executable evidence.
It checks selected CMake/Ninja/Python executables and required local Zephyr adapter files
without executing them during review. The existing environment recheck runs only during
explicit Configure. Bounded scans include DTS, bindings, headers, Kconfig, CMake and
Python inputs across selected source roots. Large files/headers may use filesystem
identity; arbitrary dynamic reads and complete tool runtime coverage remain unverified.

Before/after fingerprints and later result checks detect changes in observed inputs.
Generated output identity/hashes are checked before presenting current evidence.
Captured dependencies must establish that the saved overlay participated. A successful
Configure with an observed candidate is reported as **inconclusive** for overall
freshness because coverage is partial. It is not a hardware validation or complete
semantic pass. Missing/unsupported dependency metadata does not mean the overlay was
unused; use remains unestablished.

Diagnostics recognize selected-tool GCC and dtlib source locations. Only exact matching
candidate paths/hashes and bounded lines can navigate to editable source. Other paths
and generated output references stay plain text. Stale diagnostics cannot select a
newer candidate. Source links preserve UTF-8/UTF-16 and CRLF conversion behavior.

Jobs capture their original workspace and configuration. Leaving/reopening the editor
retains and resumes observation without cancellation; switching workspaces cannot
redirect accepted work. Draft changes immediately mark the displayed result stale.
Polling never moves focus or scrolls. Cancel and failure retain Projects attempt
evidence. Historical attempt additions no longer invalidate unchanged editor contexts;
source, environment and recipe changes still do.

## Limits and retention

- D3/D4 source, history, Save and recovery limits are unchanged.
- The editor retains at most 64 validation review references per application session.
  The Projects provider retains at most 32 plans and preserves active jobs when choosing
  an eviction. Unsubmitted reviews expire after 30 minutes. Re-review is required after
  expiration/eviction/restart; durable Projects attempts remain available.
- The browser retains up to 16 application/configuration/overlay job references. Jobs
  continue independently of those references and shared job-retention limits.
- Application scanning retains the existing 2,000-file, 5,000-directory, depth-16 and
  32 MiB bounds. The conservative selected-source scan permits 250,000 entries and
  30,000 directories, depth 32, and 32 MiB of small-file hashing; remaining relevant
  files retain filesystem identities. Source-root demos/tests/docs, hidden/cache
  directories and generated outputs are excluded from that scan.
- Selected executables are limited to 64 MiB each. Candidate source remains limited to
  256 KiB. Diagnostic extraction retains at most 100 bounded messages. Existing Projects
  command/log/time/output limits apply.
- No persistence schema migration is required. Optional `editor_validation` evidence
  belongs to existing Projects attempt records; D4 drafts/publication journals are intact.

See [ADR 0021](decisions/0021-editor-configuration-validation.md) for the public boundary.

## Verification

| Area | Result |
| --- | --- |
| Editor validation guards, routes and source links | 13 focused tests passed |
| Projects provider identity, cancellation, stale results and effective output context | 18 focused tests passed |
| Project selection/context regression | 18 tests passed, including unchanged contexts after new attempt history |
| Frontend validation lifecycle and races | 20 new tests passed |
| Projects navigation/handoff | 9 tests passed, including delayed initial catalogue loading |
| Quick repository check | Passed: 761 Python tests; 20 loopback tests excluded; all 27 frontend files passed |
| Full repository check | Passed: 781 Python and 336 frontend tests, plus lint, syntax and architecture checks |
| Installed package assets | `python scripts/verify_package.py` passed, including the new validation module |
| Browser review, cancellation, source diagnostics, navigation and narrow/scrolled layout | Passed against the normal application with a disposable real project |

Focused regressions cover changed candidate bytes/revisions/inodes, configuration
revisions, relative includes, bindings, headers, Kconfig, executables and generated
artifacts; queued input changes; partial or absent dependency metadata; effective target
mismatches; failed Configure; expired shared jobs; cancellation and switched workspaces.
Frontend tests cover lost replies, stale review responses, pending typing, late terminal
results, cancellation during polling and disposal without job cancellation. The browser
exposed an initial Projects catalogue-load race; the fix has four delayed-load tests and
was rechecked in the browser.

Real configuration used the installed Zephyr 4.4.2 and SDK 1.0.1 with target
`frdm_mcxn947/mcxn947/cpu0`, genuine environment analysis and normal Projects import.
Two attempts configured the exact saved overlay: removing a node declaration removed
it from generated `zephyr.dts`, while a relative `#include "relative.dtsi"` remained
present and appeared in captured dependency evidence. Source hashes were unchanged by
Configure. An unknown macro caused a failed Configure with an exact overlay line/column
diagnostic. Initial review took about 4.3 seconds and CMake configuration about 6.5
seconds on this machine; these are observations, not performance guarantees.

Browser checks used the real fixture through the normal builtin editor. A successful
Configure retained observed overlay use and matching target evidence while reporting
unknown overall freshness. Leaving/reopening preserved the job. Unsaved typing blocked
Configure and marked prior results stale; Undo did not silently rebind the old review.
Explicit cancellation retained a cancelled attempt. Save followed by Configure on invalid
source produced a visible failure, and **Show source line 2** focused and selected the
correct overlay line. Projects handoff selected that exact configuration attempt with
no Build review or execution. Escape returned focus to Review Configure, and the primary
action remained visible at a 600 by 780 pixel viewport and from a scrolled page.
The final browser console had no warnings or errors.

Temporary servers and browser tabs were closed, the viewport override was reset, and
the uniquely named disposable application/output directory was removed. Test evidence
remains under `/tmp/zephyr-d5-real-4386825fab`; no existing user project was modified.
No firmware compilation, flashing or hardware validation ran. A final filesystem check
confirmed the disposable project contained no `zephyr.elf`.

## Next stage

Implement **D6 only** from the [master plan](device-tree-editor-masterplan.md): local
binding-aware properties and references. Preserve the source command path and the
separate reviewed Save/Configure/Build boundaries. Modules, pin assignment and richer
topology remain later stages.
