# Adding a feature

A feature is a module inside the existing Zephyr Utilities process and browser shell.
It does not need a new launcher, port, virtual environment or frontend build system.
Read [architecture](architecture.md) before changing a shared contract.

## 1. Define one useful slice

Write a short acceptance statement: who performs which action, which input it uses,
what becomes visible or saved, and how success is checked. Separate initial functionality
from later ideas. The [feature proposal](templates/feature-proposal.md) includes useful
prompts for larger work.

Choose a stable lowercase identifier such as `telemetry`. Existing planned areas already
have IDs: `devicetree`, `projects`, and `tinyml`; the implemented advanced reader uses
`inspector`. Extend the appropriate module rather than creating
competing names for the same tool.

## 2. Generate the starting files

From the repository root:

```sh
python scripts/new_feature.py telemetry --title "Telemetry"
```

The generator creates a planned manifest, a feature README and starter `web/` files. It
does not alter the registry or make the tool available. Inspect the generated files
before implementing behavior. Keep backend services and `web/` assets together in
`src/zephyr_utilities/features/telemetry/`, and add behavior tests under `tests/`.

## 3. Implement the backend boundary

The public registration callback is `register(router, context)`. Put domain logic in
feature-owned service/model modules and keep route handlers thin. Register paths under
`/api/telemetry/...` using `router.add(method, path, handler)`.

Request handlers receive the shared `Request`. Use `request.query_value(...)`,
`request.params`, and the parsed `request.body`; validate the feature-specific fields.
Return JSON-compatible data or a shared `Response` for downloads/status codes. Reuse
shared authentication and error handling rather than duplicating it.

For expensive work, submit a task to `context.jobs` with the feature ID and a resource
key when two jobs must not overlap. Capture validated input paths before submitting.
The task accepts `(emit, cancel)`; report progress, honor cancellation and return a result
mapping. Raise `concurrent.futures.CancelledError` after releasing resources when the
cancellation event is set. Keep results that must survive restart in
`context.feature_data("telemetry")`; create the directory when writing to it.

Use `context.workspace` as the shared selection. Do not alter it implicitly while
rendering a page, or read its current value halfway through a job to retarget work.
Do not import another feature's private classes. If a shared capability is needed,
agree on a small public core interface and test both consumers.

## 4. Implement the browser lifecycle

An available frontend declares `index.js`, `view.html`, and `style.css` through
`FrontendDefinition`. The shell loads these assets and calls:

```js
export function mount(root, context) {
  const controller = new AbortController();
  const button = root.querySelector("[data-action='refresh']");
  button?.addEventListener("click", async () => {
    const result = await context.api("/api/telemetry/summary");
    if (!controller.signal.aborted) {
      root.querySelector("[data-output]").textContent = result.summary;
    }
  }, {signal: controller.signal});

  return {
    navigate(view) { /* select this feature's local view */ },
    dispose() { controller.abort(); },
  };
}
```

This illustrates the lifecycle only; implement error/loading handling for real actions.
Scope selectors and CSS to the feature root. Use `context.navigate("telemetry/view")`
for navigation, `context.notify` for shared notices, and `context.state` for state that
should survive leaving and re-entering the feature. Clear timers and disconnect observers
on dispose. Server jobs continue while a feature is not mounted.

Plan the visible response to each action before adding its button. Keep small results
beside their trigger; put reviewed writes in a focused dialog with the next action
visible. For a result in another part of the view, call `context.reveal(resultElement)`
once from the explicit action after confirming the request still belongs to this view
and workspace. Loading, empty and error outcomes also need visible feedback. Background
polling must not scroll, focus, open dialogs or switch the user's view.
Use shared `--app-*` design tokens and the [UI guidelines](ui-guidelines.md).

When extending Project Creator templates, retain its board-named default overlay for
built-in and discovered custom boards. Preserve existing sample overlay behavior and
explicit fragment replacement. Present overlay usage as expected until configuration
provides evidence; keep overlays copied for other boards distinct from selected inputs.
See [the overlay contract](decisions/0016-default-overlays-and-usage.md).

Render imported text through `textContent` or equivalent safe DOM construction. Bundle
all runtime assets; do not add CDN scripts or require another web server. Preserve keyboard
access, useful empty states, actionable errors and clear progress/cancellation feedback.

## 5. Register it explicitly

Add the feature definition to the built-in registry in `features/__init__.py`. Give it
a stable ID, English title/description and entry path. Set it available only when the
registered routes and frontend implement the advertised workflow. Planned definitions
use no frontend or registration callback and remain clearly labeled in the Home catalog.

For a completed feature, `feature.py` follows this shape:

```python
from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register

FEATURE = FeatureDefinition(
    id="telemetry",
    title="Telemetry",
    description="Describe the implemented user workflow.",
    entry_path="/#/telemetry/overview",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
```

Import that definition in `features/__init__.py` and include it in `builtin_features()`.
The shared app accepts an injected feature collection for tests. Use that to prove a
feature can register routes and assets without adding a new dispatch branch to the HTTP
server. Do not add runtime scanning of arbitrary workspace Python files.

## 6. Validate and document

```sh
python scripts/check.py --quick
python scripts/check.py
```

Cover domain behavior, malformed input, cancellation where relevant, same-server route
registration, persistent-format compatibility, and frontend lifecycle/navigation. Verify
the new assets are included in the installed package with
`python scripts/verify_package.py`. Manually switch from Home to the
feature, away, and back; ensure the UI restores correctly and does not duplicate listeners
or abandon job status.
Test from the bottom of a long page and at a 600-pixel viewport: a button's result,
progress and next action must be discoverable without guessing where to scroll.

Update the README's delivered scope, this feature's scope/documentation and the roadmap.
Add an ADR only when changing a shared architecture or persistence decision. A generated
directory, mockup or route returning a placeholder is not evidence of a completed tool.
