"""Source-preserving overlay editor with target-specific pin support; modules are planned."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register

FEATURE = FeatureDefinition(
    id='devicetree',
    title='Device Tree Editor',
    description='Configure local bus devices, GPIO LEDs/buttons, binding properties and supported pin assignments in overlay drafts, then review Save and Configure. Hardware modules are planned.',
    entry_path="/#/devicetree/editor",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
