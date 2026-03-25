"""Centralized logging configuration for the entire project.

Usage in any module:
    from shared.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("Something happened")
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Ensure logs directory exists
LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

_CONFIGURED = False


def _setup_root():
    """Configure root logger once. Called on first get_logger() call."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler — INFO level, concise format
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "[%(name)s] %(message)s"
    ))
    root.addHandler(console)

    # File handler — DEBUG level, full timestamps, 5MB rotation
    file_handler = RotatingFileHandler(
        LOGS_DIR / "jobpulse.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger. Call once per module at module level."""
    _setup_root()
    return logging.getLogger(name)
