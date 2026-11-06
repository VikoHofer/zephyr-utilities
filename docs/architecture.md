# Architecture

Zephyr Utilities runs as one local Python process serving one browser application. The
shared shell owns navigation and workspace selection. Features supply domain services,
HTTP routes and scoped browser modules through an explicit registry.

Memory Explorer, Project Creator's analyser/creation/build workflow, existing-build
inspection in Build Inspector and limited Device Tree overlay editing are working features.
The bounded Nordic pin workflow and local module catalogue/canvas are also implemented.
The D9 handoff records passed verification. D10 resource diagnostics and removal reviews
are complete within their documented graph limits, with full/browser/package checks passed.
D11a local single-specifier PWM consumers and PWM LED forms are complete within their
scoped limits, with full/browser/package checks passed. D11b adds local ADC consumers
and common channel configuration; its verification is recorded in the D11b handoff.
D11 remains partial.
Broader pin adapters, advanced composition and TinyML experiments remain planned.

## Dependency direction

```text
CLI
 └─ app.py (composition)
     ├─ core/ (HTTP, feature contracts, workspace, jobs)
     └─ explicit feature registry
         ├─ features/memory/
         ├─ features/devicetree/  [overlay editing, guarded Save and recovery]
         ├─ features/inspector/   [advanced read-only build/source inspection]
         ├─ features/projects/    [analysis, creation and single-image builds]
         └─ features/tinyml/      [planned]

Browser shell → registered feature modules → shared authenticated API
```

`core/` does not import features. A feature may depend on public core contracts, but not
another feature's internal modules. The composition root supplies shared services. When
two implemented features need the same behavior, extract a focused core service rather
than copying it or coupling their private implementations.

## Source layout

```text
src/zephyr_utilities/
  cli.py                    # Arguments, default workspace and browser launch
  app.py                    # Application composition and server creation
  core/
    features.py             # FeatureDefinition and FrontendDefinition
    context.py              # ApplicationContext and feature storage
    http.py                 # Router, Request, Response and shared transport
    jobs.py                 # Shared job execution and cancellation
    workspace.py            # Shared selected workspace
  features/
    __init__.py             # Explicit built-in registrations
    memory/                 # Report discovery/generation/model/snapshots/comparison
      web/                  # Feature entry module, template and stylesheet
    devicetree/              # Overlay editing, guarded Save and draft recovery
    inspector/              # Build outputs, resolved trees, sources and bindings
    projects/               # Environment analysis, creation, builds, storage and UI
    tinyml/                 # Planned model/experiment-analysis scope
  web/                      # Shared HTML shell, styles and JavaScript helpers
  memory/                   # Compatibility imports for the first release
tests/                      # Python and frontend behavior checks
scripts/                    # Repeatable checks and feature generator
docs/                       # Contracts, decisions, contributor guides and roadmap
```

## Backend feature boundary

`FeatureDefinition` describes an ID, title, description, status, entry path, frontend
assets and a registration callback. `FrontendDefinition` identifies a feature's bundled
asset directory and entry filenames. `features/__init__.py` explicitly selects the built-in
definitions; there is no implicit import of arbitrary workspace code as an application plugin.
Optional `NavigationItem` entries describe sidebar subviews and their nested-route
matches. `navigation_group` places a feature in the primary tools or the optional
Advanced section. The shell renders this metadata without importing feature internals.

An available feature's `register(router, context)` callback registers its routes and owns
its service instances. A planned definition has no registration callback or frontend.
`GET /api/features` serializes the catalog for the shell.

Routes use `Router.add(method, path, handler)`, with named parameters such as
`/api/memory/snapshots/:id`. A handler receives a `Request` with method, path, query,
parameters and parsed body; `query_value(name, default)` reads a query value. It returns
a JSON-compatible value or a `Response` for explicit status, content type or downloads.
Feature handlers do not implement another HTTP server or bypass shared request checks.

New feature APIs use `/api/<feature-id>/...`. Memory's original unnamespaced endpoints
remain compatibility aliases. The old `zephyr_utilities.memory` Python imports also
forward to the current implementation. These compatibility surfaces are deliberate;
removing them requires a documented migration and regression coverage.

## Shared context and jobs

`ApplicationContext` provides:

| Interface | Purpose |
| --- | --- |
| `workspace.get()` / `workspace.set(path)` | One validated selected workspace for future operations |
| `jobs.submit(feature, task, resource_key=None, metadata=None)` | Queue cancellable work through the shared manager |
| `jobs.get(id)`, `jobs.list(feature=None)`, `jobs.cancel(id)` | Observe or cancel shared jobs |
| `services` | Feature-owned service instances assembled during registration |
| `feature_data(feature_id)` | Stable persistent storage namespace |
| `builds.inspect/get/read_text/select_artifact` | Shared read-only observations, verified text and detached artifact selections |
| `project_context.catalog/capture/revalidate` | Optional public read-only project-selection provider, supplied after Projects registration |
| `project_validation.review/submit/result/cancel/handoff` | Optional Projects-owned saved-source Configure and navigation handoff; compilation stays separate |

A task receives `(emit, cancel)` and returns a result mapping. Progress goes through
`emit`; long-running work checks the cancellation event and releases subprocesses and
resources on exit. Raise `concurrent.futures.CancelledError` when stopping for cancellation
so the job records a cancelled outcome. Resource keys prevent conflicting jobs from
operating on the same resource concurrently. A task captures its input paths at submission so changing the
active workspace cannot redirect an in-flight operation.

The application owns job-manager shutdown. Leaving a browser view disposes that view,
not its server job. Job results and logs remain available through the shared API within
the manager's retention limits; durable feature results belong in feature storage.

## Storage and compatibility

`--data-dir` retains its original meaning as the Memory Explorer snapshot directory.
`context.feature_data("memory")` returns that directory. Other feature namespaces use
`<data-dir>/.features/<feature-id>/`. This keeps existing snapshot IDs, `objects/`, raw
reports and annotations available without moving data during a normal launch.

The default memory directory remains under `$XDG_DATA_HOME/zephyr-utilities/memory`
or `~/.local/share/zephyr-utilities/memory`. It does not depend on the working directory.
Features must not write directly into another feature's namespace. Persistent formats
need an explicit schema/version and migration strategy; UI state and a filesystem path
alone are not adequate experiment provenance.

Project environment observations use schema version 1 under
`feature_data("projects") / "environments"`, with immutable snapshot files and an atomic
per-workspace pointer. Reopening serves saved evidence without rescanning; explicit
analysis checks fingerprints and can reuse an unchanged catalogue. Future formats are
rejected without overwriting their data. See [ADR 0003](decisions/0003-environment-observations.md)
and the [environment analyser contract](environment-analyser.md).

Projects owns versioned project/profile, named configuration, build-attempt and output JSON under
`feature_data("projects") / "creator"`. Project definitions retain template/input identity;
attempt records retain reviewed commands, bounded logs, outcomes and live output hashes.
Imported and explicitly edited project/configuration records use schema version 2 with
compare-and-swap revisions; earlier version-1 records stay readable. Original edits
retain the creation profile. Attempts capture their selection before execution and
retain it after configuration edits or reconnection. Output ownership has separate
version-1 records, allowing cleanup after history removal. See
[ADR 0010](decisions/0010-named-build-configurations.md).
Explicit removal can delete a registration and its dependent records, a named
configuration or an attempt history entry without deleting source/build files.
Catalogue reads reconcile directory identity and retain unavailable registrations for
review and cleanup. See [ADR 0013](decisions/0013-explicit-record-removal.md) and the
[lifecycle audit](lifecycle.md).
Profiles with ordered Kconfig/overlay selections use profile version 2 and a version-1
fragment envelope. Legacy profiles stay version 1 with automatic discovery; no source
or record migration runs on opening. Earlier readers reject version-2 profiles rather
than ignoring their file selections. See [ADR 0011](decisions/0011-ordered-application-fragments.md).
Unknown formats are preserved. Interrupted work is shown as interrupted after restart,
not resumed. See [ADR 0004](decisions/0004-reviewed-project-creation.md) and the
[creator contract](project-creator.md).

Custom-board roots use a separate version-1 workspace registry. Environment reads derive
staleness from its fingerprint and directory availability; saved observations remain
immutable. Registered roots participate in both analysis and build rechecks. The board
scaffold and application creator share a feature-owned guarded file publisher; each
validates its own source contract before publishing. See
[ADR 0018](decisions/0018-custom-board-workflow.md).

Curated local sample copies add optional versioned source provenance to the existing
project record. A bounded source adapter preserves upstream files and license evidence;
source fingerprints are checked independently of environment observations. The reviewed
publisher supports nested files and retains the same no-replacement and cleanup guards
for built-in templates. Saved copies do not depend on their upstream sample directory.
See [ADR 0012](decisions/0012-reviewed-local-sample-cloning.md).
New Minimal C/C++ templates use version 2 and new target-selected sample previews use
provenance version 3. They include a board-named default overlay without suppressing
upstream defaults; older versions remain readable. Project Creator distinguishes expected
overlay selection from captured configuration inputs and preserves historical evidence.
See [ADR 0016](decisions/0016-default-overlays-and-usage.md).

## Browser feature boundary

The shell fetches the session and feature catalog, loads a registered template and
stylesheet, imports its module, then calls `mount(root, context)`. The returned lifecycle
object supplies `navigate(view)` and `dispose()`.

The context supplies authenticated `api`, session metadata, shared workspace/catalog,
shell `navigate`, viewport-visible `notify`, one-time result `reveal`, feature-local
in-memory `state`, and `refreshWorkspace`.
Only the shell owns `window` hash navigation. Feature code controls its local views and
scopes selectors/styles to its root. It removes listeners, timers and observers during
dispose and can restore state when mounted again.
After the shared workspace picker changes the workspace, the shell navigates the active
feature to its current view again. Features must reconcile workspace-bound state on this
notification, even when the view name is unchanged.

The canonical paths are `/#/home`, `/#/memory/overview`, `/#/projects/applications`,
`/#/projects/create`, `/#/projects/environment`, and `/#/inspector/builds` or
`/#/inspector/sources`. Earlier memory hashes retain their destinations. Old
`projects/builds`, `devicetree/inspect` and `devicetree/application` deep links map to
Inspector; bare `/#/devicetree` opens the delivered overlay editor.
All browser assets are bundled with the Python package and served by the same process.
Shared Zephyr color tokens and action-feedback rules apply to every feature. See
[UI guidelines](ui-guidelines.md) and [ADR 0014](decisions/0014-visible-action-feedback.md).

## Local access boundary

The HTTP service binds to loopback. Shared transport validates host/origin and requires
the session token for APIs other than session bootstrap. Request bodies are bounded and
parsed as JSON. Static assets come from registered package directories, not arbitrary
workspace paths. Keep these checks centralized when adding routes.

Selecting a Zephyr build selects its local tooling environment. Report generation runs
the selected footprint script against an existing ELF; it does not invoke a build or
flasher. Project creation uses a separate reviewed plan and an explicit source-write
action; configure/build likewise require a reviewed command plan. Editor writes use a
separate reviewed destination, conflict comparison and recoverable publication protocol.

Environment analysis reads workspace/module, hardware model v2 and SDK metadata through
feature-owned adapters. Local version/manifest/Git probes are bounded subprocesses;
analysis does not execute project CMake, configure firmware or install/update tools.
Sources, declared/discovered evidence, unknowns and diagnostics remain distinct.
`EnvironmentSnapshot` remains an analysis-only observation. Creator execution rechecks
selected environment and application inputs, then uses explicit CMake argument arrays
and a fresh output directory by default. Explicit reuse revalidates a managed output,
its captured cache/profile and configuration revision before reconfiguring in place.
Reviewed cleanup removes only an owned output tree and retains historical evidence.
See [ADR 0017](decisions/0017-project-management-and-build-outputs.md).
Application/Zephyr/toolchain code runs only for an explicit
configure/build action. It is not sandboxed by output-directory separation.

Project/profile and attempt context remain feature-owned. The shared build-inspection
service captures separate transient observations of existing outputs for Build Inspector
and Memory handoff. It preserves original context/hashes and rechecks live artifacts, with
source freshness explicitly unknown. This is a focused read interface, not the full
future editor/change-plan contract. See [ADR 0005](decisions/0005-read-only-build-inspection.md).
Memory report jobs detach a verified selection at submission and revalidate it before
generation and snapshot publication; cache expiry and workspace changes cannot retarget
accepted work. See [ADR 0006](decisions/0006-inspected-image-memory-handoff.md).
Build Inspector's source catalogues use the public `core/source_files.py` reader for bounded,
workspace-confined current text and directory reads. The feature owns reference selection,
binding YAML interpretation and transient file IDs. Generated-output evidence remains
separate from current source hashes. See [ADR 0007](decisions/0007-source-and-binding-navigation.md).
Standalone application catalogues reuse that reader with bounded scan filters and a
pinned application directory instead of a build selection. Projects hands off only the
application path; Build Inspector validates it without importing Projects internals or
creating a profile. See [ADR 0008](decisions/0008-application-source-browsing.md).
Inspector now owns the read-only implementation previously under Device Tree and
Projects. Legacy HTTP paths remain aliases, and persistence is unchanged. The built-in
editor does not replace those inspection routes. See [ADR 0009](decisions/0009-project-navigation-and-inspector.md).

D1 introduced a read-only `PREVIEW_FEATURE` for explicit development/test injection.
D4 promotes only the delivered overlay workflow to the built-in feature; the preview
definition remains compatible for existing development callers.
Its `/api/devicetree/editor/` routes and `devicetree_editor` service key remain separate
from Inspector's `devicetree` alias. Application composition supplies a Projects-owned
`ProjectContextProvider` through the optional `context.project_context` field after
all features register, so Inspector/editor registration order does not determine access.
The provider captures Original/named configuration and registration revisions, exact
target, fragment intent and separate historical evidence without a build preview or
tool execution. Unregistered directories remain partial source-only contexts.

The editor retains at most 16 contexts for 30 minutes, rechecks source/context identity
before follow-up reads, and selects an application overlay without changing it. It
does not attach a build/image or import the D0 prototype into the runtime.

D2's feature-owned `source_syntax.py` indexes source occurrences and byte spans;
`source.py` plans and applies immutable, reversible source changes in memory. Neither
module reads files, loads includes, evaluates macros, calls tools or registers routes.
Occurrence identities distinguish repeated contributors instead of collapsing them
into effective hardware. Document identity, monotonic revision, full source hashes and
exact ranges guard patch application and inverse operations. The D0 prototype remains
separate; production code does not import it. See the
[D2 handoff](device-tree-editor-d2-handoff.md) for supported operations and limits.

D3 composes a feature-owned `DraftService` as `devicetree_drafts`. Its authenticated
`/api/devicetree/editor/drafts/` routes retain canonical source and bounded undo/redo
history in session memory. Every access revalidates its D1 context and base document;
workspace identity, configuration/source fingerprint and overlay identity separate
drafts. Source and visual edits share monotonic revision/hash preconditions. Incomplete
typing remains source data while stale visual controls are withheld. The browser retains
pending text separately across feature navigation and reconciles uncertain requests.
The D3 session-only boundary is recorded in the
[D3 handoff](device-tree-editor-d3-handoff.md).

D4 adds feature-owned `SavingService`, `DraftStore` and `OverlayPublisher`. Acknowledged
drafts and histories use a version-1 envelope under `feature_data("devicetree")/drafts`.
Bounded, expiring Save plans bind the exact draft, original file identity and ancestor
chain. Linux publication locks the opened parent/destination, stages exact bytes,
persists recovery evidence, exchanges atomically and verifies both the displaced original
and current candidate. There is no automatic rollback or replay after interference.
Save returns a refreshed context/baseline while retaining unified undo history.
Recovery, reload and discard require comparison with current source. No configure/build
jobs are introduced. See [ADR 0020](decisions/0020-guarded-overlay-save-and-recovery.md)
and the [D4 handoff](device-tree-editor-d4-handoff.md) for limits and failure semantics.
See the [foundation contract](device-tree-editor-contract.md) and
[ADR 0019](decisions/0019-device-tree-editor-foundation.md) for the adopted staged design.

D5 composes `ProjectsValidationProvider` through the public
`core/project_validation.py` protocol. Editor `ValidationService` binds reviews to the
canonical saved candidate and delegates execution to the existing Projects workflow.
Only explicit submission runs Configure; Save and opening remain tool-free. The adapter
uses the original source layout and fresh managed outputs, preserving removals and
relative includes. Unsaved staging is unsupported. Bounded application/source-tree/tool
fingerprints, observed overlay participation and diagnostic links retain their evidence
limits; partial coverage never becomes a complete freshness claim. Jobs survive feature
navigation and retain the submitted workspace. Historical attempt additions do not
invalidate unchanged project/source selections. See
[ADR 0021](decisions/0021-editor-configuration-validation.md) and the
[D5 handoff](device-tree-editor-d5-handoff.md).

D6 adds feature-owned `BindingCatalog` and `BindingService`. Explicit binding inspection
reads bounded local YAML as data from the selected application, Zephyr checkout and DTS
roots. Supported includes, child schemas and bus-qualified matches produce metadata
for declarations in the current overlay; this does not resolve inherited effective
hardware. The observation binds its filesystem fingerprint to the exact context and
canonical draft revision/hash. Every binding command revalidates that observation and
uses the existing `DraftService.command` source-patch/history/recovery path. There is no
parallel generated-source model or persistent binding cache.

The frontend loads metadata explicitly, uses only proven local reference targets and
actual provider cell layouts, and invalidates forms after source/context changes.
Defaults and required properties are descriptive until Apply. Reset and explicit
deletion have distinct commands. Unsupported schemas, expressions and unresolved
references remain source-only; no board pin map or inherited value is inferred. Safe
label addition is available, while label renaming is withheld without cross-file proof.
The separate reviewed Save/Configure/Build boundaries remain unchanged. See the
[D6 handoff](device-tree-editor-d6-handoff.md) for limits and verification status.

D7 adds feature-owned `PeripheralService`, derived from the existing binding observation
service. `drafts/peripherals` projects an accessible local topology and bounded bus-device
candidates. `BindingCatalog.devices(bus)` uses resolved `on-bus` metadata, stable compatible
ordering and a 256-candidate limit; ambiguity, unsupported schemas and truncation remain
visible. Per-observation caches are detached data and reused only after filesystem
revalidation. Controller identity comes from a single resolved `bus`, never a name guess.

Peripheral commands enter `DraftService.command` and the same source/history/recovery
path. Atomic node updates preserve unit-address/`reg` consistency, labels and unrelated
bytes. Single-cell I2C/SPI forms require explicit local cell layout, reject local address
collisions and preserve shared chip-select arrays. Local path consumers constrain address
changes; leaf removal checks known local references and retains controllers/providers.
GPIO LED/key child forms use actual binding/provider metadata. Required creation values
are explicit; defaults, pinctrl, inherited controller state and module wiring are not
generated. The browser shares draft-observation lifecycle handling for binding and
peripheral forms, with late-response invalidation and the same explicit Save/Configure
boundaries. See the [D7 handoff](device-tree-editor-d7-handoff.md) for supported profiles,
remaining limits and verification.

D8 adds feature-owned `NordicEvidence`, `NordicSource` and `PinService`. The evidence
adapter accepts the exact official `nrf52840dk/nrf52840` target and a versioned Zephyr
4.4.2 profile of 41 hashed local files. It reads the matching board/SoC/pinctrl declarations,
GPIO reservations and connector mappings without running a preprocessor or project tools.
Present files and absent shadow paths are revalidated before commands. This bounded
baseline is separate from D6/D7's local declaration model; it is not a general inherited
tree resolver. Custom board roots, shields and multiple overlays are outside the profile.

`POST /api/devicetree/editor/drafts/pins` observes the guarded canonical draft and returns
the supported target, pin table, controller states, local GPIO rows and diagnostics.
`pin_assign`, `pin_unassign`, `pin_gpio` and `pin_gpio_unassign` use the existing draft
command endpoint and produce one atomic source/history change. The source adapter keeps
symbolic `NRF_PSEL`/GPIO expressions, preserves inherited/shared states by copying them,
requires an explicit shared-state split, and cleans up only supported exclusive groups.
Default and sleep states are distinct allocations for the same controller. Known board
reservations, GPIO use and shared hardware instances constrain assignments; unavailable
routes and electrical/package facts remain unknown. No metadata file becomes a build input.

The pin panel reuses the draft-observation lifecycle, with explicit loading/cancellation,
reconciliation, keyboard forms and a selectable table. Source, Undo/Redo, reviewed Save
and explicit Configure retain their existing boundaries. The [D8 handoff](device-tree-editor-d8-handoff.md)
records the exact supported profile and current verification. D8 is committed as `cfaf10e`.

D9 adds `ModuleService`, a data-only built-in recipe catalogue and the module canvas.
Recipes describe BME280 I2C/SPI leaves and GPIO LED/button leaves. The module service
extends the existing peripheral observation and delegates its canonical commands to D7
source plans. Required values come from the selected local binding catalogue; recipes
do not infer inherited buses, routing, provider cells or software configuration. Each
addition creates one leaf under an existing explicitly selected bus/group. Removal uses
the existing reference, path and nested-declaration guards and leaves parents/providers
intact. Consequently, no ownership metadata or exclusive dependency cleanup is required.
Structural recipe matches are informational for handwritten and generated nodes alike.

The graph is rebuilt from canonical source on every accepted observation. Unique local
paths produce stable `node:/...` presentation keys; source occurrence IDs remain bound
to a draft revision. Unmatched local compatible declarations can appear read-only.
The canvas contains at most 256 nodes, with an explicit truncation diagnostic and no
dangling edges. It does not claim to represent a complete inherited tree or physical wiring.

`LayoutService` stores schema-version-1 records in the feature-owned `module-layouts`
directory, independently of firmware, drafts, Undo/Redo and recovery. A scope identifies
workspace identity, application, overlay path, project/configuration and target.
Positions are integer grid coordinates from 0 to 31 and use current graph keys only.
Layout writes require the current module observation and an optimistic layout revision;
directory locking and atomic replacement protect concurrent application processes.
Unknown versions and invalid records are preserved. The store permits 256 records,
256 KiB per record and 256 positions per record; no older layout format needs migration.
See [ADR 0022](decisions/0022-module-layout-and-source-identity.md) and the
[D9 handoff](device-tree-editor-d9-handoff.md) for boundaries and verification status.

## Measurement boundary

Memory Explorer preserves upstream totals and uncertainty. Static reservations, runtime
occupancy, observed peaks and physical capacity are different measurements. RAM and ROM
may both contain initialized-data costs. Duplicate aliases and source attribution can
change without a corresponding footprint change.

TinyML features will connect immutable build/model identities to runs, datasets and
observation conditions. Memory data alone does not prove timing, accuracy, schedulability
or worst-case resource demand. The [research roadmap](roadmap.md) and
[memory master plan](memory-explorer-masterplan.md) describe those later measurements.

## Decisions and extension points

- [ADR 0001: One process and explicit features](decisions/0001-one-application-explicit-features.md)
- [ADR 0002: Preserve evidence and storage compatibility](decisions/0002-evidence-and-storage-compatibility.md)
- [ADR 0003: Versioned environment observations](decisions/0003-environment-observations.md)
- [ADR 0004: Reviewed creation and build attempts](decisions/0004-reviewed-project-creation.md)
- [ADR 0005: Shared read-only build observations](decisions/0005-read-only-build-inspection.md)
- [ADR 0006: Inspected image handoff to Memory Explorer](decisions/0006-inspected-image-memory-handoff.md)
- [ADR 0007: Read-only source and binding navigation](decisions/0007-source-and-binding-navigation.md)
- [ADR 0008: Application source browsing before configuration](decisions/0008-application-source-browsing.md)
- [ADR 0009: Project navigation and advanced Build Inspector](decisions/0009-project-navigation-and-inspector.md)
- [ADR 0012: Reviewed local sample cloning](decisions/0012-reviewed-local-sample-cloning.md)
- [ADR 0022: Module layout and source identity](decisions/0022-module-layout-and-source-identity.md)
- [Adding a feature](adding-a-feature.md)
- [HTTP/browser integration contract](platform-contract.md)

The architecture is a maintained contract, not a promise that every future requirement
is already abstracted. Add shared interfaces for concrete needs and record material
changes in a short decision note.

D10 adds `ResourceService` and `RemovalService` inside the same feature. Resource
observations retain exact context/draft guards, the local binding catalogue and optional
verified pin evidence. `resource_graph.py` indexes literal references, binding path values,
alias/chosen strings, property-label targets and source containment without preprocessing.
Ambiguous identities and unresolved external consumers remain distinct. Pin allocations
are additive structured evidence from the existing `PinService`, not inferred from its
human-readable messages. Diagnostic observations never become global editing or Save locks.

Removal reviews bind a source candidate and individually selected optional-property repairs
to the same observation. A confirmed `remove_reviewed` command enters `DraftService.command`
and creates one source/history change. The application revalidates context, source,
bindings and available pin evidence before applying it. Existing scoped peripheral/module
removal commands remain guarded and compatible; the normal UI opens the broader review.
No shared dependency is silently deleted or reassigned. Unknown or required consumers do
not receive invented repairs. D10 adds no persistent format, cross-feature dependency or
process boundary. See the [D10 handoff](device-tree-editor-d10-handoff.md).

D11a adds feature-owned `PWMService` using the existing binding observation, canonical
command and source/history paths. Its provider profile requires a unique local matched
binding and explicit matching `#pwm-cells`, with ordered `channel, period` or
`channel, period, flags` cells. It edits one literal consumer specifier and creates
binding-compatible PWM LED groups/children. Count and compatible values must satisfy
their supported local binding constraints. `pwm_providers()` shares this evidence with
resource diagnostics. The browser reuses retained observations,
focus restoration and D10 removal reviews. PWM diagnostics join `ResourceService` without
becoming global Save blockers. Channel ranges, timer coupling, electrical suitability and
inherited hardware remain unknown unless separately established. Named PWM consumers need
explicit repair before provider removal; removing `pwms` alone must not leave companion
`pwm-names` behind. No persistent schema or
shared process boundary changes. See the [D11 handoff](device-tree-editor-d11-handoff.md).


D11b adds feature-owned `ADCService` using the same observation and canonical command
path. Common ADC child-binding evidence distinguishes ADCs from generic I/O channel
providers. Single literal consumer assignments and common channel configuration use
explicit binding-valid values and ADC API representation bounds. Channel identities
stay fixed during configuration edits. `adc_providers()` shares that evidence with
resource diagnostics, while removal reviews protect numeric provider/channel dependencies
that lack a direct child phandle. Optional settings, shared providers, source/history,
reviewed Save and explicit Configure retain their existing boundaries. No storage schema,
process boundary or cross-feature dependency changes. See the
[D11b handoff](device-tree-editor-d11b-handoff.md).
