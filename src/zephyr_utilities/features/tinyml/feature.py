"""Roadmap manifest; no runtime implementation is registered."""

from ...core.features import FeatureDefinition

FEATURE = FeatureDefinition(
    id='tinyml',
    title='TinyML Experiments',
    description='Analyze model size, device measurements and experiment tradeoffs alongside firmware memory.',
    status="planned",
    frontend=None,
    register=None,
)
