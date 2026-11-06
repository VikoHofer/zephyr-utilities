"""Limited source-preserving overlay editor; modules and pin configuration are planned."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register

FEATURE = FeatureDefinition(
    id='devicetree',
    title='Device Tree Editor',
    description='Configure local bus devices, GPIO LEDs/buttons and binding properties in overlay drafts, then review Save and Configure. Modules and pin routing are planned.',
    entry_path="/#/devicetree/editor",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
