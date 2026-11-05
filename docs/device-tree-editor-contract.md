# Device Tree Editor foundation contract

Status: D0 design baseline, D1 read-only context and D2 in-memory source operations,
2026-11-05. Final D2 verification is tracked in its handoff.
D1 supplies an explicitly injected development/test preview; the built-in editor stays
planned. Draft editing, persistence and source-write contracts below remain future work.
The D0 executable evidence is a bounded in-memory source prototype under
`scripts/prototypes/`, outside the installed application. See the
[master plan](device-tree-editor-masterplan.md) and
[ADR 0019](decisions/0019-device-tree-editor-foundation.md). Verification is recorded in
the [D1 handoff](device-tree-editor-d1-handoff.md) and
[D2 handoff](device-tree-editor-d2-handoff.md).

## Ownership and compatibility

The editor uses feature ID `devicetree`, service key `devicetree_editor`, API namespace
`/api/devicetree/editor/`, and shell view `devicetree/editor`. D1 registers them only
when `features/devicetree/preview.py::PREVIEW_FEATURE` is explicitly injected. The
production feature remains planned with no frontend, registration callback or
implemented navigation entries.

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
expressions remain editable when their structure is unambiguous. D3 must bind these
pure operations to a revalidated D1 context and handle UTF-8/UTF-16 position conversion.

## Draft and change plan proposal for D3/D4

Source bytes are authoritative; the hardware graph is derived. Byte offsets belong to
the server's UTF-8 source representation, not JavaScript UTF-16 indices. D3 must convert
editor positions explicitly and test non-ASCII comments before using text selections.

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
in D4. Unknown future versions remain untouched and explain why they cannot be opened.
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

## Verification and next stage

The [D0 handoff](device-tree-editor-d0-handoff.md) records the prototype baseline.
The [D1 handoff](device-tree-editor-d1-handoff.md) records provider, context-service,
frontend, browser and package validation for the injected preview. A successful context
load is not a configure/build or hardware test.

The [D2 handoff](device-tree-editor-d2-handoff.md) records source-engine verification.
After that exit gate, implement D3 only: connect revisioned source/visual drafts to the
engine and exact D1 context, with selection, diff and unified undo/redo. Filesystem
publication remains D4.
