"""Compatible explicit development/test injection for the delivered limited editor."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register


PREVIEW_FEATURE = FeatureDefinition(
    id="devicetree",
    title="Device Tree Editor preview",
    description="Local modules, PWM consumers, resource diagnostics, reviewed removals and overlay Save, recovery and supported pin assignments.",
    entry_path="/#/devicetree/editor",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
