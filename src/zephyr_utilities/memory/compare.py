"""Compatibility alias; use zephyr_utilities.features.memory.compare."""

import sys
from ..features.memory import compare as _implementation

sys.modules[__name__] = _implementation
