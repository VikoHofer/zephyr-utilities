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

## Feature ownership and next useful slices

| Feature | State | Initial implementation direction |
| --- | --- | --- |
| Memory Explorer (`memory`) | Available | Existing-build reports, visualization, snapshots and comparisons |
| Device Tree Editor (`devicetree`) | Planned | Inspect resolved tree and source overlays, then add validated reviewable edits |
| Project Creator (`projects`) | Environment analysis available; creation planned | Next: select a template and preview safe project generation |
| TinyML Experiments (`tinyml`) | Planned | Connect model/build/run identities and compare imported experiment measurements |

Planned catalog entries are development boundaries. They do not yet edit Device Trees,
create projects, run inference or perform training.

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

P2 begins a small reproducible project-generation workflow:

1. Discover boards and template requirements from the selected workspace/version.
2. Collect application name, destination and configuration choices.
3. Produce a preview of the exact files and options to be generated.
4. Create the project without overwriting unrelated existing files.
5. Offer an explicit build as a shared job and make its artifacts available to other tools.

Keep template versions and generated build commands in project metadata. A new project
should integrate with the existing Zephyr environment; it must not require another
utilities server or hidden environment installation.

The detailed [Project Creator and environment analyser master plan](project-creator-masterplan.md)
prioritizes new applications in the existing workspace. It stages local capability
analysis, board/SoC and installed-SDK selection, safe generation, Device Tree integration,
advanced composition and environment migration. P0/P1 are delivered within the documented
analysis scope; P2 and later creation/editor/build workflows remain proposed work.

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
