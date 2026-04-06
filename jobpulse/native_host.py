#!/usr/bin/env python3
"""Native Messaging host for JobPulse Chrome extension.

Chrome extension sends {action: "ensure_running"}.
This script checks if the FastAPI backend is alive; if not, it starts it
as a detached subprocess and waits up to 10s for it to become healthy.
It then writes {status: "ready", port: 8000} (or an error) back to stdout.

Native Messaging wire format (both stdin and stdout):
  [4-byte little-endian uint32 length][JSON bytes]
"""

import json
import struct
import sys
import time
import subprocess
import urllib.request
import urllib.error
import os

HEALTH_URL = "http://localhost:8000/api/job/health"
PORT = 8000
STARTUP_TIMEOUT_S = 10
POLL_INTERVAL_S = 1


# ─────────────────────────────────────────────
# Native Messaging I/O helpers
# ─────────────────────────────────────────────

def read_message() -> dict:
    """Read one Native Messaging message from stdin."""
    raw_len = sys.stdin.buffer.read(4)
    if len(raw_len) < 4:
        raise EOFError("stdin closed")
    msg_len = struct.unpack("<I", raw_len)[0]
    raw_msg = sys.stdin.buffer.read(msg_len)
    return json.loads(raw_msg.decode("utf-8"))


def write_message(payload: dict) -> None:
    """Write one Native Messaging message to stdout."""
    encoded = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


# ─────────────────────────────────────────────
# Backend health + bootstrap
# ─────────────────────────────────────────────

def is_backend_healthy() -> bool:
    """Return True if FastAPI is responding to GET /api/job/health."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_backend() -> None:
    """Launch `python -m jobpulse.runner webhook` as a detached background process."""
    devnull = open(os.devnull, "wb")
    subprocess.Popen(
        [sys.executable, "-m", "jobpulse.runner", "webhook"],
        stdout=devnull,
        stderr=devnull,
        stdin=subprocess.DEVNULL,
        # Detach from the current process group so it survives after this
        # script exits (POSIX) or is created in a new process group (Windows).
        start_new_session=True,
    )


def wait_for_backend(timeout_s: int = STARTUP_TIMEOUT_S) -> bool:
    """Poll health endpoint until it responds or timeout expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if is_backend_healthy():
            return True
        time.sleep(POLL_INTERVAL_S)
    return False


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main() -> None:
    try:
        msg = read_message()
    except Exception as exc:
        write_message({"status": "error", "message": f"Failed to read message: {exc}"})
        return

    action = msg.get("action", "")

    if action == "ensure_running":
        if is_backend_healthy():
            write_message({"status": "ready", "port": PORT})
            return

        # Backend is down — start it
        try:
            start_backend()
        except Exception as exc:
            write_message({"status": "error", "message": f"Failed to start backend: {exc}"})
            return

        # Wait for it to become healthy
        if wait_for_backend(STARTUP_TIMEOUT_S):
            write_message({"status": "ready", "port": PORT})
        else:
            write_message({
                "status": "error",
                "message": f"Backend did not become healthy within {STARTUP_TIMEOUT_S}s",
            })
        return

    # Unknown action
    write_message({"status": "error", "message": f"Unknown action: {action!r}"})


if __name__ == "__main__":
    main()
