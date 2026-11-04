# ADR 0001: One application with explicitly registered features

Status: accepted

## Context

Memory Explorer is working, and Device Tree Editor, Project Creator and TinyML analysis
will require substantial independent development. Users should start the software once
from their existing Zephyr virtual environment. The first implementation coupled its
HTTP dispatch and browser shell directly to memory analysis.

## Decision

Keep one Python process and browser shell. Compose explicitly registered feature
definitions in `app.py`; each available feature owns its services, namespaced HTTP routes
and browser module. A small core owns transport/access checks, the workspace, jobs and
feature contracts. Core code does not import feature implementations.

The browser shell owns global routing and loads feature assets. Features use a
mount/navigate/dispose lifecycle and retain only scoped UI state. Planned features appear
as planned catalog entries without pretending to provide working tools.

Use a checked-in registry instead of automatic discovery of arbitrary workspace modules.
Dependencies on another feature's private implementation are not the extension mechanism.

## Consequences

- Features can be developed and tested separately while sharing one launch and environment.
- Cross-cutting behavior is implemented once and verified at the shared boundary.
- Feature registration changes are explicit and reviewable.
- UI modules must clean up their listeners, timers and observers when unmounted.
- Extracting shared abstractions still requires a concrete use; the platform does not
  predefine every editor, project or experiment concept.

Alternatives considered were separate applications per tool and continued growth of a
single memory-specific dispatcher. Both make shared workflows and independent contribution
harder as the feature set grows.
