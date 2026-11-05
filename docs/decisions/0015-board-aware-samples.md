# ADR 0015: Board-aware samples and explicit compatibility checks

## Context

A successful local sample copy does not establish that its selected board provides the
required hardware. GPIO Blinky failed to compile for ESP32-C3-DevKitC because its
onboard LED uses an addressable RGB protocol and the board has no GPIO `led0` alias.
Board catalogues alone do not resolve Device Tree includes, qualifiers and overlays.

## Decision

Keep catalogue reads and application previews non-executing. Add an explicit **Check
compatibility** job that configures a temporary copy with the selected environment,
then inspects its resolved Device Tree and configuration. It uses the shared job manager,
supports cancellation, removes temporary source/output directories, and never flashes.

Report `compatible`, `incompatible` or `unknown` for the sample's hardware prerequisites.
Unknown stays a warning; configuration does not prove successful compilation or hardware
operation. Bind checks to workspace, environment, exact board target, SDK, original
sample fingerprint and the adapted source fingerprint. Server-session check tokens
expire after 30 minutes. Known incompatible selections cannot be previewed while that
matching evidence is retained. Standard Configure/Build also checks current sample
requirements after configuration and stops before compilation on a known mismatch.

Cache reuse also requires fresh configuration-input evidence from the selected Zephyr,
hardware roots and active modules. Bounded stable scans cover DTS, bindings, Kconfig,
CMake and transitive headers, including additions and deletions. Small metadata is
hashed; headers and large inputs use filesystem identities and nanosecond change times.
This guards ordinary local edits, not arbitrary dynamic CMake reads outside those source
trees. Changes during a check or after a reviewed preview require another check.
During ordinary builds, edited sample source or an original main file that is no longer
compiled by the application produces unknown instead of enforcing obsolete requirements.

Blinky has an explicit, versioned GPIO8 RGB adaptation for the reviewed official
ESP32-C3-DevKitC and ESP32-C3-DevKitM targets. Review all generated files before creation.
The adapter rejects unsupported board/input changes rather than guessing a pin mapping.
Generated applications are self-contained, preserve upstream source/licensing evidence,
and reject building the RGB adaptation for another target.

Adapted sample provenance uses schema 2 and records original hashes, generated hashes,
adapter version, exact target and inspected input evidence. Existing unadapted schema 1
copies remain readable and are not rewritten. Project records retain their existing
schema; their feature-owned sample payload is versioned independently.

## Consequences

The UI can explain the exact missing requirement instead of showing compiler macro errors.
Users explicitly opt into running local CMake during compatibility checks. New peripheral
adaptations require reviewed evidence and tests; this does not imply universal wiring
support or hardware validation. All execution and storage remain in the existing server.
