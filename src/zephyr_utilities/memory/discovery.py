"""Compatibility alias; use zephyr_utilities.features.memory.discovery."""

import sys
from ..features.memory import discovery as _implementation

sys.modules[__name__] = _implementation
