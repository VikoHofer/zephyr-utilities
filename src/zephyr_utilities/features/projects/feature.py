"""Environment analysis is available; project creation remains planned."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition
from .api import register

FEATURE = FeatureDefinition(
    id='projects',
    title='Project Creator',
    description='Analyse local Zephyr environments, boards, chips and installed SDKs. Project creation is planned.',
    entry_path="/#/projects/environment",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    register=register,
)
