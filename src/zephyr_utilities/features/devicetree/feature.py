"""Read-only resolved Device Tree inspection inside the shared application."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register

FEATURE = FeatureDefinition(
    id='devicetree',
    title='Device Tree Editor',
    description='Inspect resolved nodes, browse current sources and navigate bindings for an existing build. Source editing remains planned.',
    entry_path="/#/devicetree",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
