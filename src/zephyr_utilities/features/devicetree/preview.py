"""Compatible explicit development/test injection for the delivered limited editor."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register


PREVIEW_FEATURE = FeatureDefinition(
    id="devicetree",
    title="Device Tree Editor preview",
    description="Local module placement, overlay drafts, reviewed single-file Save, recovery and supported pin assignments.",
    entry_path="/#/devicetree/editor",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
