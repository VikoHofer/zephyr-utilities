"""Source-preserving overlay editor with local modules and target-specific pins."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register

FEATURE = FeatureDefinition(
    id='devicetree',
    title='Device Tree Editor',
    description='Place local hardware modules and configure bus devices, GPIO, binding properties and supported pin assignments in overlay drafts, then review Save and Configure.',
    entry_path="/#/devicetree/editor",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
