"""Compatibility alias; use zephyr_utilities.features.memory.runner."""

import sys
from ..features.memory import runner as _implementation

sys.modules[__name__] = _implementation
