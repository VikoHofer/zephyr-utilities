# ADR 0014: Shared theme and visible action feedback

## Context

Feature-specific green colors drifted from Zephyr's branding. Several actions added
results below long pages, so a successful click appeared to have no effect.

## Decision

The shell defines shared `--app-*` tokens using the official Zephyr purple/blue palette
and neutral grays. Features inherit these tokens and reserve semantic colors for status.
All assets remain bundled and offline.

The public browser context adds `reveal(element, options)` for explicitly requested
destinations, scoped to the mounted feature. It makes the result a focus destination
and uses immediate nearest alignment, preserving a destination already in view. Route
navigation explicitly requests start alignment for its heading. Hidden and detached
targets are ignored. Passive updates never invoke it.

Ordinary load, selection, edit, Undo/Redo, Sync, refresh and Save actions preserve the
user's scroll position and keyboard focus. They show feedback beside the trigger and,
when needed, through the fixed shared notice. Completion does not navigate to a status
paragraph or editor section. Results elsewhere have an explicit Show/Open control.

Project creation/build command reviews use focused dialogs. Deliberate detail and result
navigation can reveal its destination, while progress and polling preserve the current view.
The [UI guidelines](../ui-guidelines.md) govern future features and the feature guide
includes scrolled/narrow-screen verification.

## Consequences

Features share a small interaction interface without depending on each other's UI code.
They remain responsible for request/workspace/lifetime guards and meaningful result
placement. No backend, job execution or persistence contract changes are needed.
