"""Public, reviewed configuration execution supplied by Projects."""

from typing import Protocol


class ProjectValidationProvider(Protocol):
    """Configure a captured saved-source candidate; never compile implicitly.

    Review returns a detached plan. Submission pins the selected workspace and
    uses shared jobs. Results retain candidate/input identity and disclose partial
    dependency coverage. Only review/submission require the current workspace.
    """

    def review(self, selection: dict, candidate: dict) -> dict:
        """Review exact saved path/text/sha256 through the selected tool adapter."""
        ...

    def submit(self, plan_id: str) -> dict:
        """Submit one reviewed configuration; duplicates return its existing job."""
        ...

    def result(self, plan_id: str) -> dict:
        """Observe captured progress, diagnostics and current freshness."""
        ...

    def cancel(self, plan_id: str) -> dict:
        """Request cancellation of this plan's captured shared job."""
        ...

    def handoff(self, plan_id: str) -> dict:
        """Return an exact Projects selection without starting compilation."""
        ...
