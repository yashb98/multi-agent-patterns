"""Project-level path constants — importable without heavy deps.

shared/ modules should import DATA_DIR from here instead of computing
Path(__file__).parent.parent / "data" independently.
"""

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
LOGS_DIR = PROJECT_DIR / "logs"
