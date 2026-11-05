"""Environment analysis and reviewed workspace application creation."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition, NavigationItem
from .api import register

FEATURE = FeatureDefinition(
    id='projects',
    title='Project Creator',
    description='Manage workspace applications, create a new application, and configure or build it with your selected board and SDK.',
    entry_path="/#/projects/applications",
    navigation=(NavigationItem("Applications", "applications"),
                NavigationItem("Create application", "create"),
                NavigationItem("Custom boards", "custom-boards"),
                NavigationItem("Environment", "environment")),
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
