"""Explicit built-in registry. Add one import and one entry for each feature."""

from .devicetree.feature import FEATURE as DEVICETREE
from .memory.feature import FEATURE as MEMORY
from .projects.feature import FEATURE as PROJECTS
from .tinyml.feature import FEATURE as TINYML


def builtin_features():
    return (MEMORY, DEVICETREE, PROJECTS, TINYML)
