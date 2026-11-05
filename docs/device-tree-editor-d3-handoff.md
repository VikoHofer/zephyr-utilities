# Device Tree Editor D3 handoff

Status: complete within the session-draft preview scope, verified on 2026-11-05.
D2 was committed as `690f60d` before D3 work. The default feature remains
planned; the explicitly injected development preview now supports in-memory editing.

## Delivered behavior

D3 connects the D2 source engine to one canonical server draft and one shared undo/redo
history for source and visual changes. The preview supports status and literal property
controls, source occurrence selection, an editable overlay, unsaved indication and a
diff against the observed file. Visual actions update the same source that manual
typing updates. The visible nodes describe source declarations, not an effective
hardware graph or verified peripheral capability.

Incomplete typing is retained without displaying a stale visual representation. Opaque
literal values remain read only in visual controls; their bytes remain available in the
source editor. Context changes, stale source/revision hashes, unsupported command shapes
and source limits produce visible failures without publishing firmware changes.

The preview's Save action remains unavailable. D3 adds no file writer, persistent draft
schema, configure/build execution or project registration. The standard built-in feature
is not promoted to an available visual editor before the D4 editing/save workflow.

## Canonical draft boundary

`features/devicetree/drafts.py::DraftService` uses the existing editor context service
and the shared authenticated router. Routes are POST under
`/api/devicetree/editor/drafts/`:

| Route | Additional request fields beyond workspace/context identity |
| --- | --- |
| `open` | `document_id` |
| `get` | `draft_id` |
| `source` | `draft_id`, expected draft revision/hash, complete UTF-8 `text` |
| `command` | `draft_id`, expected draft revision/hash, one typed `command` |
| `undo`, `redo` | `draft_id`, expected draft revision/hash |

Each request includes the current `workspace`, `context_id` and D1 `expected_revision`.
Mutations additionally require `expected_draft_revision` and
`expected_draft_sha256`. Unknown/missing fields and invalid types are rejected. The
supported visual commands are `set_status`, `set_property` and `remove_property`;
arbitrary source fragments are not accepted as typed values. The
[foundation contract](device-tree-editor-contract.md) records their exact payloads.

A draft is keyed by workspace path/identity, context fingerprint, document identity/path
and observed base hash. Reopening an equivalent context can recover the same draft;
another configuration, target, source observation or workspace cannot reuse it. Every
operation revalidates D1 context and document evidence. Revalidation occurs again after
parsing/projection and immediately before a mutation is committed. Editor and draft locks
use a consistent order.

Changed bytes increment the canonical draft revision monotonically. Undo/redo restore
exact prior bytes, including incomplete source, while advancing the revision. No-op
actions add no history and do not clear redo. A source edit after undo starts a new
history branch. Current source, base source and edit history remain in application
memory; firmware inputs remain unchanged.

## Browser retention and synchronization

`web/drafts.js` keeps acknowledged server text, newer local typing and an in-flight
request separate. Typing is debounced by 350 ms and sent serially. Pending source is
retained if a request fails, context navigation happens, or the server rejects a limit.
Visual controls use only the acknowledged current revision and are unavailable while
typing is pending, a request outcome is uncertain, or syntax cannot be represented.

Leaving/reopening a feature or changing project/configuration selects another
context-bound entry. A late response can update only its originating retained entry.
It cannot replace the visible text of a different context. On return, the session
revalidates and reconciles with the server. An uncertain visual command or undo is not
automatically replayed. Conflicting local/server text requires an explicit choice.

Browser textarea positions use UTF-16; parser/source spans use UTF-8 byte offsets.
Conversion functions and CRLF-aware source mapping support selection links and preserve
unchanged source line endings when the textarea normalizes its display. This does not
introduce a global source reformatter.

Node/property selection follows same-name occurrence order only while occurrence counts
remain stable; ambiguous or removed selections are cleared. The source field is briefly
read only during visual/history mutations, while ordinary source requests still accept
newer typing. Defensive late input is retained for review instead of silently replacing
a completed command or being undone as part of an earlier history request.

Retention is for navigation during this application session. A page refresh is not
durable recovery for unsent local typing, and stopping the server removes its drafts.
Persistent recovery and filesystem publication are D4 work.

## Bounds and incomplete-source behavior

- At most 16 server drafts. Dirty drafts and drafts with undo/redo history are never
  evicted to admit another; only untouched clean drafts are eligible for replacement.
- At most 8 MiB of combined base/current/history source bytes. An operation that would
  exceed the budget is refused before changing current source or history.
- The most recent 32 history entries are retained per draft. `history_truncated` and
  `history_limit` expose when older undo history has been dropped.
- Each source remains bounded to 256 KiB of UTF-8 without NUL bytes. Byte/encoding
  failures reject the submission while the client retains its pending text. Parser
  token/depth limits retain the raw draft with diagnostics and no visual nodes.
- Visual projection is bounded to 512 nodes and 4,096 properties. Larger drafts retain
  source editing and history but hide the graph with an explicit diagnostic.
- At most 256 source diagnostics plus summary/limit notices are projected. The linear
  single-hunk diff preview is bounded to 400 source lines and 64 KiB, including its
  truncation notice; the complete source remains available separately.

These bounds do not certify build validity, hardware compatibility or an effective
Device Tree. Source-only application contexts can still have unknown targets and SDKs.

## Verification

| Area | Result |
| --- | --- |
| Canonical draft service | 20 focused tests and Ruff passed during implementation |
| D2 public literal/capability projection | All 30 focused source-engine tests passed |
| HTTP route/authentication integration | Both HTTP tests passed, including all six draft routes |
| Frontend synchronization, retention and UTF conversions | 20 new D3 and 14 existing D1 focused tests passed |
| Quick repository check | Passed: 671 Python tests; 19 loopback tests excluded; frontend files passed |
| Full repository check | Passed: 690 Python and 291 frontend tests, plus lint, syntax and architecture checks |
| Browser actions, keyboard, narrow/scrolled layout and leave/reopen | Passed with synthetic sources at 600, 1280 and 1440 px widths |
| Installed-package assets | `python scripts/verify_package.py` passed, including the new browser module |

Backend coverage includes both synchronization directions, exact shared history,
incomplete/token-limited source retention, no stale nodes, projection/diff bounds,
literal/deletion conflicts, strict malformed requests, stale identity/revision/hash
guards, external file changes, workspace changes during projection, Original/named
configuration separation, retention budgets and no filesystem/tool/job side effects.

Browser checks exercised visual status changes, property insertion/removal, source-to-form
updates, both source selection links after Unicode text, shared keyboard undo/redo,
incomplete-source recovery, passive debounce without focus movement, and the diff.
Dirty/pending drafts survived feature navigation; Original/Debug and workspace switches
restored the correct separate drafts. Empty-overlay and missing-directory results remained
visible at narrow/scrolled positions. The final preview emitted no console errors or
warnings. The synthetic on-disk overlay was byte-for-byte unchanged after draft editing.
Deterministic frontend tests cover delayed requests, reconciliation, conflicts and typing
during source/visual/history requests. Temporary preview servers were stopped afterward.

These checks do not establish Zephyr build validity or hardware behavior. No firmware
configuration, build, flash or hardware validation ran for D3.

### Development performance observations

The following measurements use the direct canonical service API, including D1 context
revalidation before and after the operation, source parsing, visual projection, history
bookkeeping and diff generation. They exclude HTTP serialization, network transport,
the 350 ms typing debounce and browser rendering. Seven repetitions per case were timed
with `time.perf_counter()` on CPython 3.14.10, Linux x86-64, Intel Core i7-1265U.

Both workloads use synthetic directory-only D1 contexts and unchanged on-disk overlays.
The small workload is 71 bytes with one reference node and two literal properties.
The 64 KiB workload contains a root and 347 labeled children (348 nodes, 1,388
projected properties), each with a comment, compatible string, numeric `reg`, literal
status and opaque macro expression; remaining bytes are a comment. Each operation
toggles one status between `okay` and `disabled`, avoiding no-op measurements.

| Workload | Initial median / maximum | Final median / maximum |
| --- | --- | --- |
| Small raw-source submission | 0.86 / 1.04 ms | 0.95 / 1.12 ms |
| Small visual status command | 0.97 / 0.98 ms | 1.02 / 1.04 ms |
| 64 KiB raw-source submission | 429.15 / 436.44 ms | 45.81 / 48.13 ms |
| 64 KiB visual status command | 537.66 / 553.83 ms | 84.82 / 89.03 ms |

Profiling the initial implementation identified repeated whole-document token scans
while projecting each property: 4,174 `_tokens` calls consumed 1.204 s of a 1.836 s
profiled run. Source-document lookup maps and indexed token ranges removed that repeated
work without changing source identities or patch guards. Final measurements above used
a fresh process after that change.

All final observed canonical 64 KiB operations were below the earlier 100 ms target.
The browser's debounce, transport and rendering add to perceived interaction latency;
these service measurements do not establish an end-to-end browser budget. They are
observations, not worst-case bounds or cross-machine guarantees. Source is retained
throughout an operation and UI progress must remain visible.

## Next stage

Implement **D4 only** from the
[master plan](device-tree-editor-masterplan.md): reviewed single-overlay Save, guarded
publication, conflict handling, reload/discard and durable recovery. Reuse exact context,
source identity and hash guards; do not infer write authorization from a parsed draft.
Preserve source-only and incomplete-text recovery, and keep configure/build execution
for D5. Full module placement and pin configuration remain later stages.
