"""Environment analysis and reviewed workspace application creation."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register

FEATURE = FeatureDefinition(
    id='projects',
    title='Project Creator',
    description='Discover your environment, create and build an application, or inspect existing build outputs.',
    entry_path="/#/projects/environment",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
