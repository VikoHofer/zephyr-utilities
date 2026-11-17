"""TinyML demo selection through the public Projects creation handoff."""

from pathlib import Path

from ...core.features import FeatureDefinition, FrontendDefinition, NavigationItem
from .api import register

FEATURE = FeatureDefinition(
    id='tinyml',
    title='TinyML Demo Creator',
    description='Create a local MNIST demo in Projects, or draw a digit and send it over UART to receive a prediction.',
    entry_path="/#/tinyml/overview",
    frontend=FrontendDefinition(Path(__file__).parent / "web"),
    navigation=(NavigationItem("Create demo", "overview"), NavigationItem("Draw a digit", "draw")),
    register=register,
)
