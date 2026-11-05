"""Read-only project selection contract; implementations remain feature-owned."""

from typing import Protocol


class ProjectContextProvider(Protocol):
    """Return detached observations without creating build plans or changing files."""

    def catalog(self, workspace: str) -> dict:
        """List registered applications and their explicit saved configurations."""
        ...

    def capture(self, workspace: str, project_id: str, configuration_id: str | None) -> dict:
        """Observe an original/named selection, retaining unavailable context."""
        ...

    def revalidate(self, selection: dict) -> dict:
        """Return equivalent current context or raise a conflict/unavailable error."""
        ...
