"""Shared lock helpers for process-local and system-wide coordination.

Use `process_lock()` / `process_event()` for in-process thread safety.
Use `system_lock()` when contention can come from multiple processes.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from shared.paths import DATA_DIR

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]


_process_lock_registry: dict[str, threading.Lock] = {}
_process_event_registry: dict[str, threading.Event] = {}
_registry_guard = threading.Lock()


def process_lock(name: str) -> threading.Lock:
    """Return a named process-local lock (singleton per process)."""
    with _registry_guard:
        lock = _process_lock_registry.get(name)
        if lock is None:
            lock = threading.Lock()
            _process_lock_registry[name] = lock
        return lock


def process_event(name: str) -> threading.Event:
    """Return a named process-local event (singleton per process)."""
    with _registry_guard:
        event = _process_event_registry.get(name)
        if event is None:
            event = threading.Event()
            _process_event_registry[name] = event
        return event


class SystemLock:
    """Cross-process lock backed by an OS file lock.

    API mirrors `threading.Lock` for `acquire()`/`release()` and supports
    context-manager usage (`with SystemLock(...):`).
    """

    def __init__(self, name: str, lock_dir: Path | None = None):
        safe_name = name.replace("/", "_").replace(" ", "_")
        base_dir = lock_dir or (DATA_DIR / "locks")
        base_dir.mkdir(parents=True, exist_ok=True)
        self._path = base_dir / f"{safe_name}.lock"
        self._fd: int | None = None
        self._guard = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def locked(self) -> bool:
        with self._guard:
            return self._fd is not None

    def acquire(
        self,
        blocking: bool = True,
        timeout: float | None = None,
        poll_interval: float = 0.1,
    ) -> bool:
        if fcntl is None:
            raise RuntimeError("SystemLock requires POSIX fcntl support")

        with self._guard:
            if self._fd is not None:
                return True

        deadline = None if timeout is None else (time.monotonic() + max(0.0, timeout))
        while True:
            fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o644)
            try:
                flags = fcntl.LOCK_EX | fcntl.LOCK_NB
                fcntl.flock(fd, flags)
                os.ftruncate(fd, 0)
                os.write(fd, f"{os.getpid()}\n".encode())
                with self._guard:
                    self._fd = fd
                return True
            except BlockingIOError:
                os.close(fd)
                if not blocking:
                    return False
                if deadline is not None and time.monotonic() >= deadline:
                    return False
                time.sleep(max(0.01, poll_interval))
            except Exception:
                os.close(fd)
                raise

    def release(self) -> None:
        if fcntl is None:
            raise RuntimeError("SystemLock requires POSIX fcntl support")

        with self._guard:
            fd = self._fd
            self._fd = None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> "SystemLock":
        if not self.acquire(blocking=True):
            raise TimeoutError(f"Could not acquire system lock: {self._path}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def system_lock(name: str) -> SystemLock:
    """Return a cross-process lock instance for *name*."""
    return SystemLock(name=name)

