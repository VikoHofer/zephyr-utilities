# ADR 0014: Shared theme and visible action feedback

## Context

Feature-specific green colors drifted from Zephyr's branding. Several actions added
results below long pages, so a successful click appeared to have no effect.

## Decision

The shell defines shared `--app-*` tokens using the official Zephyr purple/blue palette
and neutral grays. Features inherit these tokens and reserve semantic colors for status.
All assets remain bundled and offline.

The public browser context adds `reveal(element, options)` for explicit action results,
scoped to the mounted feature. It makes the result a focus destination and scrolls once,
without animation. Hidden and detached targets are ignored. Passive updates never invoke
it. Shared notices remain in the viewport; route changes reveal their destination.

Project creation/build command reviews use focused dialogs. Other detail and progress
actions reveal or acknowledge their outcome, while polling preserves the current view.
The [UI guidelines](../ui-guidelines.md) govern future features and the feature guide
includes scrolled/narrow-screen verification.

## Consequences

Features share a small interaction interface without depending on each other's UI code.
They remain responsible for request/workspace/lifetime guards and meaningful result
placement. No backend, job execution or persistence contract changes are needed.
