# ADR 0024: Automatic chip discovery and pin overlay editing

Status: partially implemented, 2026-11-07. Automatic discovery, the chip view,
old-workflow retirement and existing-overlay GPIO/UART/I2C/SPI activation for the
STM32F446 R(C/E)Tx and nRF52840 QIAA profiles are delivered. The new-overlay extension
and its recovery schema/public Projects write contract are implemented under
[ADR 0025](0025-new-overlay-save-and-project-inclusion.md). Remaining silicon profiles
and the five-profile acceptance matrix are still open.
See the [redesign plan](../device-tree-editor-pin-workspace-plan.md).

## Context

The requested editor opens directly into a chip/pin view and changes application
overlays through pin assignments. The former manual discovery steps and sensor/module
workflow do not satisfy that interaction. ADR 0023 permits bounded preprocessing only
after an explicit Load action. Current hardware discovery also depends on an existing
selected overlay and holds editor locks while external tools run.

## Decision and remaining extensions

Selecting/opening a project starts bounded read-only chip and assignment discovery
automatically. Preserve ADR 0023's root validation, source/tool fingerprints, process
limits and selected-tool boundaries. Do not execute application/board CMake, Configure,
builds, downloads or firmware writes as part of loading. Use the shared job manager:
capture immutable inputs under lock, run outside the locks and revalidate before
publishing results. UI disposal does not itself cancel a shared server job.

Keep chip/package capabilities separate from current draft assignments. Feature-owned
family adapters read version-matched pin data, decode assignments and validate/encode
small source changes. The server authorizes each pin mutation against its exact target,
capability evidence and current draft guards. A graphical placement is presentation,
not hardware authority; a logical perimeter drawing is not physical package geometry.

The canonical overlay draft remains the writable model. Complete peripheral routes
are atomic source commands with one Undo entry. Board/SoC/HAL files remain untouched.
GPIO initially means a named application reference, with runtime direction controlled
by application code. No sensor/LED recipe is used to manufacture a GPIO consumer.

A missing overlay must not prevent chip display. Stage an unsaved exact-target overlay
and extend the publisher with guarded new-file creation, recovery and no-replacement
semantics. If explicit fragment selection needs to include that file, use a narrow
Projects-owned public operation with revision checks; never access Projects storage
directly. The detailed interface and any persistence schema migration must be specified
and tested before this part is implemented. A saved file and its configuration inclusion
are separate outcomes, and a registration failure must be recoverable without deleting
the file or claiming the operation fully succeeded.
The original v1 recovery format assumes an existing baseline; the extension must record
absent-destination/create identity while retaining v1 read compatibility. The Projects
operation needs expected configuration/project revisions, idempotent retry and an outcome
receipt. Recapture the editor context after success because registration changes its identity.
ADR 0025 now defines the implemented schema-2 draft and creation-journal formats,
schema-1 compatibility, public inclusion provider and separate durable receipts.

Remove obsolete connection/module runtime services, routes, commands and assets through
a deliberate compatibility change. Preserve existing source, recoverable source history,
Inspector aliases and inert legacy presentation records. Do not reinterpret module
layout records as pin positions or silently migrate unknown stored schemas.

## Consequences and acceptance

Normal project opening needs no extraction buttons and never depends on the presence
of local controller declarations. Unsupported/ambiguous input remains visible with a
specific reason. Capability caching and asynchronous discovery require guards against
stale or cross-project results. Missing-file publication requires additional recovery
tests; a hidden creation step during loading is not allowed.

This proposal changes the discovery trigger, adds a potential public Projects write
handoff and retires editor endpoints. It does not introduce another server, frontend
toolchain, runtime network service or generic executable plugin system. Acceptance is
the concrete five-profile pin workflow and verification matrix in the redesign plan.
