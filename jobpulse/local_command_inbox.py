"""Local command inbox for trusted on-machine automation triggers.

Used when a local agent needs to enqueue a command into the long-running
Telegram daemon process, so the daemon can execute the exact same routing and
approval flow it would use for a real inbound chat message.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

INBOX_DIR = DATA_DIR / "local_command_queue"


def enqueue_local_command(text: str, *, source: str = "cursor") -> str:
    """Persist a local command for the daemon to consume."""
    command_id = str(uuid.uuid4())[:8]
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": command_id,
        "text": text,
        "source": source,
        "created_at": datetime.now(UTC).isoformat(),
    }
    tmp_path = INBOX_DIR / f".{command_id}.json.tmp"
    final_path = INBOX_DIR / f"{command_id}.json"
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(final_path)
    logger.info("local_command_inbox: enqueued %s from %s", command_id, source)
    return command_id


def drain_local_commands(limit: int = 20) -> list[dict]:
    """Atomically load and remove up to *limit* pending local commands."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    commands: list[dict] = []
    for path in sorted(INBOX_DIR.glob("*.json"))[:limit]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("local_command_inbox: invalid command file %s: %s", path, exc)
            path.unlink(missing_ok=True)
            continue
        path.unlink(missing_ok=True)
        commands.append(payload)
    return commands
