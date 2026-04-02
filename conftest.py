"""Root conftest.py — ensures the worktree root takes priority on sys.path.

The parent directory (multi_agent_patterns/) has its own conftest.py that runs
first and adds multi_agent_patterns/ to sys.path.  We need the worktree copy
to win, so we:
  1. Insert the worktree root at position 0 (displacing the parent copy).
  2. Evict the entire jobpulse package tree from sys.modules so the next
     import resolves against the worktree, not the parent tree.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).parent
_ROOT_STR = str(_ROOT)

# --- Force worktree to front of path -----------------------------------------
if _ROOT_STR in sys.path:
    sys.path.remove(_ROOT_STR)
sys.path.insert(0, _ROOT_STR)

# --- Evict any already-cached jobpulse modules from the parent copy ----------
for _key in list(sys.modules.keys()):
    if _key == "jobpulse" or _key.startswith("jobpulse."):
        del sys.modules[_key]

# --- Pre-import jobpulse.utils so subpackages are registered before collect --
import jobpulse.utils  # noqa: E402, F401
