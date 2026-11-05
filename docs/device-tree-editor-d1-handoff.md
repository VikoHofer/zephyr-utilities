# Device Tree Editor D1 handoff

Status: D1 complete within its read-only preview scope, 2026-11-05. D0 and the master
plan were committed as `ed15b07` before D1 work.

## Delivered behavior

D1 opens registered Original/named configurations or unregistered application directories
without configuring, building or registering source. It shows exact target/revision/
qualifiers, selected Zephyr/SDK provenance and availability, ordered overlay intent,
predicted versus historical captured inputs, and an explicit application-overlay
destination. Current overlay text is read only. Missing applications and missing explicit
overlays retain an actionable unavailable state.

`core/project_context.py::ProjectContextProvider` is implemented by
`features/projects/editor_context.py::ProjectsEditorContextProvider`. Composition supplies
it as `context.project_context` after feature registration. The editor uses only that
public boundary, the shared workspace/router and bounded source reader. Projects keeps
ownership of registrations, profiles, attempts and environment data.

`features/devicetree/preview.py::PREVIEW_FEATURE` replaces the planned feature only in
an explicitly injected development/test feature list. The default built-in feature
remains planned, with no frontend or route callback. The preview uses the existing
server, session and shell; it is not a separate launcher or port. The injection pattern
is:

```python
from zephyr_utilities.features import builtin_features
from zephyr_utilities.features.devicetree.preview import PREVIEW_FEATURE

features = tuple(PREVIEW_FEATURE if item.id == "devicetree" else item
                 for item in builtin_features())
# Pass features to the existing Application(...) or create_server(...).
```

Inspector retains its canonical/legacy routes, transient catalogue identities, service
alias and hash redirects. Editor routes live under `/api/devicetree/editor/` and its
service uses the distinct `devicetree_editor` key. The [foundation contract](device-tree-editor-contract.md)
documents the exact POST payloads for `catalog`, `open`, `context`, `select` and `document`.

## Context and source protection

The service retains at most 16 contexts for 30 minutes from creation. Contexts are
workspace-bound and revision checked; overlay selection increments the context revision.
Follow-up reads revalidate registered selection, workspace/application identity, the
application CMake marker, overlay catalogue and hashes. Changed, expired or evicted
contexts require reopening. Browser responses are also guarded by request generation,
workspace, context revision and document identity/hash.

The browser hides cached source until the existing context has been revalidated after
leave/reopen. Workspace changes clear the old selection; late replies cannot restore it.
Explicit actions provide local progress/errors and reveal their result once. There is
no background polling or automatic focus/scroll change.

Source discovery is confined to the application, avoids symlinks, hidden files and
hidden/generated/tool directories, and excludes configured-build markers. Editor observations admit at
most 128 overlay candidates, 1,000 scan files, 256 directories and 16 directory levels.
Individual source files are limited to 256 KiB and an editor observation has an 8 MiB
read budget. Unsupported or incomplete source discovery is diagnosed rather than
presented as a complete input selection.

The exact-target default overlay is initially selected only when available and predicted
for that configuration. Otherwise the user explicitly chooses an overlay. Selecting it
changes session metadata only; it does not activate an unused file in a build recipe.
Historical attempt evidence remains historical, even when displayed beside current
source. `image` and `build_evidence` are null: D1 does not attach an existing build.

## Verification

Completed checks:

| Area | Result |
| --- | --- |
| Public project-context provider | 17 tests passed |
| Editor service and routes | 22 direct-dispatch tests and 1 HTTP integration test passed |
| Frontend request/lifecycle behavior | 14 tests passed via `node tests/frontend/devicetree-editor.test.mjs` |
| Frontend syntax | `node --check` passed for `index.js` and `state.js` |
| `python scripts/check.py --quick` | Passed; later regression additions are included in the final full run |
| `python scripts/check.py` | Passed: 617 Python tests and 271 frontend tests, including local HTTP integration |
| Browser | Passed with synthetic data at 1280 px and 600 px: Original/named configurations, overlay selection, keyboard submission, visible results from a scrolled page, empty directories, stale-source errors, workspace switching and leave/reopen |
| `python scripts/verify_package.py` | Passed: installed wheel imports, console entry point and runtime assets |

Regression coverage includes hidden-file exclusion, default-overlay suppression in
replacement mode, directory replacement during scanning and final text reads, workspace
switches during scanning, unsupported response schemas, stale revisions and late replies.
Browser console checks found no errors or warnings. Full HTTP checks used permitted
local loopback access; the temporary preview server and browser tab were closed afterward.
No configure/build or hardware validation was performed.

## Scope boundaries and next task

D1 introduces no persistent editor records or migration, no source mutations, no source
parser, no Save controls, no tool execution and no build/image attachment. Unknown
directory-only context stays unknown. The D0 in-memory prototype remains outside the
runtime application.

The next implementation stage is **D2 only** from the
[master plan](device-tree-editor-masterplan.md): introduce production source-preserving
document operations for the agreed subset, preserving comments/opaque syntax, correct
override/deletion distinctions, deterministic edits and inverse behavior. Start from
the D0 regression corpus and re-evaluate its intentionally conservative limits. Do not
add live UI mutations (D3), filesystem saving (D4), or validation/build execution (D5)
while delivering D2.
