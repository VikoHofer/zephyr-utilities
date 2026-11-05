"""Shared services supplied to feature registration functions."""

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from .builds import BuildInspectionService
from .jobs import JobManager
from .project_context import ProjectContextProvider
from .project_validation import ProjectValidationProvider
from .workspace import WorkspaceService


@dataclass
class ApplicationContext:
    workspace: WorkspaceService
    jobs: JobManager
    data_dir: Path
    services: dict[str, Any] = field(default_factory=dict)
    builds: BuildInspectionService = field(default_factory=BuildInspectionService)
    project_context: ProjectContextProvider | None = None
    project_validation: ProjectValidationProvider | None = None

    def feature_data(self, feature_id):
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", feature_id):
            raise ValueError("Invalid feature identity.")
        # --data-dir continues to point directly at existing memory snapshots.
        if feature_id == "memory":
            return self.data_dir
        return self.data_dir / ".features" / feature_id
