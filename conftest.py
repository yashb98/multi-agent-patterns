"""Root conftest.py — adds multi_agent_patterns to sys.path for all tests."""

import sys
from pathlib import Path

# Ensure the project root is on the path so `jobpulse`, `shared`, etc. are importable
_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Pre-import jobpulse.utils so subpackages are registered in sys.modules
# before test files are collected (prevents ModuleNotFoundError during collection)
import jobpulse.utils  # noqa: E402, F401
