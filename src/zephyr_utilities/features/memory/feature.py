"""Memory Explorer's explicit platform registration."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register

FEATURE = FeatureDefinition(
    id="memory",
    title="Memory Explorer",
    description="Generate Zephyr RAM/ROM reports, explore symbols and compare snapshots.",
    entry_path="/#/memory/overview",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
