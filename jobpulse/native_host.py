#!/usr/bin/env python3
"""Native Messaging host for Chrome extension bootstrap.

Chrome calls this via stdin/stdout JSON. Its only job: ensure the
FastAPI backend is running on :8000, then return {"status": "ready"}.

Registered as: com.jobpulse.brain
"""

import json
import os
import struct
import subprocess
import sys
import time

import httpx

BACKEND_URL = "http://localhost:8000"
HEALTH_ENDPOINT = f"{BACKEND_URL}/api/job/health"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_backend_health() -> bool:
    """Check if FastAPI backend is responding."""
    try:
        resp = httpx.get(HEALTH_ENDPOINT, timeout=3.0)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def start_backend() -> None:
    """Start FastAPI backend as a detached background process."""
    subprocess.Popen(
        [sys.executable, "-m", "jobpulse.runner", "api-server"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def read_message() -> dict:
    """Read a Native Messaging message from stdin."""
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return {}
    length = struct.unpack("=I", raw_length)[0]
    data = sys.stdin.buffer.read(length)
    return json.loads(data.decode("utf-8"))


def send_message(msg: dict) -> None:
    """Write a Native Messaging message to stdout."""
    encoded = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def main() -> None:
    """Main entry point -- handle one bootstrap request."""
    _msg = read_message()

    if check_backend_health():
        send_message({"status": "ready", "port": 8000})
        return

    start_backend()

    # Wait up to 10s for backend to start
    for _ in range(20):
        time.sleep(0.5)
        if check_backend_health():
            send_message({"status": "ready", "port": 8000})
            return

    send_message({"status": "error", "message": "Backend failed to start within 10s"})


if __name__ == "__main__":
    main()
