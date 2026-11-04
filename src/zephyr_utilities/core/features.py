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
class NavigationItem:
    label: str
    view: str
    match_views: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.label, str) or not self.label.strip() or len(self.label) > 80:
            raise ValueError("Navigation labels must contain between 1 and 80 characters.")
        if not isinstance(self.match_views, tuple):
            raise ValueError("Navigation matches must be a tuple of local views.")
        for view in (self.view, *self.match_views):
            if not isinstance(view, str) or not re.fullmatch(r"[a-z0-9_-]+(?:/[a-z0-9_-]+)*", view):
                raise ValueError("Navigation views must be local relative paths.")

    def descriptor(self):
        return {"label": self.label, "view": self.view,
                "match_views": list(dict.fromkeys((self.view, *self.match_views)))}


@dataclass(frozen=True)
class FeatureDefinition:
    id: str
    title: str
    description: str
    status: str = "available"
    entry_path: str = ""
    frontend: FrontendDefinition | None = None
    register: Callable | None = None
    navigation: tuple[NavigationItem, ...] = ()
    navigation_group: str = "tools"

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", self.id):
            raise ValueError("Feature IDs must contain lowercase letters, numbers, hyphens or underscores.")
        if self.id in RESERVED_FEATURE_IDS:
            raise ValueError(f"Feature ID '{self.id}' is reserved by the application shell.")
        if self.status not in {"available", "planned"}:
            raise ValueError("Feature status must be available or planned.")
        if self.status == "planned" and (self.register is not None or self.frontend is not None):
            raise ValueError("Planned features cannot register executable components.")
        if self.navigation_group not in {"tools", "advanced"}:
            raise ValueError("Feature navigation groups must be tools or advanced.")
        if not isinstance(self.navigation, tuple) or any(not isinstance(item, NavigationItem) for item in self.navigation):
            raise ValueError("Feature navigation must be a tuple of NavigationItem entries.")
        if self.status == "planned" and self.navigation:
            raise ValueError("Planned features cannot advertise implemented navigation views.")
        views = [item.view for item in self.navigation]
        if len(views) != len(set(views)):
            raise ValueError("Feature navigation views must be unique.")
        matches = [view for item in self.navigation for view in item.descriptor()["match_views"]]
        if len(matches) != len(set(matches)):
            raise ValueError("Feature navigation matches must identify one entry.")

    def descriptor(self):
        return {"id": self.id, "title": self.title, "description": self.description,
                "status": self.status, "entry_path": self.entry_path or f"/#/{self.id}",
                "frontend": self.frontend.descriptor(self.id) if self.frontend else None,
                "navigation": [item.descriptor() for item in self.navigation],
                "navigation_group": self.navigation_group}


def validate_features(features):
    result = tuple(features)
    identifiers = [feature.id for feature in result]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Feature IDs must be unique.")
    return result
