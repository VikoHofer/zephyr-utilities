"""Environment analysis and reviewed workspace application creation."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register

FEATURE = FeatureDefinition(
    id='projects',
    title='Project Creator',
    description='Discover your environment, create a minimal C application, and explicitly configure or build it.',
    entry_path="/#/projects/environment",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
