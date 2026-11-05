"""Limited source-preserving overlay editor; modules and pin configuration are planned."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register

FEATURE = FeatureDefinition(
    id='devicetree',
    title='Device Tree Editor',
    description='Edit overlay status and simple properties, review and save source changes, and recover drafts. Modules and pin configuration are planned.',
    entry_path="/#/devicetree/editor",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
