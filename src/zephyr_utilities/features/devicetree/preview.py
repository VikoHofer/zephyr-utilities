"""Explicit development/test injection; the built-in editor stays planned until D4."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register


PREVIEW_FEATURE = FeatureDefinition(
    id="devicetree",
    title="Device Tree Editor preview",
    description="Synchronized visual and source drafts with undo. File saving is planned.",
    entry_path="/#/devicetree/editor",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
