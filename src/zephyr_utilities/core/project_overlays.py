"""Public handoff for reviewed inclusion of a newly saved application overlay."""

from typing import Protocol


class ProjectOverlayProvider(Protocol):
    def review(self, selection: dict, relative_path: str, *, creating: bool = False) -> dict | None:
        """Review a new overlay or a pending creation; never write application sources."""
        ...

    def register(self, registration: dict, workspace: str, path: str, sha256: str) -> dict:
        """Include verified saved source through an idempotent configuration update."""
        ...
