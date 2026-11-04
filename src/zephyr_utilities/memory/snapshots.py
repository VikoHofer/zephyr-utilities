"""Compatibility alias; use zephyr_utilities.features.memory.snapshots."""

import sys
from ..features.memory import snapshots as _implementation

sys.modules[__name__] = _implementation
