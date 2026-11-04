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
- Reviewed minimal-C workspace application creation, explicit configure/build jobs and
  versioned profiles/attempt evidence in fresh output directories.

## Feature ownership and next useful slices

| Feature | State | Initial implementation direction |
| --- | --- | --- |
| Memory Explorer (`memory`) | Available | Existing-build reports, visualization, snapshots and comparisons |
| Device Tree Editor (`devicetree`) | Planned | Inspect resolved tree and source overlays, then add validated reviewable edits |
| Project Creator (`projects`) | Analysis, minimal creation and explicit single-image builds available | Next: existing-build inspection and Device Tree handoff |
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

## Device Tree Editor

Build toward a complete editor incrementally:

1. Read a selected build's resolved Device Tree and expose node/property/source context.
2. Navigate application overlays, board definitions and bindings without changing them.
3. Add syntax/binding diagnostics with source locations from the selected Zephyr version.
4. Preview proposed overlay edits and apply them to an explicit destination.
5. Integrate deliberate validation/rebuild workflows through shared jobs.

The [Project Creator and environment analyser master plan](project-creator-masterplan.md)
defines the proposed shared build context, source-overlay handoff and staged editor
integration. A resolved tree belongs to an application configuration and image/domain,
not just a board selection.

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
advanced profile editing, project import and Device Tree handoff deferred. See the
[Project Creator guide](project-creator.md) and
[ADR 0004](decisions/0004-reviewed-project-creation.md).

The detailed [Project Creator and environment analyser master plan](project-creator-masterplan.md)
prioritizes new applications in the existing workspace. P0 through P2 are delivered
within their documented limits. P3 introduces existing-build inspection and the first
Device Tree reader connection; later stages cover editing, advanced composition and
environment migration. Build evidence does not establish hardware operation.

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
