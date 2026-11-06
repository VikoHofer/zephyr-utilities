"""Source-preserving overlay editor with local modules and target-specific pins."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register

FEATURE = FeatureDefinition(
    id='devicetree',
    title='Device Tree Editor',
    description='Discover your board hardware, connect supported sensors and LEDs, and see the overlay take shape. Review and Save your changes; advanced source and peripheral tools remain available.',
    entry_path="/#/devicetree/editor",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
