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

The 2026-11-07 Device Tree Editor direction replaces the earlier connection/module plans:
automatically load the selected chip, display its pins, and generate overlay assignments
from pin selections. The first stage covers GPIO, UART, I2C and SPI on representative
STM32, Nordic, Espressif, NXP and Raspberry Pi microcontrollers. Retire the old sensor/module
workflow as part of the replacement. See the [chip and pin redesign plan](device-tree-editor-pin-workspace-plan.md)
for researched data sources, exact initial profiles, removal scope and acceptance gates.
Automatic chip loading, old-workflow removal and GPIO/UART/I2C/SPI activation include
missing-overlay drafts and first Save. The subsequent request expands STM32/Nordic
from selected Zephyr DTS, bindings and pin tables rather than manual chip entries; see
the [automatic chip support handoff](device-tree-editor-automatic-chip-support-handoff.md).
Additional source formats, routing/ownership models and the other three vendor adapters
remain bounded by observed evidence. The five-vendor matrix remains open. Historical stage checks remain below.

| Feature | State | Initial implementation direction |
| --- | --- | --- |
| Memory Explorer (`memory`) | Available | Existing-build reports, visualization, snapshots and comparisons |
| Device Tree Editor (`devicetree`) | Automatic STM32/Nordic source discovery and capability-gated GPIO/UART/I2C/SPI editing with existing/new-overlay Save and recovery; sensor/module workflow removed | Extend understood routing/ownership formats; ESP32/K64/RP2040 adapters and the five-vendor acceptance matrix remain open |
| Project Creator (`projects`) | Applications, creation/build and environment discovery available | Maintain the delivered everyday workflow and add focused public editor handoffs |
| Build Inspector (`inspector`) | Advanced build, resolved-tree, current source and binding navigation available | Preserve available inspection; further depth deferred |
| TinyML Experiments (`tinyml`) | Planned | Connect model/build/run identities and compare imported experiment measurements |

Planned catalog entries are development boundaries. TinyML does not yet run inference or training.

## Memory Explorer

The next memory-focused slices are reliable region/partition budgets, reviewable
optimization experiments, richer export formats, and runtime stack/heap/arena import.
Keep static footprint separate from runtime use. An optimization finding should show
its evidence, proposed experiment, trade-offs and observed result.

The detailed [memory master plan](memory-explorer-masterplan.md) remains the research and
measurement reference. The architecture guide describes the current implementation layout.

## Build Inspector and Device Tree Editor

Advanced → Build Inspector opens an inspected image's resolved DTS, supports node/property
navigation and shares Projects' target/domain context. It checks output identity;
source freshness remains unknown. See the [build inspection guide](build-inspection.md).
Current-source browsing and compatible-based binding navigation are also available from
the selected image. Generated header records are distinguished from current candidates;
include links and direct property summaries do not perform schema resolution or validation.
Applications can also be opened before configuration to browse current sources and local
bindings. Conventional include locations are candidates, not configured search paths.
Device Tree Editor automatically opens a supported selected chip and its configured pins,
with a logical diagram, pin inspector and retained source/history/Save/recovery. The old
sensor/module workflow and its creation commands are removed. The
[chip and pin redesign](device-tree-editor-pin-workspace-plan.md) is the active direction;
visual pin commands and missing-overlay creation use automatic STM32/Nordic discovery
with source-format and ownership checks. Further advanced inspection
and peripheral authoring remain outside this stage.

### Historical editor stages

The following records describe earlier implementations and their original checks. Removed
forms and services mentioned here are no longer available. These records do not authorize
continuing the former roadmap.

D0 foundation is complete: the [editor contract](device-tree-editor-contract.md) fixes
ownership, context/version proposals and source-preservation limits, with a tested
in-memory literal-edit prototype. D1 adds read-only project/configuration context loading,
overlay evidence and source inspection through an explicitly injected preview in the
existing application. See the
[D0 handoff](device-tree-editor-d0-handoff.md) and [D1 handoff](device-tree-editor-d1-handoff.md)
for evidence and remaining limits. D2 now implements production in-memory source
operations, with verification and measured limits recorded in the [D2 handoff](device-tree-editor-d2-handoff.md).
D3 connects those operations to visual/source session drafts, shared undo/redo and
context-bound navigation retention. See the [D3 handoff](device-tree-editor-d3-handoff.md)
for verification and measured service latency. D4 adds guarded single-overlay saving,
conflict comparison, explicit reload/discard and versioned recovery, making the limited
editor available. See the [D4 handoff](device-tree-editor-d4-handoff.md). D5 adds explicit
saved-source Configure and a separate Projects build handoff; saving alone does not
validate firmware. See the [D5 handoff](device-tree-editor-d5-handoff.md) for input
coverage, staging limitations and verification. D6 adds explicitly loaded local schemas,
typed property forms, actual provider specifier cells, safe label addition and alias/chosen
references on the shared source command path. Defaults remain informational until Apply;
inherited values, external references, macros and label rename remain outside visual
support. See the [D6 handoff](device-tree-editor-d6-handoff.md) for scope and verification
status. D6 was committed as `564fcff`. D7 adds an accessible local topology, supported
controller settings, binding-selected bus children and GPIO LED/button forms. Atomic
address changes retain unit-address/`reg` consistency, collision checks reject duplicate
local allocations, and guarded leaf removal preserves shared controllers and chip-select
arrays. Required values are explicit; inherited hardware, physical routing and module
recipes remain unknown or planned. D0–D7 implementations are complete within these
scoped boundaries. D7's real Configure smoke, full checks,
browser verification and package verification passed as recorded in the
[D7 handoff](device-tree-editor-d7-handoff.md).

D8 adds the first Nordic pin adapter for exactly `nrf52840dk/nrf52840` and its supported
Zephyr 4.4.2 source profile. It revalidates 41 local evidence files and a single selected
overlay before using inherited board facts. UART/I2C/SPI signals have explicit default/sleep
state forms; a 48-pin table exposes known connector mappings, reservations and allocations.
Source commands retain symbolic pin/GPIO constants, copy inherited states, require an
explicit shared-state split and preserve other consumers. Local direct GPIO entries have
symbolic flag controls. Known conflicts and shared controller instances are diagnosed;
read-only PWM/QSPI evidence is retained. No board drawing, package/electrical guarantee,
custom-board resolver, module recipe or multi-overlay composition is implied. D8's current
verification is recorded in the [D8 handoff](device-tree-editor-d8-handoff.md). D8 is
committed as `cfaf10e`.

D9 adds a vendor-independent, data-only catalogue for BME280 I2C/SPI devices and GPIO
LED/button leaves, with a keyboard-accessible logical canvas and binding-aware connection
forms. It uses supported unique local bus/group declarations and actual provider cells;
it does not expand D8's inherited pin adapter. Each recipe creates one leaf, and removal
preserves shared parents and providers. Handwritten nodes are recognized structurally
without conversion. Source and shared Undo/Redo remain canonical, while separately saved
layout has its own versioned, scoped records. Software requirements are displayed without
changing Kconfig. D9 implementation and passed verification are recorded in the
[D9 handoff](device-tree-editor-d9-handoff.md). D9 is committed as `3cf6db1`.

D10 adds unified source-linked diagnostics for current local resources and supported pin
evidence, distinguishing conflicts from unknown facts. Reviewed removal lists descendants
and known consumers, permits individually selected safe optional-property repairs and
applies one canonical draft command. Required/ambiguous/mixed dependencies need explicit
source or form repair. Source Save and Configure remain separate. Passed full checks,
browser verification, actual-binding smoke and package checks are recorded in the
[D10 handoff](device-tree-editor-d10-handoff.md). D10 is committed as `6403e43`.
D11 advanced peripherals and system resources is delivered in bounded slices. D11a adds
vendor-independent local single-specifier PWM consumer/LED forms and PWM diagnostic
coverage, with passed full checks, browser verification, actual-binding smoke and package checks in the
[D11 handoff](device-tree-editor-d11-handoff.md). D11b adds single-specifier local ADC
consumers and common ADC channel configuration, with implicit consumer removal guards;
see the [D11b handoff](device-tree-editor-d11b-handoff.md). D11c adds exact interrupt GPIO
profiles for BMI270 INT1, HDC2010 DRDY/INT and MAX30210 INT1, using literal local GPIO
provider evidence and preserved numeric flags. Trigger mode, software enablement and
physical suitability remain unknown. Full checks, browser verification, actual-binding
smoke and installed-package checks passed as recorded in the
[D11c handoff](device-tree-editor-d11c-handoff.md). D11d adds local nominal CAN settings
and existing active-transceiver selection for three explicit controller profiles, plus
declared-limit diagnostics and guarded dependency removal. See the
[D11d handoff](device-tree-editor-d11d-handoff.md). Broader CAN/interrupt profiles and
the remaining family matrix stay planned.

The [Project Creator and environment analyser master plan](project-creator-masterplan.md)
provides the earlier shared-context proposal; the chip and pin redesign defines the
current editor delivery gates. A resolved tree belongs to an application configuration
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
reviewed source checks. Broader hardware editing follows the delivered overlay workflow,
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
