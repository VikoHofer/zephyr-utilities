# Custom boards

Project Creator's **Custom boards** view manages local board roots and creates a
reviewed starting point for a new board using the selected Zephyr checkout. A board
root contains a `boards/` directory; it is separate from an application's overlay.
The same registered board can be used by several applications.

## Register and manage existing boards

1. Analyze the workspace so its Zephyr checkout and supported SoCs are known.
2. Open **Custom boards**, choose registration, and enter a display name and the
   directory containing `boards/`.
3. Review the discovered definitions, diagnostics and affected configurations, then
   apply the registration.
4. Analyze the environment again. Registered roots are included automatically in
   subsequent scans and build rechecks. Select an exact discovered target in
   **Create application** or **Add configuration**.

Registration stores Utilities metadata and does not copy or execute the board files.
Registrations are scoped to the selected workspace and survive application restarts.
Managed roots must be inside that workspace, outside Zephyr, dependencies, SDKs and
tool directories. External roots can still be supplied through Environment's manual
discovery paths, but do not participate in this registration workflow.
The list checks directory identity, so a folder deleted and recreated at the same path
does not silently become the earlier registration. Rename or reconnect a registration
after a move, review the changed location, and analyze again.

Removing a registration leaves its board source files and earlier build evidence on
disk. The review identifies affected saved configurations. Other discovery mechanisms,
such as an active Zephyr module or an explicit scan path, can still provide that root;
removing a registration does not remove those independent declarations. Source removal
remains an external filesystem operation. Missing or replaced registrations remain
visible for reconnection or removal instead of appearing as usable boards.

Registration changes invalidate the saved environment selection. After rescanning, use
**Edit selected** to refresh existing application configurations where required. A
running operation retains its captured environment; board registration mutations are
blocked while related workspace work is active.

## Create a custom board

The guide offers a reference-board path and a SoC-first path. The latter filters local
reference ports for the selected supported SoC. Both use an explicit reference port:
SoC metadata alone does not define a PCB's clock sources, memory arrangement, console,
pin wiring or programmer.

Choose the reference, a new board identifier, display name, vendor and a new destination
inside the workspace. The destination becomes an independent board root containing
`boards/<vendor>/<board-name>/`. Review the exact generated text files before applying.
Board metadata, local file references and board Kconfig symbols are renamed; SoC
identities and inherited hardware settings are retained. Source hashes and license
evidence record where the starting point came from.

The reference adapter accepts bounded, supported hardware-model-v2 layouts and rejects
definitions it cannot safely adapt. Unsupported references report a reason. Discovery
and preview do not execute their CMake or scripts. Existing destinations, changed source
inputs and redirected paths block publication; no existing board is overwritten.

The initial copy adapter supports a single board definition in one self-contained
directory of text sources. It retains all reference SoCs, variants and declared
revisions; choosing one SoC does not strip other targets. Board extensions across
directories, custom revision scripts, parent-directory dependencies and binary runtime
files require manual porting. Documentation/images and old build directories are
excluded. Apache-2.0 attribution must be established by file headers or supported
LICENSE/REUSE metadata from the selected checkout or a declared board root. Applicable
license text, original annotations and exact renamed-file annotations are retained so
a generated board can itself become a later reference. Other licenses/layouts require
an additional adapter; existing-board registration does not impose these copy rules.

The generated port is a starting point for the reference hardware. Review and change
the inherited DTS, pinctrl, Kconfig and runner settings for the actual PCB before using
it on hardware. The creator does not infer pin assignments or implement a new SoC port.
Visual pin and peripheral editing remains Device Tree Editor work.

After creation, analyze the environment, select the new target and create a minimal
application. Every new application gets its own board-matched default overlay. Run
**Configure** or **Build** explicitly to validate the port with the installed SDK. Use
the sample compatibility check before using Blinky or Button: inherited or missing
aliases must match the actual board. A successful compile is not hardware validation.

## Persistence and reviewed writes

Custom-board registration uses separate versioned feature storage; it does not rewrite
earlier environment observations or application attempt records. Preview plans are
session-bound, expire, and are consumed on apply. Registry changes, source changes or
directory replacements invalidate a pending review.

Board publication uses the same guarded staging and atomic no-replacement operation as
application creation. If source publication succeeds but registration storage fails,
the error identifies the created directory so it can be registered explicitly; source
files are retained. See [ADR 0018](decisions/0018-custom-board-workflow.md).

The authenticated routes are `GET /api/projects/custom-boards`,
`POST /api/projects/custom-boards/preview` and `POST /api/projects/custom-boards/apply`.
Preview takes the current workspace and `operation` (`register`, `update`, `remove` or
`create`); apply takes the workspace and returned `plan_id`. Registrations are stored
under `context.feature_data("projects") / "custom-boards"`, one schema-version-1 JSON
record per workspace with a compare-and-swap revision and at most 32 roots. Environment
observations include an optional `custom_board_registry` fingerprint. Legacy observations
remain readable; unknown registry schemas are rejected without replacement.

## Verification

The complete checks passed 555 Python tests and 257 frontend tests, including legacy
creation behavior and the new registration, scaffold and browser-state regressions.
Installed-wheel assets were verified. Real SDK builds compiled generated custom
nRF52840 and ESP32-C3 targets in an isolated workspace; captured inputs confirmed each
custom DTS and board-matched application overlay. Move/reconnect and remove/re-register
checks preserved sources and updated discovery. Browser checks covered generation,
registration, reconnection, removal review, analysis, application handoff, leaving and
reopening the feature, and the 600-pixel layout. No physical hardware was exercised.
