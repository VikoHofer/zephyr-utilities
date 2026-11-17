# ADR 0028: TinyML drawing and bounded UART inference

Status: accepted, 2026-11-15.

## Context

The local MNIST demo already provides a drawing tool, image normalization and the
`mnist-gray8-v1` UART upload protocol. The user requested the same interaction inside
TinyML: draw a digit, send it to the selected board, and display prediction and time.
This explicitly expands the earlier demo-creation scope without restoring training,
annotation, dataset collection, automatic flashing or arbitrary serial commands.
The user's follow-up requires the viewer to operate independently: select a port,
open it, and send/receive when the connected device supports the protocol. Firmware
provisioning and the Projects target/SDK chooser must not gate the viewer.

## Decision

TinyML owns its drawing frontend, image preparation and UART service. It registers
`GET /api/tinyml/ports`, `POST /api/tinyml/prepare`, `POST /api/tinyml/open`,
`POST /api/tinyml/close` and `POST /api/tinyml/infer` through
the existing authenticated router. Shared jobs own asynchronous port operations, inference and
cancellation. Projects retains all application, configuration and build ownership.
No server, launcher, environment, scheduler or persistent record format is added.

The Apache-2.0 image/protocol helper is statically adapted from the existing demo
tools, with source attribution and the license bundled in the installed package.
Workspace Python scripts are never imported or executed. Pillow and pyserial become
runtime dependencies in the same environment as `zephyr-utils`.

The browser maintains a 336×336 grayscale drawing. Preparation crops the ink,
scales it into 20×20 with LANCZOS, and mass-centers it in a 28×28 field, preserving
the existing tool's algorithm. The browser displays and verifies these exact bytes.
Blank inputs, wrong dimensions and malformed base64 are rejected before port access.

Port discovery is read-only and bounded. The browser selects an opaque identity for
an advertised canonical character device, never a caller-supplied file or URL.
Each explicit Send captures workspace, connection identity, port identity, normalized
image hash and CRC. Queued tasks retain those inputs after workspace changes.
The actual opened descriptor is checked against the advertised device before any
shell command. Resource keys and serial exclusivity prevent competing transactions.

Explicit Open creates one application-wide 115200/8N1 connection with DTR/RTS
deasserted. It sends no commands and requires no device response. Discovery,
navigation and workspace changes never implicitly open or close it. Open and Send
require no demo, board, SDK or firmware metadata. There is no `ai info` preflight.
The open connection remains usable if a later discovery cannot list its device.

Send performs the existing shell synchronization, then one Begin command, 28 exact row commands and one CRC-checked Infer
command. No raw command API exists. A result requires one structured result record,
the expected CRC, a valid ten-class distribution, matching argmax and an unsigned
Invoke duration. Partial writes, responses, deadlines and cancellation are bounded;
blocking driver drains are avoided. Successful sends keep the connection open for
the next drawing. Transfer failure or running cancellation closes it; cancelling a
queued send before any I/O can leave it open. Explicit Close cancels a running
transfer and releases the port. Application shutdown also releases idle connections.

Drawings may change while a job runs, but results retain the submitted image and
connection identity. Leaving a view aborts browser reads/timers, not a server job or
the open connection. Reopening refreshes the actual connection and retained shared
jobs. The global connection remains visible across workspace changes, while run
results stay with their captured workspace. A restored job without captured browser
pixels shows its image hash instead of labeling the current drawing as its input.
No drawings, results or telemetry files are written to feature storage. Retired TinyML
data stays inert; shared job retention and bounded browser captures hold live evidence.

## Consequences and verification

The viewer attempts communication when the user chooses Send. Missing responses,
unsupported commands or malformed results produce ordinary communication errors;
they do not introduce a provisioning or target-selection step. Port availability and
USB descriptions do not establish firmware, model, SDK or build identity. Reported
Invoke time excludes preparation and UART transfer. Zero or
simulated timing is not a physical-board performance result or an accuracy claim.

Tests use synthetic images and firmware responses, including real pyserial transfers
over pseudo-terminals, cancellation, lock release and reconnect. Browser tests use
an explicitly labeled synthetic UART peer. These checks do not constitute physical
board validation. See the [MNIST guide](../tinyml-mnist-demo.md).
