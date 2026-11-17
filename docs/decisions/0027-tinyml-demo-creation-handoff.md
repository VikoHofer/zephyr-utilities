# ADR 0027: TinyML demo descriptors and the Projects creation handoff

Status: accepted for D0, 2026-11-10; MNIST chooser continuation implemented 2026-11-15.
The original sine/XOR descriptor contracts below remain foundations for later recipes.
The MNIST continuation uses the same Projects ownership and transient-token boundary.

## Delivered MNIST continuation

`projects/demo_handoffs.py` owns the three catalogue/creation-handoff endpoints below.
The only runtime identity is `mnist-shell` / `reference`, mapped to the reviewed local
sample `tinyml/mnist-shell`. Its catalogue returns source/dependency availability,
requirements and a saved environment snapshot. It makes no Configure/Compile claims.
This narrow admission retains the existing sample provenance rather than constructing
a sine/XOR descriptor or adding a `demo_origin` record. D0 pure descriptor validators
and their reserved identities remain unchanged.

For this delivered chooser, preselection requires the complete board/target/SDK tuple.
Tokens bind source/model/private-module hashes, environment fingerprint, selected
execution metadata and static board/module/root configuration evidence. Create and
resolve both revalidate; retention is 32 selections for 30 minutes without extension
on resolution. Browser requests accept IDs/fingerprints, never source paths or code.

The chooser is frontend-only; the later drawing/UART service is covered separately
by [ADR 0028](0028-tinyml-drawing-uart.md). Both Projects navigation layers preserve `create/<id>`.
Resolution loads current catalogue evidence, verifies the source and applies through
ordinary form invalidation. Dirty forms and edits during loading require explicit
Keep/Use choices; accepting preserves name/destination. Route/workspace/disposal guards
discard late replies. Choice buttons retain keyboard focus through asynchronous
revalidation and completion. Catalogue and token operations create no files or jobs;
ordinary Preview/Create, Configure and Build stay separate.

The remaining sections record the broader descriptor direction and its D0 rationale.

## Context

TinyML now prepares demo projects through the classic Project Creator. Projects already
owns exact target/SDK validation, reviewed source publication, application/configuration
records and Configure/Build attempts. A second creator or a second project store would
duplicate those responsibilities. Existing public project-context and validation
providers do not authorize creating applications.

The local TFLM sine example is a mixed C/C++ application with model source, special
compiler configuration and an optional firmware module. Its presence does not establish
that the module is installed or that a selected target can compile it. The selected
emlearn XOR source is a static synthetic test fixture, not a runtime training recipe.

## Decision

### Ownership and execution boundary

Projects owns the curated demo catalogue, trusted source locator mapping, adapters,
preselection tokens and all persistent application/build evidence. TinyML will own a
small chooser that calls documented Projects endpoints through the shared authenticated
client and navigates through the shell. It may not import private Projects modules or
mutate another feature's browser state. No new core provider or forwarding TinyML backend
is introduced without an actual backend consumer.

Opening a catalogue, resolving a token and inspecting a descriptor are read-only with
respect to application, module and firmware sources. They do not run imported CMake,
training code, installers, `west update`, Configure, Compile or Flash. The D0 qualification
utility reads and hashes source/metadata only. Its report is not a creation plan.

### Version-1 descriptor

`projects/demo_contracts.py` defines pure validators and canonical fingerprints. Initial
curated identities are:

| Demo / variant | Framework / adapter | Trusted locator / model representation |
| --- | --- | --- |
| `tflm-sine` / `reference` | `tflm` / `tflm-sine-v1`, version 1 | `zephyr-tflm-hello-world`; `tflite-c-source` |
| `emlearn-xor` / `loadable-trees` | `emlearn` / `emlearn-xor-v1`, version 1 | `synthetic-emlearn-xor`; `emlearn-trees-c-source` |

These identities reserve understood contract examples, not available implementations.
The emlearn runtime seed has not yet been adapted/admitted; its source capture and model
artifact hashes remain null until that work is complete. An installed Python package
does not substitute for an installed Zephyr firmware module.

A descriptor contains schema version, demo/variant/framework, title/task, adapter
identity/version, source kind/locator/revision/capture fingerprint, runtime file manifests
and omissions, model origin/format/artifact hash, module/Kconfig/hardware requirements,
license evidence and expected output. Sources use a curated locator rather than a caller
supplied path. The two initial contracts admit fixed-input CPU inference and their own
framework module only. Optimized or sensor-dependent variants need separate admission.

Runtime manifests contain relative paths, byte lengths and SHA-256 hashes. Existing
128-file, 1 MiB/file and 4 MiB total publication bounds apply. License paths/hashes and
non-null model hashes must match reviewed runtime artifacts. Model hashes identify the
textual C/C++ artifact, not an inferred FlatBuffer or estimator hash. Omissions list paths
and reasons. Source qualification separately retains hashes for omitted source evidence.

The descriptor fingerprint normalizes unordered manifests and binds all semantic recipe,
model, requirements, license and omission fields. It excludes availability and target
results, which have separate identities. JSON validation/fingerprinting alone does not
verify local bytes or make a source trusted; D1/D2 must perform authoritative capture and
revalidation against the curated source root.

### Compatibility identity and observations

A version-1 compatibility identity binds:

- Captured workspace and environment fingerprint.
- Descriptor/source/model identities and demo/variant/adapter version.
- Board identity, requested/effective target and effective revision when known.
- Selected SDK, SDK fingerprint and toolchain fingerprint.
- Every relevant module's identity, observed root, revision, fingerprint and
  present/missing/unknown state.
- Board/SoC/DTS/binding/snippet roots and their observed fingerprints.
- Kconfig and overlay input fingerprints.

Unknown evidence remains null. An unconfigured target does not acquire an invented
effective revision or compiler. The compatibility key is a canonical SHA-256 over this
identity. Changing source/model, variant, target/revision, SDK, modules, roots or
configuration invalidates the key. Root and module list order is preserved because it
can affect resolution precedence; descriptor file-manifest/dictionary ordering is
canonicalized independently.

Observation envelopes separate availability (`source-ready`, `missing-module`,
`unsupported-layout`, `unknown`) from Configure and Build states (`not-checked`, `passed`,
`failed`, `unknown`). Source qualification can succeed while a module is absent; in that
case application readiness remains missing-module. Unavailable observations require a
visible reason. No positive check is inferred from upstream test declarations.

Configure evidence may reference an existing Projects temporary `sample-check` ID or a
saved `project-attempt` ID. Build evidence references a project attempt. Passed checks
need complete captured identities and an evidence reference; Build success also retains
successful Configure evidence. These validators check structure and binding only. The
future service must load/verify actual Projects results before publishing a passed state;
a JSON assertion or a fabricated ID cannot establish execution.

### Public preselection handoff

Future endpoints remain under Projects:

| Endpoint | Frozen responsibility |
| --- | --- |
| `POST /api/projects/demo-catalog` | Read bounded local demo descriptors/requirements for a captured workspace and optional target/SDK selection |
| `POST /api/projects/creation-handoffs` | Revalidate curated demo/variant, source/environment fingerprints and optional profile preselection; retain a bounded server-side selection token |
| `GET /api/projects/creation-handoffs/:id?workspace=...` | Resolve and revalidate that token without publication or job submission |

The captured handoff selection has `schema_version: 1`, `workspace`, `demo_id`,
`variant_id`, `source_fingerprint`, `environment_fingerprint`, and optional `selection`.
When present, `selection` is a complete `{board_id, target, sdk_id}` tuple. The future
HTTP handler supplies the schema version; client selection fields cannot contain paths,
commands, arbitrary recipe objects or source code. Projects' existing `select_profile`
remains authoritative for board/target coherence and SDK validity. A demo with an
unqualified source/model cannot be preselected. Missing dependency observations remain
visible and cannot become a successful compatibility result through a token.

Return an opaque 32-lowercase-hex token ID and route `projects/create/<id>`. Initially
retain at most 32 tokens for 30 minutes. Tokens are server-memory selections, unavailable
after restart. Repeat resolution of a valid token is idempotent. Validate current
workspace, environment, source/adapter/model and any profile/module evidence on creation
and resolution. Reject stale evidence rather than retargeting it. A token selects form
inputs; it never authorizes file publication, changes the workspace or starts execution.

Extend both Projects navigation layers to preserve `create/<id>` while retaining normal
`create` and existing application/configuration handoffs. Apply through the creator's
ordinary field-update/invalidation path. If its form is dirty, show the proposed selection
and retain **Keep current draft** / **Use demo selection** choices. Resolve only while the
same view, workspace, navigation generation and form revision remain current. An expired,
foreign or late token keeps the current form and offers a return to Demos.

Projects performs a fresh ordinary Preview/Create after preselection. Target/SDK edits
invalidate compatibility/review normally. Configure/Build stay separate explicit actions.
Do not copy `creator-state.js`, its review/publisher or build history into TinyML.

### Additive Projects provenance

The future optional `demo_origin` shape is `{schema_version: 1, descriptor, identity,
transformations}`. It nests the full validated descriptor and compatibility identity,
plus path/source-hash/published-hash/reason entries for transformed files. Transformed
source paths must refer to reviewed source files and bind different published bytes.
Before Configure, configuration hashes may remain null. Source/model/module identities
and a selected target/SDK must exist before publication.

This decision does not change current ProjectStore readers/writers or outer record
schemas. Older projects with no origin remain valid. D1/D2 will add provenance when
publishing a demo application, test legacy reads and retain original bytes for transformed
build files. A saved demo application subsequently builds from its own files and captured
profile; reopening it does not rescan or synchronize its origin.

## Consequences

The first D1 implementation can reuse existing Projects UI, review, publication,
configuration and build history through a narrow public data contract. Current classic
templates and sample adapters remain unchanged. TFLM needs an explicit runtime-only
C++/module adapter; a new descriptor row cannot bypass the old copier's restrictions.

No model/trainer/experiment database, installer, second scheduler or TinyML routes are
restored. The feature remains planned until the first demo has passed its source,
dependency and exact-target Configure/Compile gates. D0 synthetic fixtures exercise
contract meaning and invalidation; they do not claim a real build or hardware result.

See the [D0 handoff](../tinyml-d0-handoff.md),
[source inventory](../tinyml-d0-source-inventory.json),
[emlearn seed qualification](../tinyml-d0-emlearn-seed.md) and
[implementation plan](../tinyml-demo-creator-plan.md).
