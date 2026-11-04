"""Advanced read-only build, source and resolved Device Tree inspection."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition, NavigationItem
from .api import register


FEATURE = FeatureDefinition(
    id="inspector",
    title="Build Inspector",
    description="Inspect existing build outputs, resolved Device Trees, current sources and binding references.",
    entry_path="/#/inspector/builds",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
    navigation_group="advanced",
    navigation=(NavigationItem("Build outputs", "builds"),
                NavigationItem("Sources and Device Tree", "sources", ("inspect", "application"))),
)
