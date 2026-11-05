# ADR 0019: Source-preserving Device Tree Editor foundation

Status: accepted design for D0, 2026-11-05. Runtime contracts and storage are introduced
in their consuming stages; this decision does not declare an editor implemented.

D1 implementation note: the public read-only selection provider and transient context
routes now have an editor-preview consumer. `PREVIEW_FEATURE` is explicitly injected
for development/tests; the built-in definition remains planned. No draft persistence,
source writer or build-execution handoff is introduced. See the
[D1 handoff](../device-tree-editor-d1-handoff.md) for verification status.

## Context

The next product workflow is a visual editor where hardware actions immediately change
the overlay draft. Project Creator already owns applications, configuration selections
and explicit execution. Build Inspector owns read-only source/build inspection and
legacy `devicetree` routes. Its semantic/generated-tree readers cannot preserve the
original overlay's comments, preprocessing constructs or override history.

## Decision

Keep a canonical byte-preserving source draft, with a token/span or concrete-syntax layer
for edits and a separately derived hardware graph. Unknown constructs remain source,
not discarded fields. Visual actions and text edits reconcile through the same draft
revision. A resolved tree is evidence, never the serialization source for an overlay.

D0 uses a bounded dependency-free prototype under `scripts/prototypes/` and synthetic
fixtures to test no-op byte preservation, local literal edits, inverse patches and
refusal of ambiguous input. It is not shipped as an editor API. Introduce a production
parser in D2 only after its broader supported grammar passes fidelity tests; no parser
dependency is chosen solely because it can read a resolved DTS.

Reserve `/api/devicetree/editor/` and `context.services["devicetree_editor"]` for editor
runtime work. Retain Inspector's existing HTTP/hash aliases and its `devicetree` service
alias. D1–D3 use the application's explicit feature-injection mechanism for development
and tests. The default feature stays planned until D4 has a usable save/reopen workflow.

Projects supplies a narrow read-only `ProjectContextProvider` through a public core
interface when D1 adds the consumer. Capture original/named configuration revisions,
registration/directory identity, exact target, selected environment and ordered fragment
intent. Keep predicted inputs separate from historical configured evidence. The editor
does not import Projects internals, mutate its records or create another build launcher.

Version 1 is the initial planned envelope for editor contexts, drafts and change plans,
specified in the [foundation contract](../device-tree-editor-contract.md). No D0
persistence or migrations run. Later storage belongs to `feature_data("devicetree")`;
source remains sufficient for ordinary builds without editor metadata. Reject unknown
versions without replacing their data.

Hardware UI changes update a draft immediately. Source publication is an explicit
reviewed Save, separate from tool-backed validation and builds. Initial saving is one
overlay. D4 must implement identity/hash rechecks, participating-writer coordination,
recoverable publication and guarded recovery as defined in the foundation contract.
Preserve external changes and expose conflicts; do not claim that locks or a pre-write
hash eliminate races with nonparticipating filesystem writers. Multi-file publication
requires a separate D12 journal/recovery protocol.

Opening a project does not execute project/tool code. Full semantic validation remains
an explicit later operation using the selected environment and pinned inputs. Source
freshness, binding interpretation, pin capability and hardware operation remain distinct
evidence. Initial target references are `nrf52840dk/nrf52840` and, for later adapter
comparison, `nucleo_f401re`; metadata discovery does not certify either target's hardware.

## Consequences

- D1 can add a context reader without turning historical build evidence into current
  source truth or breaking Inspector compatibility.
- D2 has a measurable preservation corpus and a deliberately small prototype to replace
  or extend; it does not inherit a premature general-purpose parser promise.
- A feature manifest or successful source test does not imply available visual editing,
  safe disk saving, configured firmware or verified physical wiring.
- Shared contracts and persistent formats can be added only with their actual consumers
  and migration/compatibility tests, keeping D0 limited to foundation work.
