"""Small explicit feature descriptors; feature imports belong to the composition root."""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable


# Shared shell paths, asset namespaces and original Memory Explorer hash aliases.
RESERVED_FEATURE_IDS = frozenset({
    "home", "shell", "shared", "core", "api", "assets", "app-main",
    "overview", "explorer", "compare", "snapshots",
})


@dataclass(frozen=True)
class FrontendDefinition:
    asset_dir: Path
    module: str = "index.js"
    template: str = "view.html"
    stylesheet: str = "style.css"

    def descriptor(self, feature_id):
        return {name: f"/assets/{feature_id}/{getattr(self, name)}"
                for name in ("module", "template", "stylesheet")}


@dataclass(frozen=True)
class FeatureDefinition:
    id: str
    title: str
    description: str
    status: str = "available"
    entry_path: str = ""
    frontend: FrontendDefinition | None = None
    register: Callable | None = None

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", self.id):
            raise ValueError("Feature IDs must contain lowercase letters, numbers, hyphens or underscores.")
        if self.id in RESERVED_FEATURE_IDS:
            raise ValueError(f"Feature ID '{self.id}' is reserved by the application shell.")
        if self.status not in {"available", "planned"}:
            raise ValueError("Feature status must be available or planned.")
        if self.status == "planned" and (self.register is not None or self.frontend is not None):
            raise ValueError("Planned features cannot register executable components.")

    def descriptor(self):
        return {"id": self.id, "title": self.title, "description": self.description,
                "status": self.status, "entry_path": self.entry_path or f"/#/{self.id}",
                "frontend": self.frontend.descriptor(self.id) if self.frontend else None}


def validate_features(features):
    result = tuple(features)
    identifiers = [feature.id for feature in result]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Feature IDs must be unique.")
    return result
