# Device Tree Editor foundation contract

Status: D0–D9 contracts, 2026-11-06. D4 adds reviewed existing-overlay Save and durable
draft recovery; D5 adds explicit saved-source Configure through Projects; D6 adds local
binding-aware property/reference forms; D7 adds local peripheral and bus-device topology.
D8 adds Nordic pins for the verified nrf52840dk/nrf52840 profile. D9 adds local module
recipes, a logical canvas and separately saved layout; its handoff records passed verification.
Broader adapters remain planned. D6 is committed as `564fcff`; D7 as `4caf359`; D8 as `cfaf10e`.
Full D7 checks and browser/package verification passed as recorded in its handoff.
The D0 executable evidence is a bounded in-memory source prototype under
`scripts/prototypes/`, outside the installed application. See the
[master plan](device-tree-editor-masterplan.md) and
[ADR 0019](decisions/0019-device-tree-editor-foundation.md). Verification is recorded in
the [D1 handoff](device-tree-editor-d1-handoff.md) and
[D2 handoff](device-tree-editor-d2-handoff.md), [D3 handoff](device-tree-editor-d3-handoff.md)
and [D4 handoff](device-tree-editor-d4-handoff.md), followed by the
[D5 handoff](device-tree-editor-d5-handoff.md), [D6 handoff](device-tree-editor-d6-handoff.md)
[D7 handoff](device-tree-editor-d7-handoff.md), [D8 handoff](device-tree-editor-d8-handoff.md)
and [D9 handoff](device-tree-editor-d9-handoff.md).

## Ownership and compatibility

The editor uses feature ID `devicetree`, service key `devicetree_editor`, API namespace
`/api/devicetree/editor/`, and shell view `devicetree/editor`. D1–D3 registered these only
through explicit `PREVIEW_FEATURE` injection. D4 registers the delivered overlay editor
in the default application; the preview definition remains available to existing tests.

Inspector retains both `context.services["inspector"]` and its alias
`context.services["devicetree"]`, pointing to the same service. Its existing POST paths
remain unchanged:

- `/api/devicetree/inspect`
- `/api/devicetree/sources`
- `/api/devicetree/bindings`
- `/api/devicetree/source-file`
- `/api/devicetree/application-sources`
- `/api/devicetree/application-source-file`

Existing `devicetree/inspect` and `devicetree/application` hash redirects still belong
to Inspector. D1–D3 use an explicitly injected development/test feature definition in
the existing application composition, replacing the planned definition rather than
appending a duplicate feature ID. This adds no server or launcher. D4 may
promote the production definition after its usable editing workflow passes its gate.

| Existing implementation | Audited capability and limit |
| --- | --- |
| `core/context.py` | Workspace, jobs, build observations, feature storage and optional public `project_context` provider |
| `core/project_context.py` | Read-only `ProjectContextProvider` protocol: catalogue, capture and revalidation |
| `features/projects/editor_context.py` | Projects-owned detached selection provider, supplied by application composition after feature registration |
| `core/source_files.py` | Bounded UTF-8 reads, hashes and guarded directories; no general source writer or public file-identity receipt |
| `core/builds.py` | Existing-output observation and selected artifact reads; source freshness remains unknown |
| `features/projects/configurations.py` | Separate original/named configuration revisions and reviewed edits |
| `features/projects/fragments.py` | Version-1 fragment envelope with independent Kconfig/overlay modes and ordered application-relative files |
| `features/projects/overlay_usage.py` | Predicted, captured direct and transitive evidence remain distinct |
| `features/projects/reviewed_files.py` | Guarded Linux exchange pattern for a restricted creator operation; not an editor writer |
| `features/inspector/api.py` | Owns the legacy aliases and shared transient catalogue identities |
| `features/inspector/parser.py` | Structural generated-DTS reader; unsuitable for editable overlay serialization |

## Public project selection

`ProjectContextProvider` in `core/project_context.py` has its first consumer in D1.
Projects implements it; application composition supplies it explicitly when Projects is
registered. Core does not import Projects, and the editor must not call
private `CreatorService` methods or read Projects-owned JSON directly.

The implemented methods are read-only:

```text
catalog(workspace: str) -> {workspace, projects, diagnostics}
capture(workspace: str, project_id: str, configuration_id: str | None)
    -> ProjectSelection
revalidate(selection: ProjectSelection) -> ProjectSelection
```

`catalog` lists explicit Original/named configurations and retains unavailable
registrations with their availability; unreadable entries produce visible diagnostics.
`capture` takes identifiers, validates their relationship and directory identities, and
returns detached data. `None` selects the original configuration; it never means the
most recent build. `revalidate` either returns an equivalent current selection or raises
a context-conflict/unavailable result; it must not silently substitute another profile,
target or environment. No provider method configures, builds, rescans environments or
mutates records.
Do not call the private build-preview workflow to obtain this data: opening an editor
does not require build readiness or create a retained executable plan.

For an unregistered application, the editor uses the public source reader to create a
partial context from its selected directory. This does not import/register the project
or invent a build profile. Target, environment and domain can remain unknown. Opening
a build is not supported in D1: `image` and `build_evidence` remain `null`. Historical
attempt evidence can accompany a registered selection, but does not prove that current
source matches that attempt.

### Version 1 selection and context fields

The following describes the D1 editor-context response. Each field is required, with explicit
`null` where allowed. Future fields require a deliberate schema change or a documented
optional extension; consumers reject unknown schema versions without overwriting data.

| Field | Type and rule |
| --- | --- |
| `schema_version` | Integer `1`, excluding booleans |
| `workspace` | Absolute validated workspace path |
| `workspace_identity` | Captured workspace directory identity; not accepted as authority from the browser |
| `application` | Path, name, availability and registered directory/ancestor identity; directory-only source guards remain server-side |
| `project` | `null` for unregistered sources, otherwise `{id: string, revision: integer >= 0}` |
| `configuration` | `null` when no saved configuration is selected, otherwise `{id: string or null, revision: integer >= 0, name: string, profile_version: integer}`; a new registered app has an Original configuration before its first Configure |
| `target` | `null`, or `{requested: string, board_id: string or null, revision: string or null, qualifiers: string[]}`; retain the original exact target string |
| `environment` | `null`, or selected observation/tool/root identities with availability and staleness evidence; no implicit replacement with the active shell environment |
| `fragments` | `null`, or the detached version-1 selection envelope described below |
| `default_overlay` | `null`, or preserved project metadata plus its path, exact target and selection evidence |
| `roots` | Ordered records of `{kind, path, origin, identity, availability}`; evidence for unknown/dynamic roots remains partial |
| `input_evidence` | Separate `predicted` and `captured` arrays, `coverage`, and optional current-source fingerprints |
| `build_evidence` | `null` in D1; existing-build attachment is a later capability |
| `image` | `null` in D1; no image/domain is inferred from a project directory or historical attempt |
| `editing_document` | `null`, or selected validated application-relative overlay path with observed hash and predicted/captured usage state |
| `diagnostics` | Bounded records of code, severity, message and optional source reference; no raw HTML |

`ProjectSelection` is the registered portion above. The editor wraps a selection or
partial unregistered source context as `EditorContext`, adding `id` (opaque server
session identifier), `revision` (positive monotonic session integer), `fingerprint`
(SHA-256 of the captured canonical context), and `capabilities: {edit: false, save: false}`. Identifiers are
not filesystem permissions. Server-side validated selection remains authoritative.

Original configuration revision comes from `project.profile_revision` (legacy default
zero); a named configuration uses its own `revision` (legacy default zero). The separate
project registration `revision` must also be captured: reconnecting a directory changes
context even when a named configuration is unchanged. D1 tests both independently.

The fragment envelope retains `schema_version: 1`, `kconfig` and `overlays`, each with
`mode: "auto" | "append" | "replace"` and ordered `files: string[]`. Reuse Projects'
validation, including its 32-file limit per kind. Legacy profile version 1 represents
automatic discovery; version 2 carries the fragment envelope. Do not persist a profile
migration merely by opening it.

Keep this requested selection separate from `input_evidence.predicted` and
`input_evidence.captured`. Each evidence record contains `status`, `message`, `scope`,
`target`, `files`, `board_files` and `provenance`. File records preserve `path`, `state`,
`reason` and `role`; provenance identifies the configuration/revision and, for historical
evidence, the attempt and capture time. Available source hashes are separate fingerprints;
the enclosing coverage remains `partial`.
A current file observation, old configure attempt and creation recipe are different
evidence. Null or incomplete input order must not be turned into an authoritative empty
list. Later overlays can shadow an edit, and includes can introduce unknown contributors.

### D1 routes and lifetime

All routes are POST under `/api/devicetree/editor/`, use the shared authenticated
router, and reject extra/missing request fields. All request bodies include the exact
current `workspace` string.

| Suffix | Other request fields | Result |
| --- | --- | --- |
| `catalog` | None | `{workspace, projects, diagnostics}`; each project contains its explicit saved configuration choices |
| `open` | Either `project_id`, `configuration_id` (null for Original), or `application_path` | A new version-1 editor context with revision `1` |
| `context` | `context_id`, `expected_revision` | The same context after revalidation |
| `select` | `context_id`, `expected_revision`, `document_id` | The same context with the selected available overlay and revision incremented by one |
| `document` | `context_id`, `expected_revision`, `document_id` | `{context_id, revision, document}` with verified UTF-8 source text |

Catalogue project records contain `{id, name, path, availability, configurations}`;
configuration choices contain `{id, name, revision}`, with a null ID for Original.
The context includes `documents` containing `{id, path, relative_path, sha256,
availability, reason, origins, usage}` and an optional `editing_document` from that list.
Availability is `available`, `missing` or `unavailable`; usage is `unknown`, `predicted`
or `captured`. The `document` response adds `text` to this same summary.
The exact-target default overlay is selected on open only when available and predicted
to participate in the chosen configuration; otherwise the user explicitly chooses a
destination. Selecting an overlay changes session metadata only. Capabilities remain
`{edit: false, save: false}` and no write endpoint exists.

The server holds at most 16 contexts for 30 minutes from creation. Expired contexts or
those evicted to admit newer ones require explicit reopening. Requests use positive
integer revisions, excluding booleans. There is no persisted context, draft, watcher or
background refresh. Leaving/reopening revalidates the retained context reference before
showing source; expired/changed context does not silently reopen a new selection.

Source discovery is application-confined, bounded to 128 overlay candidates, 1,000
candidate scan files, 256 directories and 16 directory levels in the editor service.
Individual overlays and the application CMake marker are limited to 256 KiB; the editor
source observation has an 8 MiB read budget. Hidden files, hidden/tool/generated directories and
configured-build markers are excluded. Symlinks are not followed. Missing explicit
overlays retain an unavailable entry; incomplete scans do not silently become complete
catalogues. The Projects provider also bounds its independent prediction reads.

Follow-up reads recheck workspace/application identity, the CMake marker, current
overlay catalogue/hashes and the captured project selection. A replaced directory,
changed file list, changed source, registration revision or configuration revision
requires reopening. Reading a historical configure report does not attach its build
or establish current source freshness.

Follow-up requests carry the editor context ID and expected revision. Reopening or
changing target creates a new context identity; late results cannot populate another
context. Distinguish invalid input, unavailable input, context conflict, unsupported
capability and expired context with actionable messages. These are domain categories,
not a requirement to replace the shared HTTP error envelope.

Use Projects' existing 32-lowercase-hex identifier validation for its record IDs, not
a new project ID scheme. The browser submits identifiers and selection intent, never
trusted directory identities, authoritative environment records or arbitrary commands.

## D2 in-memory source operations

`features/devicetree/source_syntax.py` and `source.py` implement the source layer inside
the installed package. They do not import the D0 prototype, read files, resolve includes,
evaluate macros, execute tools or register mutation endpoints. D1's browser remains
read only. This layer is a byte-preserving syntax index, not an effective Device Tree
or a complete DTS grammar/binding validator.

`source_syntax.parse(source: bytes)` returns immutable `Syntax`: original bytes/hash,
complete token coverage, flat node occurrences, direct property occurrences, known node
deletion occurrences and diagnostics. IDs combine the full document hash, construct
kind and byte span. Repeated overrides, repeated properties and duplicate labels stay
separate. The `complete`, `opaque` and `incomplete` states describe this parser's coverage;
`blocked` separately records whether source transformations are permitted. Incomplete
structure exposes no node/deletion index. Unrelated opaque property values may coexist
with editable literals; structural ambiguity blocks the document.

The command interface is:

```text
SourceDocument.parse(bytes, document_id=None, revision=0) -> SourceDocument
document.find_node(reference_or_path) -> Node       # rejects absent/ambiguous lookup
document.node(occurrence_id) -> Node
document.find_property(node_id, name) -> Property  # rejects absent/ambiguous lookup
document.plan_set_property(node_id, name, PropertyValue, property_id=None)
document.plan_set_status(node_id, "okay" | "disabled")
document.plan_remove_property(property_id)
document.plan_add_node(parent_id | None, name, label=None, properties=())
document.plan_remove_node(node_id)
document.plan_delete_property(node_id, name)
document.plan_delete_node(parent_id, child_name)
document.plan_delete_reference("&label" | "&{/absolute/path}")
    -> SourceChange
change.apply(document) -> new SourceDocument
change.inverse() -> SourceChange
```

`PropertyValue.boolean()`, `.string(text)` and `.cells(values)` accept typed values,
not arbitrary source fragments. Cells contain one to 64 unsigned 32-bit integers.
Node creation accepts at most 128 unique literal properties; root declarations cannot
carry labels. Source is bounded to
256 KiB, 32,768 tokens and 64 nested delimiters; names are bounded to 256 characters,
string values to 64 KiB of UTF-8 and document identifiers to 128 characters. Invalid
UTF-8/NUL input and resource overflows raise `SourceError`; no source is truncated.
The [D2 handoff](device-tree-editor-d2-handoff.md) records workload measurements.

`SourceChange` contains document identity, expected/result revision and hashes, ordered
nonoverlapping `TextEdit(start, end, before, after)` entries and operation diagnostics.
Applying it verifies the complete source identity/hash, expected revision, exact original
ranges and result hash. Changed bytes advance the revision; a no-op does not. An inverse
restores exact prior bytes under the result preconditions and advances the revision
again. Stale IDs, unrelated source changes, wrong documents and stale revisions cannot
silently apply. There is no persistent draft or Save receipt in this layer.

Literal updates preserve unaffected bytes, comments and line endings. Equal-length cell
updates replace only changed numeric tokens, preserving surrounding trivia and existing
hexadecimal style. A shape change that would discard an internal comment is refused.
Insertion uses local indentation/newline evidence without reformatting existing source.
Removing a declaration removes only its selected span, including content inside that
declaration; surrounding comments and other contributors remain unchanged.

Local override removal, `status = "disabled"`, `/delete-property/` and `/delete-node/`
are separate operations. Local removal can reveal inherited or earlier values; it does
not promise effective deletion. Explicit deletion inserts a directive and requires
later selected-environment validation. Conflicting local declarations/deletion targets
are refused where identified. Local node removal rejects visible references to its
labels or known paths; includes/macros are not resolved, so that is not a complete
dependency analysis. Undo reverses emitted directives as exact source changes.

UTF-8 BOMs, conditionals, physical line splicing outside recognized directives and
unknown structural constructs remain preserved but block transformations. Expressions,
phandle values, string lists, byte arrays and unsupported literal encodings are not
converted into invented typed values. Supported source occurrences beside opaque
expressions remain editable when their structure is unambiguous. D3 binds these
pure operations to a revalidated D1 context and handles UTF-8/UTF-16 position conversion.

## D3 canonical session drafts

`DraftService` owns one canonical source revision and one undo/redo history for each
context/overlay binding. It uses the existing editor context service and D2 engine.
Its key includes workspace path/identity, context fingerprint, document identity/path
and base hash. The D1 fingerprint also includes workspace identity for unregistered
applications. A reopened equivalent context can recover its draft; a different target,
configuration, source baseline or workspace cannot reuse it.

All draft routes are authenticated POST requests below `/api/devicetree/editor/drafts/`.
Every body includes `workspace`, `context_id` and `expected_revision` for the D1 context.
Unknown request/command fields and invalid types are rejected.

| Route | Additional fields | Behavior |
| --- | --- | --- |
| `open` | `document_id` | Open or recover the equivalent bound draft |
| `get` | `draft_id` | Revalidate and read its current canonical revision |
| `source` | Mutation guard plus `text` | Retain bounded UTF-8 source, including incomplete syntax |
| `command` | Mutation guard plus `command` | Apply `set_status`, `set_property` or `remove_property` through D2 |
| `undo`, `redo` | Mutation guard | Restore prior source bytes under a new revision |

The mutation guard is `draft_id`, `expected_draft_revision` and
`expected_draft_sha256`. A no-op retains its revision; accepted changes advance it.
Supported command shapes are:

```json
{"kind": "set_status", "node_id": "...", "status": "okay"}
{"kind": "set_property", "node_id": "...", "name": "current-speed", "value": {"kind": "cells", "value": [115200]}}
{"kind": "remove_property", "property_id": "..."}
```

`set_status` accepts `okay` or `disabled`. `set_property` accepts an optional
`property_id` to identify a specific occurrence; typed values are `boolean` with null,
`string` with a string, or `cells` with unsigned 32-bit integers. IDs belong to the
current revision. Node creation/removal and deletion directives remain source-only in
the D3 UI, even where the D2 engine has corresponding operations.

The service revalidates the D1 context again immediately before publishing a mutation.
Responses contain the draft identity/binding, source/base hashes, text, revision,
dirty/history flags, syntax diagnostics, source occurrence spans, literal property
values and a bounded diff. `capabilities.save` remains false. `SourceDocument` exposes
public `property_value` and `can_edit_node` projections; unknown values remain unknown.

Session retention is bounded to 16 drafts and 8 MiB of retained base/current/history
bytes. Dirty drafts and drafts with history are not evicted to admit another draft.
Undo/redo retain at most 32 recent entries and report `history_truncated` when older
undo entries are dropped. A capacity failure retains the existing server draft; the
browser retains the user's pending text. Closing the application loses session drafts.
There is no recovery journal or persistent schema migration in D3.

Each source remains limited to 256 KiB, valid UTF-8 without NUL. Incomplete input and
parser token/depth-limit failures remain recoverable text with visual controls withheld.
Visual projection is limited to 512 nodes and 4,096 properties. A projection overflow
withholds the visual model while source editing/undo remain available. The diff is a
linear-time single-hunk preview capped at 400 lines/64 KiB, with visible truncation.

The browser serializes mutations, retains pending text per binding and reconciles
uncertain results before retrying. A current visual projection is shown only after the
corresponding source has been acknowledged. Source linking converts UTF-8 byte spans
to textarea UTF-16 positions explicitly, preserving the original line-ending bytes
outside the changed range. D4 adds separate durable acknowledgement and publication;
the earlier session-only state is not durable evidence.

## D4 reviewed Save, reconciliation and recovery

D4 implements existing-overlay replacement only. All routes remain authenticated POST
requests under `/api/devicetree/editor/drafts/`. Ordinary draft mutations retain the D3
guards. Recovery storage is feature-owned; no project code or configure/build tool runs.

| Route | Request beyond D3 context binding | Result |
| --- | --- | --- |
| `review` | Draft mutation guard | Ready plan, incomplete-source refusal or current-file conflict comparison |
| `save` | Draft mutation guard plus `plan_id` | Saved/unchanged receipt and refreshed context/document/draft, or conflict/uncertain evidence |
| `compare` | Draft mutation guard | Base, candidate and current text plus a pinned `comparison_id` |
| `reload` | Draft mutation guard, `comparison_id`, `mode: keep` or `discard` | Rebase retained text or explicitly discard/reload without writing firmware |
| `recovery/list` | Only `workspace` | Bounded recovery summaries, publication evidence and diagnostics |
| `recovery/read` | Only `workspace`, `recovery_id` | Versioned retained source plus current-file comparison |
| `recovery/restore` | Context binding, `document_id`, `recovery_id`, `expected_recovery_revision`, `comparison_id`, `mode` | Explicitly restored unsaved draft bound to the reviewed current context |
| `recovery/discard` | Only `workspace`, `recovery_id`, `expected_recovery_revision` | Explicit recovery-record removal; source files remain unchanged |

Save plans and comparison tokens expire after ten minutes, with 32 retained entries each.
Plans bind exact candidate bytes, original identity/hash and directory ancestry. The review
includes a bounded diff, complete candidate text, unresolved semantic validation, overlay
usage and unknown effects on other configurations sharing the file. Successful Save
rebinds the same draft/history to the new baseline. Duplicate requests return a retained
response without replaying publication; 64 completed responses are retained in session.

The production draft payload reports persistence status and Save capability. Version-1
records under `drafts/` retain base/source bytes, hashes, revision, bounded history and
captured context/file observations. Recovery requires explicit comparison and never applies
firmware changes. Existing recovery records have 64-record/32-MiB total and 16-MiB per-record
bounds. The publisher retains at most 64 version-1 records under `publications/`, each at
most 4 MiB, plus displaced originals beside the overlay. Unknown/malformed records remain
untouched. No prior persistent draft schema exists to migrate.

Linux atomic exchange preserves supported file metadata and verifies both sides. A failure
before exchange leaves the destination untouched; after exchange, an uncertain result may
already be visible and retains evidence without automatic rollback/replay. Loaded publication
records require comparison after restart. Unchanged Save performs no file replacement or
publication-journal write. See [ADR 0020](decisions/0020-guarded-overlay-save-and-recovery.md)
for locks, identity checks, metadata support and the remaining external-writer race.

## Later persistent change-plan extensions

The following earlier design envelope also describes later create/multi-file extensions;
the preceding D4 section and ADR 0020 define the delivered existing-file format.
Source bytes are authoritative; the hardware graph is derived. Byte offsets belong to
the server's UTF-8 source representation, not JavaScript UTF-16 indices. D3 converts
editor positions explicitly and tests non-ASCII comments before using text selections.

| Record | Minimum version-1 contents |
| --- | --- |
| `SourceDocument` | Context ID/revision; validated document ID/path; original UTF-8 bytes/hash; identity receipt; draft bytes/hash/revision; token/span and capability diagnostics |
| `DraftRecord` | Schema version, full context fingerprint and selection, document path, base bytes/hash, draft bytes/hash/revision, original newline/BOM policy, recovery timestamp |
| `EditCommand` | Command ID/kind, context and document IDs, expected draft revision/hash, selected construct/span, typed arguments and inverse patch |
| `ChangePlan` | Schema version, immutable plan ID, context fingerprint/revision, draft revision/hash, one destination in D4, expected identity/base hash or explicit absence, exact candidate bytes/hash, validation reference and affected-configuration evidence |
| `SaveReceipt` | Plan ID, published path/identity/hash, original/recovery identity, outcome and time; only produced after verified publication |

When stored as JSON, bytes use a documented lossless UTF-8 string representation;
hashes always cover the original bytes, never normalized newlines or reserialized JSON.
Do not silently add/remove BOMs or normalize CRLF. D0 and D2 preserve BOM input but
reject it for editing. NUL/binary/invalid UTF-8
input cannot enter the editable document path.

Change plans are bounded server-held reviews with an expiry time and an explicit
`create` or `replace` operation. An expired/changed plan requires new review. Successful
publication consumes the plan; a retry retrieves its receipt rather than applying
the patch again. A failed/uncertain publication first enters recovery/reconciliation,
not automatic retry. D4 defines retention limits and tests duplicate requests.

Drafts, receipts and later journals belong under
`context.feature_data("devicetree")`, with versioned envelopes. D0 creates no records,
directories or migration. Introduce the concrete storage layout and compatibility tests
in D4 as recorded above. Unknown future versions remain untouched and explain why they cannot be opened.
Recovery never automatically applies source changes. Layout/recipe metadata cannot become
necessary for standalone firmware builds.

Dirty drafts are keyed by workspace/application identity, project and configuration
revision, target, domain and destination. Leaving a view retains its draft. Switching
context does not reuse it; returning reconciles current source and configuration first.
Two drafts of the same file still compete through the same Save conflict checks.

Missing semantic understanding is not malformed syntax. Opaque source remains visible
and preserved. The prototype only proposes literal replacements it can locate exactly;
it does not authorize D4 to save malformed text or declare unknown constructs invalid.
D2 supplies the complete/incomplete/opaque distinction described above for D3/D4.

## Initial support matrix

These targets are selected design/fixture references, not newly certified hardware.
The local Zephyr observation is commit
`fbab2f598d545ad043ce50a75fefa4550b053f0b`, `VERSION` 4.4.2. Runtime decisions use the
user-selected checkout and revalidated metadata, not this observation or a bundled list.

| Area | D0 evidence | Planned runtime boundary |
| --- | --- | --- |
| Nordic target | Exact identifier `nrf52840dk/nrf52840` in `boards/nordic/nrf52840dk/nrf52840dk_nrf52840.yaml` | Initial D1 context reference and D8 nRF52840 pin adapter target |
| Second pin family | `nucleo_f401re` in `boards/st/nucleo_f401re/nucleo_f401re.yaml`; SoC `stm32f401xe` in `board.yml` | D13 STM32 comparison target, not a D0 pin editor |
| Custom boards | Existing Projects custom-root registration contract | Synthetic custom-target fixtures first; no new board port or assumed wiring |
| Project shape | Registered/imported workspace app or partial unregistered source context | One application/image and selected application overlay through D4 |
| Source language | UTF-8 synthetic overlays; lossless lexical coverage | D0 direct existing string or one numeric-cell property only |
| Opaque syntax | Includes, macros, conditionals and deletion directives retained as bytes | Conditional/deletion documents remain non-editable in the D0 prototype; D2 decides narrower safe spans |
| Pins and bindings | Selected local files can be read as evidence | No pin validity, binding evaluation or effective-tree interpretation in D0 |
| Writes and execution | Pure in-memory bytes and test fixtures | No firmware writes, source registration, configure, build, or external parser execution |

Use synthetic fixtures with provenance comments rather than copying complete board
ports. No real build artifacts, device captures or personal workspace settings belong
in the corpus.

## Source feasibility experiment

`scripts/prototypes/devicetree_source.py` is a disposable D0 experiment, not an installed
module or a stable API. It has no filesystem reader/writer or external tool integration.
The tests load explicit synthetic files from `tests/fixtures/devicetree_editor/`.

The intended API is:

```text
lex(source: bytes) -> tuple[Token(start, end, kind)]
round_trip(source: bytes) -> bytes
plan_literal_replacement(source, node_reference, property_name, replacement)
    -> BytePatch
BytePatch.apply(source: bytes) -> bytes
BytePatch.inverse() -> BytePatch
```

Tokens cover every byte, including whitespace and comments; round trip reconstructs
their spans rather than pretty-printing a semantic tree. A patch selects exactly one
existing property in one direct top-level `&label` or `&{/absolute/path}` block and
replaces only its complete string or numeric-cell value. Base and resulting hashes
guard forward and inverse application. Repeated blocks/properties, ambiguous structure,
nonliteral values, and conditional/deletion-sensitive input are refused.
Escaped physical newlines outside recognized opaque preprocessor directives also block
edits: preprocessing can splice a following apparent node into a line comment. The
prototype does not resolve that preprocessing phase.

Includes and macro expressions in unrelated properties may be preserved during an
accepted edit. They are never resolved. A successful patch means only that the intended
source range changed; it does not establish its effective contribution after preprocessing
or later overlays. Node creation/removal, inheritance, general arrays, phandle mutation,
forms and Save are D2 or later work.

Use 256 KiB, 32,768 tokens and 64 nested delimiters as initial experiment limits. They
are intentionally below the public reader's 2 MiB ceiling. D2 must choose and document
production limits from measured workloads; it must not silently truncate source. D0
tests boundary rejection, malformed input, no-op bytes, edit locality, stale patches and
exact inverse behavior. Known invalid typing can remain in a later draft even if the
prototype refuses to tokenize it.
Its structural checks are lexical/delimiter checks, not a complete DTS grammar validator.

Development performance budgets are at most 100 ms per edit on a representative 64 KiB
document and 1 s for bounded processing/rejection near the prototype's 256 KiB limit.
Record the environment, corpus shape and observed timings in the handoff; these budgets
are targets, not hardware-independent guarantees. Timing is measured separately from
unit tests to avoid flaky pass/fail assertions. D3/D15 set browser rendering budgets
against real measured UI workloads when that UI exists.

The dependency decision is to proceed to D2 with byte-span/concrete-syntax preservation
as the editing model. No parser package is added in D0. The prototype demonstrates the
approach only for its literal subset; D2 must evaluate richer parsing against the same
corpus. Inspector's parser and selected Zephyr `dtlib`/`edtlib` are semantic readers,
not replacements for the lossless source layer. Do not serialize their resolved tree
back into an overlay or deserialize imported `edt.pickle`.

## Filesystem concurrency policy for D4

The source prototype provides no filesystem race protection. Adopt the following writer
requirements now and test their implementation in D4:

1. Revalidate context/draft/base hashes and opened ancestor/destination identities.
   Refuse symlink traversal, non-regular source files and replaced directories. Define
   a hard-link policy before saving; the initial proposal rejects multiply linked files
   rather than silently separating their contents.
2. Lock by opened parent directory `(device, inode)` plus basename for participating
   application writers, not only by editor session, project ID or the old file inode.
   This stable key covers replacement and creation; check the original file inode
   separately. Coordinate with Projects' source mutations
   through a focused shared writer boundary when both become consumers. Locks must
   cover review recheck and publication; review itself does not hold a long-lived lock.
3. Stage exact candidate bytes on the same filesystem, preserve supported permissions,
   flush contents and use a recoverable exchange for an existing file. Verify the
   displaced file's bytes/identity against the reviewed original and verify the
   published candidate before returning success. Do not rely solely on a pre-write hash
   followed by an unconditional `os.replace`.
4. Publish a new file with a no-replacement operation. Never replace a destination that
   appeared after review. Make recovery records durable before irreversible cleanup and
   retain originals until successful publication is established.
5. On interference, report a conflict and retain both candidates for recovery. Restore
   the original only while the destination still matches this operation's candidate;
   never overwrite a newer external change during rollback. A crash/partial outcome
   cannot produce a success receipt or automatic restart replay.

The first writer can require Linux atomic-exchange/no-replacement support, consistent
with the current creator platform. Unsupported filesystems fail closed until an
equivalent tested protocol exists. An exchange verifies a displaced inode but is not
a content-based filesystem compare-and-swap. Nonparticipating processes can hold writable
descriptors or write during/after exchange; transient candidate visibility and later
external changes cannot be excluded. Retain recoverable evidence and describe this
limit rather than promising perfect isolation. Multi-file transactions remain D12.

## D5 configuration validation

`context.project_validation` implements the optional public
`core/project_validation.py::ProjectValidationProvider` contract. Projects owns the
implementation and command execution; the editor supplies a captured project selection
and exact `{path, text, sha256}` candidate. This first adapter accepts saved sources
only and configures a fresh managed output through the existing reviewed Projects job.
There is no staging, live temporary patch, implicit compile, or automatic activation of
an unused overlay. Directory-only contexts require registration and a saved configuration.

Authenticated POST routes under `/api/devicetree/editor/drafts/validation/` are:

| Route | Request | Result |
| --- | --- | --- |
| `review` | D3 context and draft mutation guards | `ready` with immutable plan, or `blocked` with an explanation |
| `submit` | Same guards plus `plan_id` | Captured shared Configure job; acknowledged duplicates reuse it |
| `status` | `workspace`, `plan_id` | Job, candidate/input identity, outcome, coverage, diagnostics and captured artifacts |
| `cancel` | `workspace`, `plan_id` | Cancellation request for that job only |
| `handoff` | `workspace`, `plan_id` | Projects application/configuration/attempt route, without executing Build |

Plans show exact source, command/environment, target, output directory and limitations.
Application content hashes, selected source-tree observations, tool identity/hash and
configuration identity are checked around execution. The bounded source-tree scan
includes DTS, bindings, Kconfig, CMake, Python and headers; large files/headers can use
filesystem identities. Arbitrary dynamic reads and complete tool runtime coverage are
not established. Captured dependency evidence must prove overlay participation; partial
coverage and successful Configure remain unknown overall freshness, not a green pass.

Draft changes mark results stale for the current candidate. Background polling neither
focuses nor scrolls. Leaving/reopening retains the browser's job reference, while shared
jobs and Projects attempt records retain their original workspace. Verified diagnostics
link only to the matching current overlay; other/generated paths remain evidence.
Historical attempts added during Configure do not invalidate unchanged project context.
See [ADR 0021](decisions/0021-editor-configuration-validation.md) and the
[D5 handoff](device-tree-editor-d5-handoff.md) for exact bounds and verification.

## D6 local bindings and references

Binding forms describe source declarations in the selected overlay, not an effective
tree merged from board/SoC files and all overlays. Explicit
`POST /api/devicetree/editor/drafts/bindings` accepts the existing context and draft
mutation guards. It returns an expiring `schema_id`, exact draft revision/hash, node
binding states, typed property metadata, local values/deletions, unique local reference
targets, provider-cell layouts, aliases/chosen and diagnostics. Loading reads metadata
only: required/default values are not inserted and no project tools run.

`BindingCatalog` reads the selected application's `dts/bindings`, selected Zephyr
checkout's `dts/bindings`, and captured DTS roots within the workspace. YAML is data;
checkout Python, macros and network sources are not executed. Supported include
composition and property allow/block filters, child bindings, compatible order and
bus-qualified matching are resolved conservatively. Missing, ambiguous and unsupported
states remain distinct. Unknown field constraints withhold that field; unresolved schema
composition and incomplete catalogue observations withhold unsafe matches.

The observation fingerprints file contents, directories, root availability and workspace
identity. Commands revalidate it before planning and before publishing the draft change.
New/changed candidates, changed includes, replaced roots, expired observations and stale
draft/context identities require a fresh explicit load. At most 16 observations are
retained for ten minutes. No persistent schema or draft-format migration is introduced.

The catalogue permits 64 roots, 10,000 files, 2,000 directories, 12,000 encountered entries
and 16 MiB of YAML, with 256 KiB per file. Directory depth is limited to 32; YAML permits
20,000 parsing events and depth 32, rejecting aliases, anchors, explicit tags and duplicate
mapping keys. Include/child depth is 16, with 64 includes per declaration and 128 dependency
files. Schemas permit 1,024 properties, 128 candidate paths and 100 diagnostics. Missing
or exceeded coverage is never a successful complete scan. Symlink traversal is refused.

The following commands use the existing guarded `drafts/command` endpoint, canonical
`SourceDocument` patches, Undo/Redo, diff and durable draft acknowledgement:

| Command | Additional arguments beyond `kind` and `schema_id` | Meaning |
| --- | --- | --- |
| `binding_set` | `node_id`, `name`, typed `value` | Apply a supported schema value to this local declaration |
| `binding_reset` | `node_id`, `name` | Remove a unique local property or deletion directive |
| `binding_delete` | `node_id`, `name` | Introduce an explicit `/delete-property/` directive |
| `add_label` | `node_id`, `label` | Add a nonconflicting label to a safe local declaration |
| `set_alias`, `set_chosen` | `name`, `reference` | Set a named reference to an existing unique local target |
| `remove_alias`, `remove_chosen` | `name` | Remove a uniquely identified local named reference |

Typed values are boolean, unsigned 32-bit integer, string, numeric/string/byte array,
single reference (`phandle`/`path`), reference list, or phandle-array entries of
`{reference, cells}`. Arrays use up to 64 explicit entries; bytes range from 0 to 255.
Integer/string enumerations constrain scalar values, and array enumerations constrain
each entry while preserving order and repetitions. Supported constants are applied only
on an explicit action. Unsupported byte-array enum/constant semantics remain source-only.
Boolean false uses explicit deletion; Reset does not claim that the effective value is
false or absent. Reset may reveal an unknown inherited value or a binding default.

References are limited to unique declarations identified by local labels or absolute
paths. Provider cells require the actual matched provider's named specifier layout and
an explicit matching `#<space>-cells` count in the overlay. Existing unknown references,
macros, repeated contributors and incompatible cell counts are preserved without
fabricating editable values. Neither binding presence nor a GPIO specifier establishes
a board pin map. Label rename is not exposed through the API/UI because cross-file
reference closure is not established; its lower-level source operation requires explicit
closure proof. Unrelated unknown source remains byte-preserved.

The browser loads binding metadata explicitly and supports cancelling inspection.
Typing, Undo/Redo, context/workspace changes and navigation invalidate stale forms;
late responses cannot restore them. Binding commands share source history and do not
Save, Configure or Build. The existing D4/D5 workflows remain separate explicit actions.
See the [D6 handoff](device-tree-editor-d6-handoff.md) for verification status and limits.

## D7 local peripheral topology

Explicit `POST /api/devicetree/editor/drafts/peripherals` accepts the existing context
and draft revision/hash guards. Feature service `devicetree_peripherals` returns an
expiring `schema_id`, local controllers/children, GPIO groups/providers, supported fields,
creation candidates and diagnostics. Its observation reuses D6 filesystem guards and
limits. Loading metadata neither changes the overlay nor runs tools. The frontend shows
an accessible list; selection and forms operate on the same canonical source draft.

Controllers require a unique local declaration and a matched schema whose `bus` contains
exactly one of `uart`, `i2c` or `spi`. An inherited `&controller` without a resolvable local
declaration remains outside topology support. UART offers supported status/current-speed
fields; I2C/SPI offer supported status/clock-frequency fields. Controller-specific enums
and constants still apply. No default speed, pinctrl or peripheral capability is invented.

`BindingCatalog.devices(bus)` lists only explicitly bus-qualified device bindings, using
resolved `on-bus` metadata and the existing matching rules. It returns stable compatible
order, at most 256 candidates per requested bus, `truncated` and diagnostics. Duplicates,
missing includes and unsupported schemas remain explicit states. Unqualified bindings
are not inferred to be bus devices. Required creation fields must be supplied explicitly;
unsupported required fields prevent creation. Loading never inserts binding defaults.

Simple I2C/SPI address forms require explicit local `#address-cells = <1>` and
`#size-cells = <0>`. Existing children must have a literal single-cell `reg` equal to their
hexadecimal unit-address. I3C/multiple-bus and other cell layouts remain source-only.
The I2C form supports non-reserved 7-bit addresses `0x08` through `0x77`; this bounded
profile does not claim that every address is permitted by a particular device's wiring.
SPI uses `reg` as a chip-select index. When an existing `cs-gpios` list is resolved, its
indices constrain selection. Unresolved lists block allocation; absence leaves native
chip-select capacity unknown. Changes and removal never resize or reorder this shared
array. UART children do not receive fabricated I2C/SPI addresses.

Duplicate local addresses/chip selects, including disabled siblings, are rejected.
Unknown or inconsistent sibling addresses withhold allocation. `SourceDocument` applies
an address update to the existing node header and `reg` in one patch, preserving its
base name, labels, unrelated properties and surrounding bytes. Local absolute-path
consumers, including supported binding `path` properties, constrain address changes.
Leaf removal refuses known local label/path consumers and nested declarations; it
removes only that source block and leaves controllers, providers and siblings intact.
External, inherited and macro-generated consumers remain unknown, so these checks are
not whole-project reference closure or hardware conflict validation.

GPIO group creation uses matched `gpio-leds` or `gpio-keys` schemas. Child fields use
their actual child binding; `gpios` entries come from unique local providers with named
cells and an explicit matching `#gpio-cells`. Button creation also requests an explicit
`zephyr,code`: the selected GPIO-key driver requires it for enabled children even though
the binding does not mark it required. Existing symbolic input codes and unsupported
provider layouts remain source-editable. This supplies GPIO specifiers, not pin routing,
electrical validation or a verified board map.

Commands use the existing guarded `drafts/command` endpoint. Each adds `kind` and the
current peripheral `schema_id` to these arguments:

| Command | Additional arguments | Result |
| --- | --- | --- |
| `peripheral_controller` | `node_id`, `values` | Change supported explicit controller fields |
| `peripheral_add` | `parent_id`, `name`, `label`, `compatible`, `address`, `values` | Add a supported child with explicit required values |
| `peripheral_update` | `node_id`, `address`, `values` | Update the existing device, including atomic unit-address/`reg` changes |
| `peripheral_remove` | `node_id` | Remove a unique local leaf after consumer checks |
| `gpio_group_add` | `group_kind`, `name` | Add a supported LED/button group under the unique local root |
| `gpio_add` | `parent_id`, `name`, `label`, `values` | Add a GPIO child using actual provider cells |
| `gpio_update` | `node_id`, `values` | Change supported fields on an existing GPIO child |

Source, binding and topology edits share Undo/Redo, diff and recovery. Pending text,
changed context/revision, cancellation and navigation invalidate observations; late
responses must not restore stale forms. Save and explicit Configure/Build retain their
existing boundaries. D7 does not add module recipes, inherited-controller resolution,
pin assignment or automatic pinctrl. See the [D7 handoff](device-tree-editor-d7-handoff.md)
for measured evidence and verification.

## D8 Nordic pin observation and commands

D8 adds a bounded source adapter for the exact official `nrf52840dk/nrf52840` target.
`NordicEvidence` accepts the versioned `nrf52840dk-zephyr-4.4.2-v1` profile only when
41 selected local evidence files match their recorded hashes. The set includes the board
DTS include closure, pin/GPIO encoders and relevant binding/driver evidence. Facts are
then read from those local files; source contents are not bundled as a substitute for the
selected checkout. Observed absence of potential shadow files is revalidated as well.
Missing, changed, shadowed or unsupported evidence leaves the pin forms unavailable.

The context must identify the official board, selected environment and exactly one active
application overlay matching the draft. Custom board/SoC roots, shields, snippets and
multi-overlay composition are unsupported. The source profile allows the supported
GPIO/pinctrl header includes, not arbitrary preprocessing, node deletions, absolute
reference blocks, overridden provider layouts or changed read-only QSPI/PWM routing.
These boundaries avoid claiming an effective hardware tree from partial composition.
Unsupported documents remain available to the existing source editor.

`POST /api/devicetree/editor/drafts/pins` uses the existing context/draft guard and returns
an expiring observation: `schema_id`, draft revision/hash, evidence fingerprint, support
state, exact target, controller states, local GPIO rows, symbolic flags, a 48-pin table and
diagnostics. Loading performs no firmware write or project execution. `PinService` is
feature-owned and enters the same `DraftService.command` path as D6/D7.

Supported controller roles are `UART_TX`, `UART_RX`, `UART_RTS`, `UART_CTS`, `TWIM_SCL`,
`TWIM_SDA`, `SPIM_SCK`, `SPIM_MOSI` and `SPIM_MISO`, constrained by the observed controller
profile. PWM/QSPI routes contribute read-only allocation evidence. Explicit `default`
and `sleep` states remain separate; identical pins in those two states of one controller
are not a concurrent conflict. Known enabled consumers, shared hardware instances and
board reservations constrain assignment. A board reservation may retain its evidenced
owner; selecting the pin alone does not transfer that ownership. Unknown enabled routes
prevent proving a new allocation safe. External wiring, package pads, electrical behavior
and runtime ownership remain unknown.

Commands carry the current `schema_id` and existing exact context/draft guard:

| Command | Additional arguments | Result |
| --- | --- | --- |
| `pin_assign` | `controller`, `state`, `role`, `port`, `pin`, `split_shared` | Assign/reassign one symbolic signal in the selected state |
| `pin_unassign` | `controller`, `state`, `role`, `split_shared` | Remove one signal and supported exclusively owned empty groups |
| `pin_gpio` | `node_id`, `property`, `index`, `port`, `pin`, `flags` | Update an observed direct GPIO entry with explicit symbolic flags |
| `pin_gpio_unassign` | `node_id`, `property`, `index` | Remove one GPIO entry; explicitly delete the property when removing its final entry |

`NordicSource` plans one atomic source change. Existing local `NRF_PSEL` expressions retain
their symbolic roles and surrounding bytes. An inherited state is copied into the overlay
before modification. A state with other consumers requires explicit `split_shared: true`;
the original state remains for those consumers. Unsupported expressions, ambiguous local
contributors and unsafe descendant-label/comment transformations are refused with source
editing available. Unassign retains unrelated signals and removes only supported exclusive
groups. An emptied pinctrl state receives an explicit empty reference list so inherited
pins are not silently restored. Required headers are added only by applicable commands.

GPIO forms cover observed local `gpios` and `*-gpios` properties using direct `gpio0`/
`gpio1` references, including supported local overrides of inherited consumers. They do
not enumerate every inherited GPIO device as an editable row. Flags remain explicit
symbols from the selected header; invalid combinations and opaque values are not
rewritten. Multi-entry edits preserve the other entries; removal shifts their source
indices. The final removal uses `/delete-property/` to suppress inheritance. A required
property can consequently remain absent until the user replaces it before Configure.
`cs-gpios` entries can be reassigned at their existing indices, but removal remains
source-only because it could reorder or invalidate a bus device's chip-select index.

The browser uses shared draft-observation invalidation, cancellation and reconciliation.
Controller/state/role selectors, GPIO forms and a selectable pin table support keyboard
use. Table selection only fills a form; Apply/Unassign changes the canonical draft. Source,
Undo/Redo, recovery, reviewed Save and explicit Configure keep their existing boundaries.
No board drawing, module recipe, automatic controller enablement or multi-file writer is
introduced. The [D8 handoff](device-tree-editor-d8-handoff.md) records current verification.

## D9 module observations, commands and presentation layout

D9 is vendor-independent and inherits D6/D7's local declaration boundary. It does not
extend D8's inherited pin profile. `ModuleService` observes the canonical overlay with
the selected local binding catalogue. The built-in catalogue is data only, with catalogue
version 1 and four version-1 recipes: `bme280-i2c`, `bme280-spi`, `gpio-led` and
`gpio-button`. It neither downloads components nor executes recipe hooks.

`POST /api/devicetree/editor/drafts/modules` takes the existing context/draft guard.
Its response retains D7's binding, parent, provider and field metadata, plus:

| Field | Meaning |
| --- | --- |
| `catalogue_version`, `recipes` | Versioned recipe identities, descriptions, separate software requirements and observed eligible parent forms |
| `parents` | Local bus/group declarations with canonical path keys and supported operations |
| `instances` | Child declarations, structural recipe matches, local edit/removal capability and source origin |
| `graph.nodes` | Local controllers, groups, modules, providers and read-only unmatched compatible declarations |
| `graph.edges` | Observed parent relationships and resolved GPIO references, with property/entry identity |
| `graph.truncated` | Whether the 256-node canvas limit omitted additional declarations; diagnostics explain the limit |

Only unique local declarations receive stable graph keys, formed as `node:` plus their
canonical path. Repeated contributors, inherited references and unresolved providers do
not become invented connections. Graph node IDs used by source commands still belong to
one exact draft revision. Recipe matches are informational and do not claim compatibility,
provenance or ownership. Handwritten and generated nodes use the same source projection.

The canonical `/drafts/command` route accepts these exact module command fields, in
addition to `kind` and `schema_id`:

| Kind | Fields |
| --- | --- |
| `module_add` | `recipe_id`, `recipe_version`, `parent_id`, `name`, `label`, `address`, `values` |
| `module_update` | `node_id`, `address`, `values` |
| `module_remove` | `node_id` |

Adds require the exact observed recipe version and a suitable existing local parent.
Required values come from its resolved binding. I2C addresses, SPI chip-select indices
and GPIO provider cells remain explicit; GPIO commands use `address: null`. SPI frequency
uses the selected binding's constraints. GPIO buttons require an explicit input event
code. Existing address, name and label conflicts are rejected rather than silently
creating a second identity or converting an existing node.

Each recipe creates exactly one leaf. No recipe owns a parent, provider, pinctrl state,
header or other shared dependency. Removal reuses D7's leaf/reference/path guards and
deletes only the selected local source block. Nested nodes and known consumers require
explicit resolution. This bounded recipe set consequently needs no ownership metadata
or automatic dependency cleanup. Broader removal planning belongs to D10.

Module commands validate the observation's context/revision/hash, expiry and local
catalogue dependencies, then delegate to D7's source plans. One accepted hardware action
is one canonical draft/history operation. Existing source bytes, unrelated properties
and comments retain the same guarantees as those plans. Layout coordinates and recipe
versions are not written into firmware source. Software requirements are displayed
separately; Kconfig, controller enablement, pin routing, Save and Configure remain explicit
independent actions. Missing bindings or recipe information do not establish compatibility.

`LayoutService` exposes two presentation-only routes:

| Route | Request beyond the canonical guard | Response |
| --- | --- | --- |
| `POST /api/devicetree/editor/drafts/module-layout` | `schema_id` | `schema_version`, `layout_id`, `revision`, `positions` |
| `POST /api/devicetree/editor/drafts/module-layout/save` | `schema_id`, `expected_layout_revision`, `positions` | Updated layout record without private scope data |

Both routes revalidate the current module observation. Each position is keyed by a
current graph key with exactly `{x, y}` integer values in the range 0–31. Read responses
omit positions for nodes absent from the current graph. Position changes never create
source edits, source history revisions, recovery records or project jobs. **Save layout**
is separate from overlay **Review and Save**.

Persistent records use schema version 1 under the feature-owned `module-layouts`
directory. A SHA-256 identity covers the workspace path/identity, application path, overlay
path, project/configuration identifiers and requested target. Records contain this scope,
layout identity, optimistic revision and positions. A directory `flock`, bounded regular
file reads, atomic replacement and file/directory synchronization protect writes. Symlink
directories/records are refused. Unknown versions, malformed records, conflicts and
failed replacement preserve the previous record. A synchronization failure after replacement
requires reload to determine the stored revision. Limits are 256 layout records, 256 KiB per record
and 256 positions. This is a new format; no older layout schema needs migration.
See [ADR 0022](decisions/0022-module-layout-and-source-identity.md).

The canvas offers keyboard-accessible selection, placement/connection forms and directional
layout controls. Moving/selecting cards is presentation only. Source and module changes
use the shared draft-observation lifecycle; retained presentation does not authorize stale
commands. Background refresh must not scroll, refocus or discard an unrelated form.
Current verification is recorded in the [D9 handoff](device-tree-editor-d9-handoff.md).

## Verification and next stage

The [D0 handoff](device-tree-editor-d0-handoff.md) records the prototype baseline.
The [D1 handoff](device-tree-editor-d1-handoff.md) records provider, context-service,
frontend, browser and package validation for the injected preview. A successful context
load is not a configure/build or hardware test.

The [D2 handoff](device-tree-editor-d2-handoff.md) records source-engine verification.
The [D3 handoff](device-tree-editor-d3-handoff.md) records synchronized drafts and the
[D4 handoff](device-tree-editor-d4-handoff.md) records guarded publication and recovery.
The [D5 handoff](device-tree-editor-d5-handoff.md) records explicit saved-source
configuration and Projects handoff. The [D6 handoff](device-tree-editor-d6-handoff.md)
records local binding-aware properties and references. The [D7 handoff](device-tree-editor-d7-handoff.md)
records local peripheral topology, real Configure evidence and passed full/browser/package
checks. The [D8 handoff](device-tree-editor-d8-handoff.md) records the bounded Nordic pin
workflow and current verification. D0–D8 are implemented within their scoped boundaries.
The [D9 handoff](device-tree-editor-d9-handoff.md) records the implemented local module
catalogue/canvas, separate layout format and passed verification. The next
implementation stage is D10 only: resource diagnostics and broader removal planning,
preserving the source command path and separation between source saving and project execution.
