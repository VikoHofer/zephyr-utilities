# Zephyr Utilities

One local application for Zephyr development, build analysis and experiments.

**Zephyr Utilities 0.2** provides an English, offline interface with shared navigation,
workspace selection and background jobs. Memory Explorer and Project Creator's environment
analysis, minimal C/C++ creation, curated local sample cloning and explicit configure/build workflow are available.
Advanced Build Inspector reads existing outputs, resolved Device Trees, current sources
and bindings, and carries the selected image into Memory Explorer for report generation.
Device Tree Editor supports visual status/property edits, local binding-aware forms and
references, local UART/I2C/SPI and GPIO topology, editable overlay source, reviewed
single-file Save, draft recovery and explicit saved-source Configure. Nordic pin assignment
supports the exact verified nrf52840dk/nrf52840 profile. Hardware modules and broader pin
adapters remain planned.
TinyML model/experiment analysis also remains planned.

## Start

Install once into your existing Zephyr virtual environment (Python 3.10+):

```sh
source /path/to/zephyrproject/.venv/bin/activate
python -m pip install -e /path/to/zephyrproject/zephyr-utilities
```

After activating that environment, start the utilities from **any directory**:

```sh
zephyr-utils
```

This opens the Home tool catalog at `http://127.0.0.1:8765/#/home`. Open **Memory Explorer**,
select a project or build folder, choose a completed build, and click **Generate reports**.
For environment discovery, open **Project Creator** and click **Analyze workspace**.
Then use **Create application** to select a target/SDK, preview files and create a new app.
The initial workspace is detected from `.west` in the current
directory's ancestors, then from the workspace containing the launcher's `.venv`.
If neither is available, the current directory is used. `--root` overrides detection.

The editable installation keeps the command connected to this checkout: ordinary
Python/UI changes are available on the next launch. Reinstall when package dependencies
or console entry points change. No shell alias or changes to `.venv/bin/activate` are needed.

The explicit tool command and its options also remain available:

```sh
zephyr-utils memory --root /path/to/zephyr/workspace
zephyr-utils --help
```

Without installation, the source-checkout launcher works with Python and PyYAML:

```sh
./zephyr-utils
```

Options:

- `--root PATH`: initial project, workspace, or build folder; overrides automatic discovery.
- `--data-dir PATH`: persistent snapshot storage. Defaults to
  `$XDG_DATA_HOME/zephyr-utilities/memory`, or `~/.local/share/zephyr-utilities/memory`.
- `--port 8765`: local HTTP port; `--port 0` selects an available port.
- `--no-browser`: print the address without opening a browser.

Options work with the bare command or before/after the `memory` subcommand.
Snapshot storage is independent of the working directory. If you previously used the
checkout's `.memory-explorer` directory, open that history explicitly:

```sh
zephyr-utils --data-dir /path/to/zephyrproject/zephyr-utilities/.memory-explorer
```

Stop the application with Ctrl+C. Saved snapshots remain available on the next start
using the same data directory. No Node.js, frontend build, internet access, or cloud
account is needed to run the UI.

## One application, independent features

All tools use the same launcher, server, session, workspace and background-job service.
Switch tools from the sidebar without starting another process. Returning to Memory
Explorer restores its session state and continues tracking active report jobs.

```text
src/zephyr_utilities/
  cli.py / app.py         # Launch and compose the application
  core/                  # Shared HTTP, feature contracts, workspace and jobs
  features/
    memory/              # Working Memory Explorer and its web/ assets
    devicetree/           # Overlay editing, guarded Save and draft recovery
    inspector/            # Advanced build, resolved tree and source inspection
    projects/            # Environment analysis, reviewed creation and explicit builds
    tinyml/              # Planned model and experiment analysis
  web/                   # Shared browser shell and helpers
  memory/                # Compatibility imports for the original release
```

Each feature owns its routes, services and UI. The explicit feature registry supplies
the Home catalog; contributors add modules without new launcher handlers or HTTP-server
branches. See [architecture](docs/architecture.md) and [adding a feature](docs/adding-a-feature.md).

## What works

- Analyse the selected local Zephyr environment, browse declared board/chip targets and
  revisions, inspect installed SDKs, and retain environment observations with diagnostics.
- Create a minimal C or C++ application inside the existing workspace from a reviewed file plan.
- Clone local Hello World, Blinky or Button samples with supporting files, license evidence and source provenance.
  Select an exact board target and installed GNU SDK, then explicitly configure or build
  in fresh or verified reusable outputs with saved commands, logs and attempt evidence.
- Import or reconnect workspace applications, review saved configuration edits, and clean
  managed build outputs independently of attempt history.
- Browse local folders and discover ordinary Zephyr builds and sysbuild images.
- Generate RAM/ROM JSON directly from an existing final ELF with that build's Zephyr
  `size_report` script and recorded Python environment. This does not invoke CMake,
  Ninja, `west build`, or flashing.
- Follow generation progress and logs, and cancel active report jobs.
- Explore RAM and ROM totals, proportional treemaps with drill-down, hierarchy tables,
  largest consumers, symbol/path/section search, and B/KiB units.
- Automatically save every completed capture, including raw reports, artifact hashes,
  build configuration, available metadata and content-addressed copies of the ELF and
  related build artifacts. Edit snapshot names and notes without changing evidence.
- Delete an old snapshot from the Snapshots list after confirming its name. The list,
  open report and comparison selections update immediately; original build files remain
  untouched. Shared archived artifact objects are retained, so deleting a snapshot does
  not reclaim all of its archived build storage.
- Compare two snapshots: signed total deltas, added/removed/changed symbols, configuration
  changes, and warnings about incompatible or incomplete comparison metadata.
- Import one or both native Zephyr `ram.json` / `rom.json` reports, and export a snapshot
  as JSON or its symbol records as CSV.

The browser talks only to a loopback service. Build selection and report generation run
on the computer running the launcher. Treat selected build environments as executable
local tools, as you would when running their Zephyr commands yourself.

## Environment analysis

Open **Project Creator** from Home or use `/#/projects/environment`. **Analyze workspace**
discovers local boards, chips, SDK installations, tool versions and module-provided roots.
Search by board/target, chip family and vendor, then inspect the exact declared targets
and their origins. Add explicit board/chip roots or installed SDK paths when needed.

Analysis is offline and does not configure, compile, flash, download or update firmware
tools. It reports discovered metadata, not build or hardware readiness. Missing tools
and unsupported definitions produce useful partial results. The initial hardware adapter
supports tested hardware model v2 metadata shapes; legacy hardware metadata is unsupported.
Linux is the tested host.

Observations are retained across restarts. Reopening shows saved evidence without
rechecking its freshness; use **Analyze again** after environment changes or **Full
rescan** to bypass catalogue reuse. Leaving the feature does not cancel an active scan.
Discovery remains read-only. The separate **Create application** view adds reviewed source
creation and explicit configure/build jobs. See the
[environment analyser guide](docs/environment-analyser.md) for discovery limits and the
[Project Creator guide](docs/project-creator.md) for its supported workflow and contract.

## Application creation and builds

Project Creator opens **Applications**. Its sidebar links separate application management,
**Create application**, **Custom boards**, and **Environment**. Detailed build/source inspection lives in
the optional **Advanced → Build Inspector** area. **Device Tree Editor** edits existing
application overlays; hardware modules and pin assignment are later stages.

Use **Create application** or `/#/projects/create` after analysis. Choose the name, new
destination, C/C++ template or supported local sample, exact target and installed GNU SDK; inspect **Preview application**, then
click **Create application**. Creation writes the reviewed files without starting a build.
For local samples, use **Check compatibility** to configure a temporary copy and check
the selected board's hardware requirements. Missing requirements block preview; unverified
results show a warning. Blinky uses a reviewed RGB LED adaptation on the official
ESP32-C3 DevKitC/M targets. Other boards retain the GPIO sample and are checked for `led0`.
Every new project also includes an editable default overlay named for its exact board
target, including discovered custom boards. Previews show expected overlay selection;
completed configurations show the files actually used, separately from copied overlays
for other boards. Existing project files are preserved.
Open **Applications** in the Project Creator sidebar. Choose **Configure fresh** or
**Build fresh**, review the exact command and new output path, then start that operation.
Managed build outputs offer explicit incremental reuse and reviewed disk cleanup. Each attempt retains its outcome and bounded
log; leaving the view does not cancel it.

Use **Add configuration** on a saved application to name another exact board target and
installed SDK. Select it from **Build configuration** before reviewing a build. Use **Edit selected** to rename or update an existing selection, including refreshing
Original after an environment rescan. Earlier attempts retain their captured choices.
An optional reviewed change creates the matching default overlay for an additional board. Fragment
selection supports automatic discovery, ordered appended files, or replacement
application-file lists for Kconfig and Device Tree overlays. Select existing files in
the application; saving the fragment selection keeps their contents unchanged. Use Device Tree Editor
to edit and separately review changes to an overlay's source.

The creator supports Linux, minimal C/C++ and curated sample applications with one image, an existing resolved
west workspace and installed GNU Zephyr SDKs. It rejects existing destinations, symlinked
application paths and source paths containing whitespace. No flashing, SDK installation,
or workspace update is included. Existing applications can be imported without changing
source files; moved applications can be reconnected while preserving their saved history.
Generated source files and their README commands work independently of the
utilities' metadata.

Use **Custom boards** to register an existing board root, reconnect a moved folder, or
remove a registration while retaining its source. Registered roots persist per workspace
and participate in environment analysis. The guided creator starts from a local reference
board, directly or after filtering by supported SoC, and previews all renamed board files
in a new hardware directory. It retains the reference's hardware settings and license
evidence; configure/build and physical validation remain separate steps. See the
[custom-board workflow](docs/custom-boards.md).

Use **Advanced → Build Inspector → Build outputs** to open an existing build, select its image/domain and
inspect saved outputs. **Inspect Device Tree** shows that image's resolved nodes and
properties without running tools or changing source files. Changed outputs require
re-inspection; source freshness remains unknown. **Open Memory Explorer** reviews the
same image before explicit report generation from its archived ELF. See the
[build inspection guide](docs/build-inspection.md) for the available P3 workflows and limits.
In Build Inspector, use **Browse sources** for recorded inputs or **Find bindings** on a node.
Current source text, binding candidates and binding paths recorded in generated output
remain distinct; this navigation does not validate or edit hardware configuration.

Before the first build, expand **Advanced details** on a workspace application and choose
**Browse sources**, or enter a directory in Build Inspector’s **Sources and Device Tree** view. This opens its current overlays, DTS files,
configuration fragments and local bindings without running tools. The application must
be inside the selected workspace and contain `CMakeLists.txt`. Include links use local
and conventional workspace Zephyr locations; they do not establish configured inputs,
an effective board or a resolved tree.

## Requirements and interpretation

The application itself needs Python and PyYAML. To generate reports, the selected
build's Python environment additionally needs the dependencies required by its Zephyr
`scripts/footprint/size_report` (commonly `pyelftools`, `anytree`, `colorama`, and
`packaging`). Existing Zephyr environments normally provide these. Missing dependencies
are reported in the generation log; report import does not need them.

Use an unstripped final ELF containing DWARF information. An incomplete build, removed
Zephyr checkout, or unavailable interpreter is shown with an actionable error. A folder
search is bounded to six levels, 6,000 visited folders and 100 results; select a more
specific folder or paste the build path when needed.

RAM reports describe **static footprint and reservations**, not runtime peak usage.
Initialized data may occupy both RAM and ROM; these are separate totals. File size of
an ELF is not its Flash footprint. Physical capacity and application partition budgets
are intentionally shown as unknown in this first release. Charts show shares of the
report, not percentages of the board's advertised capacity.

Snapshots retain local paths and build configuration; keep their storage private and
review exported configuration before sharing. Imported native reports have no verified
build identity. LTO, generated code and duplicate names can make symbol matching
ambiguous; comparisons expose that uncertainty instead of inventing exact matches.

Snapshot JSON exports use the application's normalized format; **Import reports** expects
native Zephyr JSON. To move complete history, copy the data directory, including `objects/`.

## Development and contributing

Use Python 3.10+ and Node.js 22+ for development checks. Node is not a runtime dependency.

```sh
python -m pip install -e '.[dev]'
python scripts/check.py --quick
python scripts/check.py
python scripts/verify_package.py
```

Checks cover lint, Python/JavaScript syntax, architectural dependency boundaries, Python
behavior and HTTP integration, and browser-module logic. Quick mode excludes only tests
marked as needing loopback sockets. Tests use temporary fixtures and do not rebuild or
change firmware projects. Package verification builds an offline wheel and checks its
imports, console entry point and bundled assets independently of the editable checkout.

GitHub Actions runs the checks and packaging verification on Python 3.10, 3.12 and 3.14.
The repository includes [agent guidance](AGENTS.md), [contribution instructions](CONTRIBUTING.md),
issue/PR templates, decision records and feature/handoff templates. To start a new feature:

```sh
python scripts/new_feature.py telemetry --title "Telemetry"
```

This creates a planned skeleton without overwriting existing work or registering an
unfinished workflow. Follow [the feature guide](docs/adding-a-feature.md) to implement
and register it. Extend the existing `devicetree`, `inspector`, `projects` and `tinyml` modules for
those tools rather than generating replacements.

The application uses a standard-library Python HTTP server and bundled HTML/CSS/JS.
The analysis modules are independent of HTTP. See the [platform contract](docs/platform-contract.md),
[environment analyser contract](docs/environment-analyser.md),
[Project Creator contract](docs/project-creator.md),
[memory API contract](docs/implementation-contract.md) and
[Memory Explorer master plan](docs/memory-explorer-masterplan.md) for details.
The [Device Tree Editor master plan](docs/device-tree-editor-masterplan.md)
divides live overlay editing, hardware-module placement and pin configuration into
16 implementation stages with individual prompts and acceptance gates.
Its [D0 foundation](docs/device-tree-editor-d0-handoff.md) defines the contracts and
tests a bounded source-edit prototype.
The [D1 read-only preview](docs/device-tree-editor-d1-handoff.md) adds project/configuration
context loading and overlay inspection through explicit development/test injection.
The [D2 source engine](docs/device-tree-editor-d2-handoff.md) adds pure in-memory,
reversible operations for literal properties, status and source node declarations.
It preserves surrounding overlay bytes. The [D3 draft preview](docs/device-tree-editor-d3-handoff.md)
connects visual status/property controls and editable source with diff and undo/redo.
The [D4 workflow](docs/device-tree-editor-d4-handoff.md) makes this limited editor available
in the normal application. Open a project/configuration or application directory, choose
an existing overlay, edit it, then use **Review and Save**. The review shows the destination,
source diff and unresolved validation before writing. Saving does not configure, build,
activate the overlay or change a recipe. Undo after Save produces another unsaved draft.

The [D5 workflow](docs/device-tree-editor-d5-handoff.md) adds **Review Configure** for
a saved overlay in a registered project/configuration. Review the candidate, selected
environment and command, then explicitly run Configure. Results retain candidate/input
identity, logs, cancellation and verified source diagnostic links. **Open in Projects**
selects the matching configuration attempt; compilation requires its separate Build
review. Unsaved staging is unsupported, so Save first. A successful Configure records
whether the overlay was observed; partial input coverage remains unknown freshness,
not proof of hardware behavior or a complete validation pass.

The [D6 workflow](docs/device-tree-editor-d6-handoff.md) adds **Load binding properties**
for the current overlay draft. Select a local node and binding property to inspect its
type, required/default/constant values and supported enumerations. Apply changes through
the shared source draft and Undo/Redo history; defaults and required fields are never
added by loading. **Reset local override** removes that local declaration and may reveal
an unknown inherited value or default. **Delete property in overlay** writes an explicit
deletion directive where supported.

Supported forms cover booleans, unsigned integers, strings, numeric/string/byte arrays,
local node references and provider-specific phandle arrays. Provider cell names come
from the actual local provider binding and declared cell count. Add safe node labels and
set/remove aliases or chosen references to unique declarations in this overlay. Label
renaming, inherited effective-tree resolution, macro evaluation and pin maps are outside
this stage. Missing/ambiguous bindings and unsupported fields remain explained and
source-editable. Inspection can be cancelled; Save, Configure and Build remain separate.

The [D7 workflow](docs/device-tree-editor-d7-handoff.md) adds **Load peripherals** and an
accessible list of local UART, I2C and SPI controllers, their children, and GPIO LED/button
groups. Configure supported controller fields, add a binding-selected device with its
explicit required values, or update/remove an existing leaf. Address changes update the
same node's unit-address and `reg` together. Duplicate local addresses/chip selects and
known local consumers prevent unsafe changes. All actions share the overlay draft and
Undo/Redo history.

Simple I2C/SPI forms require one resolved bus and explicit local address/size cell counts
of 1/0. I2C supports non-reserved 7-bit addresses `0x08`–`0x77`. Existing SPI GPIO
chip-select arrays keep their order and entries; native chip-select capacity remains
unknown. Device lists are bounded to 256 compatible candidates with explicit truncation.
GPIO fields use actual local provider cells, and new buttons require an explicit input
code for the selected GPIO-key driver. Loading adds no defaults or required properties.
Inherited controllers, pin routing and module placement remain outside this stage.
The D7 handoff records full checks, browser/package verification and the real Configure smoke.

The [D8 workflow](docs/device-tree-editor-d8-handoff.md) adds **Load pin assignments** for a
registered `nrf52840dk/nrf52840` configuration with one selected overlay. This first adapter
requires the supported Zephyr 4.4.2 source profile: 41 local evidence files are checked by
hash before board/controller facts are used. Changed or missing evidence, custom board
roots, shields and unsupported composition leave the forms unavailable with an explanation.
Opening a directory alone does not establish this exact target.

Choose a UART, I2C or SPI controller, its `default` or `sleep` state and a signal role, then
assign, reassign or unassign a pin. A pin table shows 48 SoC pins, observed board connector
names, reservations and uses. Selecting a table row fills the form; applying it updates
the same overlay draft and Undo/Redo history. Existing inherited states are copied into
the overlay when changed. A shared state requires an explicit split and retains its other
consumers. The editor preserves symbolic `NRF_PSEL` calls and GPIO constants, surrounding
source and required includes. It never enables or relocates a different controller.

GPIO forms edit supported local `gpios`/`*-gpios` entries referring directly to `gpio0`
or `gpio1`, including explicit symbolic flags. Removing the final entry emits a property
deletion so an inherited assignment cannot return silently. Read-only PWM/QSPI routes
contribute evidence, and known pin conflicts, reservations and shared controller instances
are checked. Package pads, solder bridges, electrical suitability and external wiring
remain unknown. Other pin encodings and opaque source remain available in the source editor.
D0–D8 are implemented within these scoped limits; D8 verification is recorded in its
handoff. D9 module recipes and visual placement is the next stage.

Acknowledged drafts are retained under `.features/devicetree/drafts/` in the data directory.
**Find recoverable drafts** compares retained text with current files before an explicit restore;
it never replays firmware writes. Keep or export pending text if recovery storage fails.
Linux Save uses guarded atomic exchange, retaining original/candidate evidence under
`.features/devicetree/publications/` and a hidden original beside the overlay. Conflicts or
interrupted publication require comparison; files may already contain the candidate after
an uncertain exchange. Known incomplete syntax stays recoverable but cannot be saved.
Missing files, links, changed directories and unsupported permissions fail closed.

Existing snapshot layouts, IDs, exports and legacy memory endpoints are preserved.
`--data-dir` remains the memory-history directory; environment observations use
`.features/projects/environments/` below it. Project profiles and build attempts use
`.features/projects/creator/`. Other feature storage is likewise namespaced
under `.features/`. No history migration is performed on launch.

Later stages cover region budgets, optimization recommendations, runtime stack/heap/arena
measurements and TinyML experiment metrics. See the [roadmap](docs/roadmap.md) for scope
and acceptance criteria; these later capabilities are not included in version 0.2.
