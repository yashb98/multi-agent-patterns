"""Centralized logging configuration for the entire project.

Usage in any module:
    from shared.logging_config import get_logger, generate_run_id, set_run_id, get_run_id
    logger = get_logger(__name__)
    logger.info("Something happened")

Run ID tracking:
    run_id = generate_run_id()  # "run_a1b2c3"
    set_run_id(run_id)          # Set for current thread
    logger.info("msg")          # Includes [run_a1b2c3] prefix in file logs
"""

import logging
import sys
import threading
import uuid
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

    # File handler — DEBUG level, full timestamps, 5MB rotation, run_id correlation
    file_handler = RotatingFileHandler(
        LOGS_DIR / "jobpulse.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(RunIdFilter())
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(run_id)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)


# ─── RUN ID TRACKING ─────────────────────────────────────────────
# Thread-local run ID for correlating logs across a single pipeline execution.

_run_id_local = threading.local()


def generate_run_id() -> str:
    """Generate a short run ID like 'run_a1b2c3'."""
    return f"run_{uuid.uuid4().hex[:6]}"


def set_run_id(run_id: str):
    """Set the run ID for the current thread."""
    _run_id_local.run_id = run_id


def get_run_id() -> str:
    """Get the current run ID (or 'no_run' if not set)."""
    return getattr(_run_id_local, "run_id", "no_run")


class RunIdFilter(logging.Filter):
    """Inject run_id into every log record."""
    def filter(self, record):
        record.run_id = get_run_id()
        return True


def get_logger(name: str) -> logging.Logger:
    """Get a named logger. Call once per module at module level."""
    _setup_root()
    return logging.getLogger(name)
