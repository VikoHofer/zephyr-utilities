# Product roadmap

The application grows through feature modules inside one `zephyr-utils` launch. This
roadmap describes intended work, not automatic task authorization or completed features.
The user has identified the AI area as **TinyML model and experiment analysis** from the
research master plan, not a general coding-assistant integration.

## Current platform

- One local server, session, selected workspace and browser shell.
- Explicit feature registration with module-owned routes and frontend assets.
- Shared job execution, cancellation and progress observation.
- Stable memory snapshots and legacy Python/HTTP compatibility paths.
- Contributor guidance, agent instructions, repeatable checks and feature scaffolding.
- Project environment analysis with local board/chip and installed-SDK discovery,
  bounded offline probes, saved observations, refresh and diagnostics.
- Reviewed minimal C/C++ creation and curated local sample cloning, explicit configure/build jobs and
  versioned profiles/attempt evidence in fresh output directories.
- Read-only existing-build inspection, sysbuild image selection and a shared observation
  handoff to the resolved Device Tree reader and Memory Explorer's explicit report review.
- Current source-file and binding navigation from an inspected image, with literal
  include links and a distinction between recorded binding paths and current candidates.
- Application source browsing before configuration, with bounded discovery and include
  navigation independent of generated build outputs.
- Sidebar subnavigation separates application management, creation and environment
  discovery. Optional Advanced navigation contains Build Inspector.

## Feature ownership and next useful slices

| Feature | State | Initial implementation direction |
| --- | --- | --- |
| Memory Explorer (`memory`) | Available | Existing-build reports, visualization, snapshots and comparisons |
| Device Tree Editor (`devicetree`) | Planned visual editor; next product priority | Follow the staged editor master plan for live overlay editing, hardware modules and pin assignment |
| Project Creator (`projects`) | Applications, creation/build and environment discovery available | Maintain the delivered everyday workflow and add focused public editor handoffs |
| Build Inspector (`inspector`) | Advanced build, resolved-tree, current source and binding navigation available | Preserve available inspection; further depth deferred |
| TinyML Experiments (`tinyml`) | Planned | Connect model/build/run identities and compare imported experiment measurements |

Planned catalog entries are development boundaries. They do not yet edit Device Trees,
run inference or perform training.

## Memory Explorer

The next memory-focused slices are reliable region/partition budgets, reviewable
optimization experiments, richer export formats, and runtime stack/heap/arena import.
Keep static footprint separate from runtime use. An optimization finding should show
its evidence, proposed experiment, trade-offs and observed result.

The detailed [memory master plan](memory-explorer-masterplan.md) remains the research and
measurement reference. The architecture guide describes the current implementation layout.

## Build Inspector and the future Device Tree Editor

Advanced → Build Inspector opens an inspected image's resolved DTS, supports node/property
navigation and shares Projects' target/domain context. It checks output identity;
source freshness remains unknown. See the [build inspection guide](build-inspection.md).
Current-source browsing and compatible-based binding navigation are also available from
the selected image. Generated header records are distinguished from current candidates;
include links and direct property summaries do not perform schema resolution or validation.
Applications can also be opened before configuration to browse current sources and local
bindings. Conventional include locations are candidates, not configured search paths.
The user-facing Device Tree Editor remains planned. Its goal is visual overlay creation,
hardware-module placement and pin/signal assignment. Following the delivered everyday
Project Creator workflow, the [Device Tree Editor master plan](device-tree-editor-masterplan.md)
is the next product direction. It defines D0–D15 with separate implementation prompts
and exit gates: source-preserving edits and guarded saving first, then peripherals,
pins, modules and advanced composition. Hardware UI actions immediately update the
overlay draft; an explicit Save publishes source changes. Inspection capabilities can
support it, but a source reader is not the editor itself. Further advanced inspection
depth remains deferred.

D0 foundation is complete: the [editor contract](device-tree-editor-contract.md) fixes
ownership, context/version proposals and source-preservation limits, with a tested
in-memory literal-edit prototype. D1 adds read-only project/configuration context loading,
overlay evidence and source inspection through an explicitly injected preview in the
existing application. The built-in editor remains planned. See the
[D0 handoff](device-tree-editor-d0-handoff.md) and [D1 handoff](device-tree-editor-d1-handoff.md)
for evidence and remaining limits. D2 now implements production in-memory source
operations, with verification and measured limits recorded in the [D2 handoff](device-tree-editor-d2-handoff.md).
D3 next connects those operations to visual/source drafts; saving remains D4.

The [Project Creator and environment analyser master plan](project-creator-masterplan.md)
provides the earlier shared-context proposal; the editor master plan now defines its
staged implementation needs. A resolved tree belongs to an application configuration
and image/domain, not just a board selection.

Treat generated build outputs as inspection artifacts. Do not present generated files as
the canonical place for durable source edits. Preserve user formatting/comments where
possible, document transformations, and support unsaved-change handling before expanding
editing capabilities.

## Project Creator

P0/P1 environment analysis is available inside Project Creator. It inventories the
selected workspace's Zephyr/tools, declared board/chip targets, module roots and installed
SDKs. Search, provenance, partial results, cancellation and explicit refresh are included.
Saved observations use schema version 1; they do not claim configured build readiness.
See the [environment analyser guide](environment-analyser.md).

P2 adds the first creator workflow:

1. Choose a new workspace application destination and exact discovered target.
2. Select an installed GNU SDK and the minimal-C template.
3. Review every generated file and the command recipe before creation.
4. Create without replacing an existing destination.
5. Review and explicitly run Configure or Build in a new output directory, retaining
   requested/effective context, bounded logs and live artifact hashes.

The source and README commands work without Utilities metadata. This release is Linux
only, requires a resolved existing west workspace/module list, and keeps SDK installation,
visual configuration editing deferred. Reviewed import/reconnection, configuration
updates, incremental reuse and owned build-output cleanup are available. The P3 inspection slice adds the
read-only Device Tree handoff and verified-image Memory report handoff. See the
[Project Creator guide](project-creator.md) and
[ADR 0004](decisions/0004-reviewed-project-creation.md).

The detailed [Project Creator and environment analyser master plan](project-creator-masterplan.md)
prioritizes new applications in the existing workspace. P0 through P2 are delivered
within their documented limits. P3 provides existing-build inspection,
sysbuild domain selection, a read-only Device Tree connection and an explicit Memory
report handoff. Current source/binding navigation is available for inspected builds and
applications before configuration; deeper source attribution remains future work.
The first P4 slice adds named board-target/SDK configurations to saved applications,
with explicit selection for reviewed builds and captured selection in attempt history.
Reviewed configuration editing now supports renaming and refreshing target/SDK choices,
with an explicit optional default-overlay patch. Import/reconnect retains history;
managed build outputs support verified reuse and cleanup. Ordered Kconfig/overlay selection is also available with
independent automatic, append and replacement modes, bounded app-local discovery and
reviewed source checks. Visual overlay/hardware editing is the next planned workflow,
scoped independently in the editor master plan; advanced creator milestones remain separate.
P5 provides a Minimal C++ starter alongside the original C template and curated local
Hello World, Blinky and Button cloning through the same reviewed creation/build workflow.
Sample copies retain nested supporting files, license evidence and origin hashes;
unsupported layouts or changed source inputs block copying. Further sample adapters remain a later
template expansion, with source provenance, licenses and dependency checks required.
The first custom-board workflow registers workspace board roots with reviewed
rename/reconnection/removal and environment invalidation. A guided creator copies an
eligible local reference port into a new board root, directly or through a supported-SoC
filter, with reviewed files and retained provenance. Hardware settings are inherited;
visual customization and device validation remain separate. See [custom boards](custom-boards.md).
Later stages cover editing, advanced
composition and environment migration. Build evidence does not establish hardware operation.

## TinyML model and experiment analysis

Start with durable data models and imports before device orchestration. Core identities
should include experiment, run, model hash, build/ELF identity, framework version, board,
dataset/sensor-replay identity, workload phase and measurement method.

Useful comparison groups from the research plan are `Base System`, `Inference`,
`Retraining`, `Model Switch`, and `Multicore`. Compare like conditions, expose deliberate
differences and retain repeated runs rather than only a summarized number.

| Comparison | Measurements to retain |
| --- | --- |
| Framework/model inference | F1 score, inference duration, static footprint, observed runtime memory |
| Local retraining | Quality change, training duration, additional reservations and observed use |
| Real-time interference | Response-time distribution, period/deadline, misses and measurement method |
| Model replacement/cloud workflow | Model/update bytes, activation delay, interruption and temporary overlap |
| Multicore/portability | Core/image mapping, shared memory, speedup, board/configuration differences |

Future framework adapters can expose arena statistics and model/operator information.
They should not assume all runtimes support on-device training or expose identical
allocators. Record instrumentation overhead and distinguish observed peaks from proven
worst-case bounds. Coordinate with Memory Explorer through a public artifact/measurement
contract rather than importing its private persistence implementation.

## Cross-feature work when needed

Introduce public artifact references, persistent workspace settings, richer job history
or device-capture services when a concrete feature needs them. Keep records versioned
and test migration paths. Avoid introducing a general plugin marketplace, cloud backend
or training scheduler merely to reserve names for future work.

Each implementation slice should have acceptance cases, tests, a declared data contract
and an updated delivered-scope description. Use [feature proposals](templates/feature-proposal.md)
and [decision notes](decisions/) when they make a long-running change easier to resume.
