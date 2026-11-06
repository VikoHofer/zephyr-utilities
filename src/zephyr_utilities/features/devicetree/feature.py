"""Source-preserving overlay editor with local modules and target-specific pins."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register

FEATURE = FeatureDefinition(
    id='devicetree',
    title='Device Tree Editor',
    description='Place local modules, configure peripherals, PWM consumers and supported pins, inspect resource conflicts, and review removals and overlay Save. Configure remains a separate action.',
    entry_path="/#/devicetree/editor",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
