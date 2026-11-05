# Device Tree Editor D2 handoff

Status: D2 complete within its in-memory source-engine scope, 2026-11-05.
D1 was committed as `c8737f0` before D2 work. The built-in visual editor remains planned;
the D1 development preview remains read only.

## Delivered behavior

`features/devicetree/source_syntax.py` indexes original overlay bytes and source
occurrences. `features/devicetree/source.py` provides typed operations and reversible
patches over immutable in-memory documents. Production modules do not import the D0
prototype. No new route, browser control, persistent record or filesystem writer is
introduced, and no parser/tool from an imported project is executed.

The parser retains complete token coverage, comments, whitespace, mixed line endings,
Unicode bytes and unsupported expressions. It indexes root/reference blocks, nested
named nodes, labels, properties and known deletion directives. Repeated contributors
remain separate occurrences. Its `complete`, `opaque` and `incomplete` states describe
structural coverage, not validated or effective hardware. Incomplete input retains its
bytes and diagnostics but publishes no node/deletion index. Structurally ambiguous,
conditional or unmodeled line-spliced input is preserved but blocks transformations.

The command API supports:

| Operation | Source effect |
| --- | --- |
| `plan_set_property` | Insert or update a boolean, string or one to 64 unsigned 32-bit literal cells |
| `plan_set_status` | Set the selected occurrence's status to `okay` or `disabled` |
| `plan_remove_property` | Remove one selected local property/deletion declaration |
| `plan_add_node` | Add a root/reference block or named child with up to 128 unique literal properties; labels are supported except on the root |
| `plan_remove_node` | Remove only the selected block; visible references to its labels/known paths must first be resolved |
| `plan_delete_property` | Insert `/delete-property/ name;` for an absent local property |
| `plan_delete_node` | Insert `/delete-node/ child;` under an explicit parent |
| `plan_delete_reference` | Insert an explicit top-level `/delete-node/ &label;` or path-reference deletion |

All planning methods return `SourceChange`. `change.apply(document)` produces another
immutable `SourceDocument`; `change.inverse()` produces the guarded exact inverse.
The [foundation contract](device-tree-editor-contract.md#d2-in-memory-source-operations)
records signatures and limits.

For example:

```python
from zephyr_utilities.features.devicetree.source import SourceDocument

document = SourceDocument.parse(b'&uart0 { status = "disabled"; };')
node = document.find_node("&uart0")
change = document.plan_set_status(node.id, "okay")
updated = change.apply(document)
restored = change.inverse().apply(updated)
assert restored.source == document.source
```

## Targeting and preservation

Occurrence IDs contain the source hash, kind and byte span. Unique label/reference/path
lookup is convenient, but ambiguous lookup is refused. A caller must explicitly choose
an occurrence when multiple blocks/properties contribute; no last-writer or effective
value is guessed. IDs expire when bytes change.

Patches contain document identity, expected/result revision and hashes, exact original
ranges, replacements and operation diagnostics. Changed bytes advance the document
revision; no-op operations retain it. Undo restores original bytes while advancing the
revision again, so an old forward command cannot silently replay on a later revision.
Wrong document identity, stale revision/hash or changed source ranges reject the patch.

Same-length cell updates change only numeric tokens, retaining comments, brackets,
spacing and hexadecimal style. Shape changes that would discard internal comments are
refused. Insertions use nearby indentation and newline evidence; existing bytes are
never reformatted. Properties and property deletions precede child declarations/node
deletions, matching the stricter DTS ordering accepted by `dtc`. Local removal removes
the selected declaration span, including its contained content, while surrounding
comments and other contributors remain unchanged.

Local removal, disabling and deletion are intentionally distinct. Removing an overlay
assignment can reveal inherited/earlier values. Disabling retains the node. Deletion
emits a directive whose effective result still requires selected-environment validation.
Identifiable local deletion conflicts are refused. Reference checks cannot see through
includes or macros and do not establish a complete dependency graph.

## Conservative support and resource limits

The initial production limits are 256 KiB per source document, 32,768 tokens and 64
nested delimiters. Typed cells have one to 64 unsigned 32-bit values; strings admit
at most 64 KiB of UTF-8; identifiers/names are bounded. Resource overflow, invalid UTF-8
and NUL input fail without truncation. Byte offsets are UTF-8 offsets, not browser
UTF-16 positions.

BOMs remain preserved but non-editable. Conditionals, line splicing outside recognized
preprocessor directives, unknown structural constructs and incomplete syntax block
all transformations. Opaque property expressions can remain beside editable literals.
Phandles, macro/arithmetic values, byte arrays, string lists and other unsupported values
are not rewritten into guessed literals. Includes and definitions are never evaluated
or loaded. D2 is not an arbitrary DTS validator or an effective-tree model.

## Verification

| Area | Result |
| --- | --- |
| Syntax behavior | 21 tests passed |
| Source operations and inverse regressions | 29 tests passed |
| `python scripts/check.py --quick` | Passed: 649 Python tests; all 24 frontend test files passed |
| `python scripts/check.py` | Passed: 667 Python tests and 271 frontend tests, including HTTP integration with permitted local loopback access |
| Installed `dtc` 1.8.1 | 11 sequential synthetic generated-source cases accepted; every inverse restored exact original bytes |
| Representative 64 KiB and near-limit workloads | Measured below; one edit exceeded the 100 ms target |
| Browser/package asset checks | No browser assets or asset paths changed in D2 |

Syntax coverage includes every D0 fixture, complete token round trips, UTF-8 offsets,
repeated/labeled/nested occurrences, comma/hash property names, comments and quoted
braces, macro operators, includes, directive continuations, conditionals, deletion
forms, malformed input, root-label rejection, ordering and resource limits. Operation
coverage includes preserving labeled declarations, rejecting label reuse inside opaque
values, explicit occurrence selection, reversible insertion/removal, stale/tampered
patches, no-ops, local deletion conflicts and absence of filesystem/process side effects.
Installed `dtc` confirmed the properties-before-subnodes rule, including
`/delete-property/` before child/node-deletion statements. This development grammar
check is not an editor runtime subprocess or selected-board validation.

No firmware configure/build, hardware validation or filesystem-save claim is made.

### Development performance observations

Measured with CPython 3.14.10 on Linux x86-64, Intel Core i7-1265U, seven repetitions
per case using `time.perf_counter()`. Corpus construction is outside the timed region.
The 64 KiB synthetic source contains a root and 347 labeled children (348 nodes,
17,358 tokens), each with a comment, compatible string, numeric `reg`, literal status
and opaque macro expression. Remaining bytes are a comment. An edit plans and applies
one status change; both planning and application currently parse the candidate.

| Workload | Median | Maximum observed |
| --- | --- | --- |
| Parse 64 KiB | 33.37 ms | 38.45 ms |
| Plan and apply 64 KiB status edit | 69.31 ms | 120.14 ms |
| Parse 256 KiB with the same nodes and comment padding | 39.11 ms | 42.72 ms |
| Reject a dense 256 KiB corpus at the token limit | 43.55 ms | 45.98 ms |
| Reject 256 KiB + 1 byte | 0.01 ms | 0.03 ms |

The 256 KiB/32,768-token/64-level limits remain conservative admission limits and
never truncate input. Near-limit processing/rejection met the 1 s development target.
The final edit run had one observed value above the 100 ms target; an earlier run
measured 64.57 ms median and 69.21 ms maximum. These observations are not worst-case
bounds or a cross-machine latency guarantee. D3 should measure actual draft interaction
latency before adding continuous source updates; D15 owns broader performance coverage.

## Next stage

The next implementation stage is **D3 only** from the
[master plan](device-tree-editor-masterplan.md): bind these operations to D1's exact
revalidated context, add a canonical revisioned draft, visual status/property controls,
source editing, selection links, diff and unified undo/redo. Keep incomplete text in
the draft while withholding a stale visual representation, guard late responses and
retain separate context-bound drafts across navigation. Convert browser positions
explicitly between UTF-16 and UTF-8. Source publication and recovery remain D4; the
configure/build loop remains D5.
