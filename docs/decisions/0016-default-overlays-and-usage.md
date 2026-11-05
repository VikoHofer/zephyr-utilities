# ADR 0016: Default board overlays and configuration evidence

## Context

Copied samples include overlays for several boards. A file appearing in the project
does not mean that Zephyr selected it for the current target. New applications also
need an obvious place for user hardware changes, including applications using custom
boards discovered through additional board roots.

## Decision

Every new Minimal C, Minimal C++ and local-sample application includes a comment-only
default overlay named for its exact creation target under `overlays/`. Its name retains
the board, qualifiers and any requested revision. No pins or peripheral wiring are
invented. This uses the same mechanism for built-in and discovered custom boards.

Keep the generated file outside Zephyr's automatic `boards/` search. A generated CMake
block adds it to `EXTRA_DTC_OVERLAY_FILE` for the exact creation target. This preserves
the sample's automatic overlays and application fallback. Appended user overlays retain
their order; an explicit replacement on a fresh configuration disables the generated
default. A cached `ZEPHYR_UTILS_DEFAULT_OVERLAY` option preserves the initial choice on
reconfiguration. Use a fresh build directory when changing selection, or set the option
explicitly for standalone builds. Another target does not inherit the original target's
default overlay.

The complete overlay and CMake changes are reviewed before publication. Minimal templates
use version 2 while retaining version-1 validation. Local samples use provenance schema 3,
preserve the previous CMake file under `upstream/original/`, and include the generated
overlay and changed file hashes. Earlier provenance schemas remain readable and replayable.
Project records gain optional versioned `default_overlay` metadata; no existing project
files or records are migrated merely by opening the application.

Show overlay usage in creation/build reviews and alongside the selected saved build
configuration. Before configuration, distinguish expected selection from confirmed use.
After configuration, use captured Device Tree inputs, not a filename guess, to identify
the overlays actually used. Keep the base board DTS separate from application overlays.
Mark other copied overlays as unused only when captured dependencies also cover
transitive includes. Otherwise explain which files are not selected directly while
leaving transitive use unknown. Explain files retained for other boards or as upstream
evidence. Missing or unsupported evidence remains unknown.

Saved attempt evidence describes that attempt and its target. It must not be presented
as proof of current inputs after edits or as evidence for a different configuration.
Opening views does not execute CMake or change firmware files.

## Consequences

Users can find their default editing file without mistaking every copied overlay for
an active input. Sample behavior and explicit fragment selection remain reviewable.
Adding a new template must preserve this default-overlay contract. Default overlays are
created with new applications; saving another build configuration remains metadata-only.
Custom board creation and visual Device Tree editing remain separate planned features.
