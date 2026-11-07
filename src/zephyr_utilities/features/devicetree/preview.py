"""Compatible explicit development/test injection for the delivered limited editor."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register


PREVIEW_FEATURE = FeatureDefinition(
    id="devicetree",
    title="Device Tree Editor preview",
    description="Automatic chip and pin inspection with overlay source editing, reviewed Save and draft recovery.",
    entry_path="/#/devicetree/editor",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
