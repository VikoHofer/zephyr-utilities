"""Automatic chip discovery with source-preserving overlay drafts."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition, NavigationItem
from .api import register

FEATURE = FeatureDefinition(
    id='devicetree',
    title='Device Tree Editor',
    description='Open a project to discover its chip, inspect known pins and assignments, and edit its overlay source. Review and Save changes with Undo, Redo and draft recovery.',
    entry_path="/#/devicetree/editor",
    navigation=(
        NavigationItem("Chip and pin editor", "editor"),
        NavigationItem("Supported boards", "support"),
    ),
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
