# Zephyr Memory Explorer: master plan

Status: M1 implemented in version 0.1 and integrated into the shared application in version 0.2; later measurement stages remain proposed. Prepared on 2026-11-04 against the local Zephyr checkout `v4.4.2-103-gfbab2f598d5`. See the repository README for the delivered scope and launch instructions, and [architecture](architecture.md) for the current feature layout. The implementation uses a standard-library Python server and buildless JavaScript UI to avoid additional framework and frontend-build dependencies; the analysis interfaces remain independent of the UI.

## Product goal

Provide a local, English-language tool that turns an existing Zephyr build into an understandable memory report and a reproducible comparison record. The main questions are:

- What occupies RAM and ROM/Flash?
- What changed between two builds?
- Which changes are worth testing to reduce memory?
- Did the optimization preserve model quality and real-time behavior?

Start with memory analysis. Keep interfaces for later timing, model-quality, and experiment-analysis helpers in this repository without building a general benchmarking platform first.

## Research context

The supplied thesis definition describes TinyML framework comparisons, local retraining, real-time interference, model replacement, and multicore execution. These are research requirements and context, not instructions to implement all experiments in this tool now.

| Research comparison | Contribution of the memory tool | Additional evidence needed later |
| --- | --- | --- |
| Two frameworks, same model and reference board | Static RAM, ROM, framework/model attribution, configuration differences | Observed peak RAM, F1 score, inference duration, integration effort |
| Inference versus local retraining | Additional reservations, training code and buffers | Phase-specific runtime memory, training duration, F1 change |
| Real-time task with inference/training | Memory cost of threads, stacks and buffers | Response times, jitter, deadline misses from timing measurements |
| Model replacement / cloud workflow | Firmware and model artifact sizes; update-buffer reservations | Transfer bytes, activation delay, interruption duration, transient runtime overlap |
| Multicore and second-board port | Per-image/per-region footprint; clearly labeled platform comparisons | Concurrent memory use, speedup, deadline misses, shared-memory ownership |

The build report alone cannot establish maximum RAM demand, schedulability, or inference quality.

## Existing Zephyr functionality to reuse

Zephyr already provides `ram_report`, `rom_report`, interactive `ram_plot`/`rom_plot`, and a build dashboard. The new value is build selection, durable snapshots, reliable comparisons, and optimization experiments. See the [official optimization-tool documentation](https://docs.zephyrproject.org/latest/develop/optimizations/tools.html).

The checked source also includes `scripts/footprint/fpdiff.py`. Use it as a reference, but implement normalized comparisons: its matching by tree position and identifier does not cover all duplicate-symbol and path-movement cases.

Reuse the report generator from the selected build's Zephyr checkout through a version-aware adapter. Preserve the original JSON alongside a small, versioned internal format. Do not parse the decorative terminal table.

## Primary workflow

Launcher: `zephyr-utils memory` after installation, or `./zephyr-utils memory` from the checkout. It starts a local service and opens the browser.

1. **Select Project**: choose a project/workspace folder or paste a path. A backend folder browser provides local directory selection; a browser upload dialog is not a substitute for selecting a build path.
2. **Select Build**: discover builds under that location, or accept a direct build-directory path. Show application, board, image/domain, artifact timestamp and readiness. Remember recent selections. Bound discovery to selected roots and avoid recursive symlink loops.
3. **Generate Reports**: analyze the existing final ELF with the build's report generator and Python environment. Show progress, useful errors and expandable logs.
4. **Explore Memory**: open the overview, drill into large components, inspect individual symbols.
5. **Save Snapshot**: retain the report, metadata and evidence under a descriptive name such as `tflm-inference-baseline`.
6. **Compare Builds**: choose a saved baseline and candidate. Explain total changes, largest contributors and configuration differences.
7. **Export**: save data or a report suitable for discussing results and preparing thesis figures.

Every successful generation creates a durable capture; naming and tagging it are optional. Reusing a build directory must not overwrite previous evidence.

### Three input modes

| UI action | Behavior | Build effects |
| --- | --- | --- |
| `Generate Reports` | Run the selected Zephyr generator on the existing ELF | No firmware build or source/configuration changes |
| `Import Reports` | Load previously generated RAM/ROM JSON or a saved snapshot | No build tools required; absent metadata stays unknown |
| `Refresh Build & Reports` | Invoke the native report targets for the selected build/image | Can rebuild dependencies; explicit separate action |

The native equivalents are:

```sh
west build -d /path/to/build -t ram_report
west build -d /path/to/build -t rom_report
# Generate both reports through the native targets:
west build -d /path/to/build -t footprint
```

In the checked checkout, these targets write `ram.json` and `rom.json` in the image build directory and depend on the final ELF. Invoking them can therefore rebuild changed inputs. Evidence: `cmake/reports/CMakeLists.txt`, lines 14-46.

For default analysis, invoke `scripts/footprint/size_report` directly, with the resolved ELF, Zephyr base, workspace and build paths. Keep its `--output` argument pointing to the original build directory for source-path classification; use an absolute snapshot path containing `{target}.json` for `--json`. The checked implementation supports `ram rom` in one invocation and requires DWARF information. Discover supported arguments rather than assuming all Zephyr versions are identical.

Detect sysbuild through `domains.yaml`; let the user select an image and resolve its recorded build directory. Native RAM/ROM targets must run within that image's build directory. See [sysbuild's dedicated image targets](https://docs.zephyrproject.org/latest/build/sysbuild/index.html#dedicated-image-build-targets).

## Interface and visualizations

Navigation: `Overview`, `Memory Explorer`, `Compare`, `Snapshots`; add `Optimization Insights` and `Runtime Memory` when their data is available.

| View | Presentation | User question |
| --- | --- | --- |
| `Overview` | RAM and ROM totals, region/partition budget bars when known, build identity, attribution coverage | How large is this build and where is capacity tight? |
| `Memory Explorer` | Treemap with click-to-drill-down, breadcrumbs, synchronized sortable tree/table | Which subsystem, file or symbol takes the space? |
| `Largest Consumers` | Horizontal bars and exact byte values; RAM/ROM switch | Where should I investigate first? |
| `Sections & Regions` | Section breakdown and later an address map | Is the space code, constant data, initialized data, BSS, reservations or gaps? |
| `Compare` | Baseline/candidate totals, signed delta bars, added/removed/changed symbols | What caused the observed footprint change? |
| `Optimization Insights` | Evidence cards linking to affected symbols, config and validation steps | Which concrete experiment should I try next? |
| `Runtime Memory` | Reserved versus observed-used stack/heap/arena memory, with run/phase labels | Which reservations may have headroom? |

Prefer a treemap and ranked bars initially; an optional sunburst can follow. Keep colors stable across builds, supply exact values in a table, and make search/drill-down work with keyboard navigation. Use bytes internally and offer B/KiB display. Positive deltas mean increased footprint. Never rely on color alone.

Support source/module grouping first. Add editable semantic tags such as `Kernel`, `Drivers`, `Application`, `ML Runtime`, `Model Weights`, `Tensor Arena`, `Sensor Buffers`, and `Training State`. Show whether a tag comes from a configured mapping, a naming heuristic, or a user annotation. Unattributed data remains visible.

## Memory accounting rules

1. **Static footprint and runtime usage are separate measurements.** A reserved 64 KiB arena is a 64 KiB reservation even when only part is occupied. Stack/heap/arena usage is a view inside that reservation, not extra RAM to add to it.
2. **ROM is not ELF file size.** Debug information and other nonloaded ELF contents must not inflate the Flash metric. External model assets, signed images, headers, trailers and update slots need separate deployment-size accounting.
3. **Initialized data can occupy both RAM and ROM.** In XIP configurations its initial bytes are stored in ROM and its writable instance lives in RAM. Do not present RAM + ROM as a unique physical-memory total.
4. **Report share is not board utilization.** Native report percentages use report totals. Budget bars require trustworthy region/partition capacities and matching used-byte accounting; otherwise show `Capacity unknown`.
5. **Board capacity is not necessarily application capacity.** Bootloader partitions, shared regions, external memory and reserved space matter. Prefer the linked memory layout and actual application partition over a chip's advertised size.
6. **Keep unexplained bytes visible.** Preserve `(hidden)`, `(no paths)`, padding and unsupported-section diagnostics. Reconcile attributed totals against report totals; do not silently discard discrepancies.
7. **Source attribution can be incomplete.** Missing DWARF, libraries, LTO and compiler-generated symbols may prevent file attribution. Offer a clearly labeled section-only result when supported, or a precise diagnostic.
8. **Multiple images are not automatically additive.** A bootloader and application may reuse RAM at different times. Shared memory may appear in multiple images. SMP can use one image; sysbuild domains do not automatically correspond to CPU cores.
9. **Measured peaks are observed peaks.** Record workload, duration, phase and instrumentation. Independent stack/heap high-water marks are not a synchronized whole-system peak or proof of a worst-case bound.

## Snapshots and comparisons

A snapshot contains raw reports, normalized records, `.config`, available build metadata, relevant map/Devicetree evidence, generation logs, artifact hashes and schema/tool versions. Archive the analyzed ELF when feasible so it remains independently inspectable; a hash alone cannot reconstruct a deleted artifact. Store large artifacts by content hash to avoid needless duplication.

Capture board qualifiers, image/domain, Zephyr version, toolchain/compiler, optimization profile and LTO state. Include app/framework/model versions or hashes, and optional experiment tags, notes and integration effort. Distinguish original build-recorded metadata from the checkout observed at analysis time: the current Git commit does not prove which source produced an older ELF.

Normalize workspace-dependent path prefixes while preserving raw paths. Match symbols with normalized source identity, section, original name and additional disambiguation where available. Do not use an address as a stable identity across builds. Flag ambiguous matches; show reliable file/module deltas even when individual symbol correspondence is uncertain. Added or removed symbols have no meaningful percentage delta against a zero baseline.

Before interpreting a comparison, show mismatches in board, model, Zephyr/toolchain, optimization and profiling settings. Allow deliberately different comparisons with visible labels. A second-board comparison is a portability result, not an isolated framework-cost experiment.

Recommended experiment sequence:

- `Base System`: sensor input/replay and the reference real-time task.
- `Inference`: the same system plus the selected framework and model.
- `Retraining`: the same system plus the chosen training component.
- `Model Switch`: the configured update and activation mechanism.
- `Multicore`: the corresponding parallel configuration with documented image/core mapping.

Pairwise deltas estimate the cost of those configurations. They are not universal component sizes: linking, shared dependencies and compiler optimization can change surrounding code too.

## Optimization features

Begin with deterministic, inspectable rules. Every finding has `Evidence`, `Affected Footprint`, `Suggested Experiment`, `Trade-offs`, and `Validation`. Affected bytes are not a promise of reclaimable bytes; only a completed comparison establishes observed savings.

| Candidate | Evidence to expose | Experiment and validation |
| --- | --- | --- |
| Large thread/ISR stacks | Linked reservation, effective config, later stack high-water marks | Adjust one stack after representative workloads; retain a justified margin and rerun stress/interrupt cases |
| Large tensor arena or training workspace | Arena symbols, reservation, framework allocator statistics | Right-size after testing all model shapes and inference/training phases |
| Broad operator registration | Linked operator groups plus model/operator metadata when available | Register only required operators; validate model execution and outputs |
| Logging, shell, diagnostics, unused drivers | Actual linked symbols and effective `.config` | Test a lean configuration with required functionality preserved |
| Compiler optimization and LTO | Actual build flags and supported Kconfig options | Compare supported size/speed/LTO profiles; measure inference latency and deadline behavior again |
| Large constant or initialized arrays | Section, symbol, source and storage placement | Review immutable data placement and duplicate buffers; verify writable training data remains writable |
| Oversized sensor/batch buffers | Application annotations, sampling/window/batch dimensions | Reduce or reuse buffers only when lifetimes and concurrency allow; rerun functional/timing tests |
| Heap reservations | Distinct system/libc/custom heaps, effective sizes and allocator stats | Adjust the correct pool and verify allocation failures, alignment and fragmentation constraints |
| Struct padding | Optional `pahole` results and instance counts | Investigate layout changes with ABI/alignment checks; avoid a blanket packed-struct recommendation |
| Model representation | Model hash, quantization, model bytes and arena usage | Compare quantized or smaller-model variants as separate experiments with F1 and latency results |

Zephyr documents stack sizing and unused peripherals as footprint optimization areas. These are investigation candidates, not automatic removal decisions. See [footprint optimization guidance](https://docs.zephyrproject.org/latest/develop/optimizations/footprint.html).

For this checkout, relevant compiler settings include `CONFIG_SIZE_OPTIMIZATIONS`, `CONFIG_SIZE_OPTIMIZATIONS_AGGRESSIVE` and `CONFIG_LTO`. Availability and dependencies must be checked against the selected build. Heap analysis must account for subsystem contributions to effective pool sizes. The local TFLM `magic_wand` sample demonstrates selective operator registration and a statically allocated tensor arena.

Later, generate a reviewable configuration fragment and an experiment recipe. Run candidate builds in a separate directory and retain the baseline. Do not claim that a Kconfig change caused a particular symbol delta merely because both changed together.

## Runtime memory extension

Add runtime-data import before live device control. A small optional firmware helper can emit versioned records with run ID, ELF identity, timestamp/phase, allocator or thread identity, capacity, current use and observed high-water mark.

- Import Zephyr Thread Analyzer results for stack usage. Its monitoring and automatic thread affect the measured firmware; label profiling builds and account for measurement overhead. See [Thread Analyzer](https://docs.zephyrproject.org/latest/services/debugging/thread-analyzer.html).
- Import allocator statistics for supported heaps; do not assume every allocator is the kernel heap. Model the enclosing reserved region so usage is not counted twice. See [Zephyr heap concepts](https://docs.zephyrproject.org/latest/kernel/memory_management/heap.html).
- Add framework-specific arena statistics through adapters rather than assuming all TinyML frameworks expose the same allocator.
- Distinguish phases such as `Boot`, `Idle`, `Inference`, `Retraining`, and `Model Activation`; record whether counters were reset and their observation window.
- Show the reservation budget alongside observed usage. An optional synchronized trace can support a defined concurrent-use metric. A sum of separate observed maxima is shown only as a labeled derived sum with compatible run and observation windows; it is not a proven whole-system worst-case bound.
- Associate memory records with externally measured F1, inference/training duration, response times and deadline misses. Profiling builds must not silently stand in for the final real-time benchmark build.

## Technical approach

Recommended form: a Python analysis backend and a small TypeScript browser UI, served locally. FastAPI and a chart library such as ECharts are reasonable implementation candidates; select and pin versions during implementation. Package the built frontend with the Python tool so users do not need a frontend development server. Bind to loopback and bundle UI assets for offline use.

Keep analysis independent of the GUI so the same engine can later provide CLI export and CI budget checks. Start with versioned JSON snapshot directories and a small index; add a database only if history size warrants it.

Proposed modules:

```text
src/zephyr_utilities/
  cli.py
  memory/
    discovery.py       # Build metadata and sysbuild domains
    runner.py          # Report generation jobs and diagnostics
    adapters/          # Zephyr-version and report-format support
    model.py           # Typed normalized records and provenance
    accounting.py      # Sections, regions and reconciliation
    snapshots.py       # Immutable capture and artifact retention
    compare.py         # Matching and delta calculation
    insights.py        # Evidence-based optimization rules
    exports.py         # JSON, CSV and shareable reports
    api.py             # Local UI interface
frontend/              # English UI and charts
tests/fixtures/        # Small reference reports and build metadata
```

Minimum data entities: `Build`, `Image`, `Snapshot`, `MemoryRegion`, `Section`, `Symbol`, `Comparison`, `Finding`; later `Experiment` and `RuntimeRun`. Every metric carries units, source, memory scope and measurement kind. Missing values remain null/unknown, never zero.

Discover `CMakeCache.txt`, `build_info.yml`, final ELF/map, `.config`, `zephyr.dts`, and optionally `domains.yaml`. Resolve custom kernel filenames from metadata. Require only the artifacts needed for each capability; report imports should remain useful without the original checkout. The native JSON has `symbols` and `total_size`, with hierarchical node metadata, not physical capacities or runtime peaks.

Execute commands with argument lists, explicit working directories and the selected build environment. Keep one mutating job per build directory, provide cancellation and retain failures in logs. Capture/hash inputs consistently and detect an ELF changing during analysis so a snapshot cannot silently combine two builds. Source paths from reports are data and must be escaped in rendered output.

## Delivery stages and completion criteria

| Stage | Scope | Complete when |
| --- | --- | --- |
| **M1: Useful memory explorer** | Build/image selection, existing-ELF generation, JSON import, RAM/ROM overview, treemap/tree, largest consumers, durable snapshots, basic two-snapshot comparison, JSON/CSV export | A real built application can be selected and analyzed without rebuilding; totals reconcile; a saved baseline remains usable after the build folder changes |
| **M2: Optimization workflow** | Effective config diff, reliable region budgets where supported, richer matching, evidence-based findings, explicit refresh action, configurable regression budgets, standalone HTML and SVG/PNG export | Each recommendation links to evidence and an experiment; comparison explains where measured changes occurred and exposes uncertainty |
| **M3: TinyML runtime evidence** | Stack/heap/arena import, phase labels, model metadata, experiment grouping, quality/timing result attachments | Static reservations and observed usage are clearly separated; inference/retraining comparisons retain workload and instrumentation metadata |
| **M4: Extended experiments** | Candidate-build recipes, optional device capture, detailed multicore/shared-memory and model-switch views, CLI/CI integration | Repeated experiments can be compared with auditable artifacts and appropriately scoped metrics |

M1 deliberately includes basic comparison: it is the first feature that turns a visual report into a useful thesis helper. Do not delay it until after a sophisticated advice engine. Automatic optimization, flashing, OTA deployment and training orchestration are outside M1.

Validation during implementation should use a small set of real builds and focused fixtures: duplicate symbols, changed path prefixes, initialized data, hidden bytes, zero-sized baselines, missing DWARF, missing capacity, custom ELF names and sysbuild domains. Cross-check bytes against the selected Zephyr generator, test unchanged-input snapshot stability, and verify the default analysis does not modify the build. Larger reports should keep search and navigation responsive without changing the underlying totals.

## Evidence consulted

- User-supplied `Definition_MA.pdf`, both pages: thesis comparisons and measurement requirements.
- Local `cmake/reports/CMakeLists.txt`: report targets, JSON paths and ELF dependencies.
- Local `scripts/footprint/size_report`: command arguments, DWARF requirement, section classification, tree schema and JSON export.
- Local `scripts/footprint/plot.py` and `fpdiff.py`: visualization and symbol-matching behavior.
- Local `scripts/dashboard/README.md` and `dashboard.py`: existing dashboard and build metadata discovery.
- Local `scripts/pylib/build_helpers/domains.py`: sysbuild domain schema.
- Local `Kconfig.zephyr`, `doc/kernel/memory_management/heap.rst`, `include/zephyr/sys/sys_heap.h`, `subsys/debug/thread_analyzer/Kconfig`, and TFLM samples: optimization and instrumentation constraints.

Local source references above are relative to the inspected Zephyr checkout. Public documentation links describe current upstream capabilities; adapters must still verify the user's selected checkout.
