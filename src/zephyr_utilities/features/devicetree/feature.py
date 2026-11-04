"""Roadmap manifest; no runtime implementation is registered."""

from ...core.features import FeatureDefinition

FEATURE = FeatureDefinition(
    id='devicetree',
    title='Device Tree Editor',
    description='Inspect compiled device trees and edit board overlays in the shared workspace.',
    status="planned",
    frontend=None,
    register=None,
)
