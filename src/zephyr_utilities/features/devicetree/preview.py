"""Explicit D1 development/test injection; the built-in editor stays planned."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register


PREVIEW_FEATURE = FeatureDefinition(
    id="devicetree",
    title="Device Tree Editor preview",
    description="Read-only project context and overlay preview. Visual editing is planned.",
    entry_path="/#/devicetree/editor",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
