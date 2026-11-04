"""Compatibility alias; use zephyr_utilities.features.memory.model."""

import sys
from ..features.memory import model as _implementation

sys.modules[__name__] = _implementation
