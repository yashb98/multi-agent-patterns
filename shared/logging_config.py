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

import contextvars
import json
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
_RESERVED_RECORD_ATTRS = set(logging.LogRecord(
    name="x", level=logging.INFO, pathname="", lineno=0, msg="", args=(), exc_info=None,
).__dict__.keys())
_FORMATTER_INJECTED_ATTRS = {
    "message",
    "asctime",
    "exc_text",
    "stack_info",
}


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
    console.setFormatter(StructuredFormatter("[%(name)s] %(message)s%(structured)s"))
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
    file_handler.setFormatter(StructuredFormatter(
        "%(asctime)s [%(levelname)s] [%(run_id)s] [%(trajectory_id)s] %(name)s: %(message)s%(structured)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)


# ─── RUN ID TRACKING ─────────────────────────────────────────────
# Thread-local run ID for correlating logs across a single pipeline execution.

_run_id_local = threading.local()
_trajectory_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trajectory_id",
    default="no_trajectory",
)


def generate_run_id() -> str:
    """Generate a short run ID like 'run_a1b2c3'."""
    return f"run_{uuid.uuid4().hex[:6]}"


def set_run_id(run_id: str):
    """Set the run ID for the current thread."""
    _run_id_local.run_id = run_id


def get_run_id() -> str:
    """Get the current run ID (or 'no_run' if not set)."""
    return getattr(_run_id_local, "run_id", "no_run")


def set_trajectory_id(trajectory_id: str) -> None:
    """Bind a trajectory ID to the current execution context."""
    _trajectory_id_var.set(trajectory_id or "no_trajectory")


def get_trajectory_id() -> str:
    """Get the current trajectory ID (or 'no_trajectory' if not set)."""
    return _trajectory_id_var.get()


def clear_trajectory_id() -> None:
    """Clear the current trajectory binding."""
    _trajectory_id_var.set("no_trajectory")


class RunIdFilter(logging.Filter):
    """Inject run_id into every log record."""
    def filter(self, record):
        record.run_id = get_run_id()
        return True


class StructuredFormatter(logging.Formatter):
    """Formatter that appends non-standard LogRecord fields as JSON."""

    def format(self, record):
        record.run_id = getattr(record, "run_id", get_run_id())
        record.trajectory_id = getattr(record, "trajectory_id", get_trajectory_id())
        extras = {}
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS:
                continue
            if key in _FORMATTER_INJECTED_ATTRS:
                continue
            if key in {"run_id", "trajectory_id", "structured"}:
                continue
            extras[key] = value
        record.structured = f" | {json.dumps(extras, default=str, sort_keys=True)}" if extras else ""
        return super().format(record)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger. Call once per module at module level."""
    _setup_root()
    return logging.getLogger(name)
