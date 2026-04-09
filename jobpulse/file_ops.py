"""File Operations — view files, tail logs, show errors, system status.

Security: all file paths are resolved and must be under PROJECT_DIR.
Pagination: module-level state tracks last file/offset for "more"/"next".
"""

import os
import subprocess
from pathlib import Path
from shared.logging_config import get_logger
from jobpulse.config import PROJECT_DIR, LOGS_DIR, DATA_DIR

logger = get_logger(__name__)

try:
    from jobpulse.config import MAX_FILE_LINES
except ImportError:
    MAX_FILE_LINES = 100

# ── Pagination state ──

_page_state: dict = {
    "filepath": None,
    "offset": 0,
    "limit": MAX_FILE_LINES,
    "total_lines": 0,
}


def _resolve_safe(filepath: str) -> Path | None:
    """Resolve a filepath and verify it's under PROJECT_DIR. Returns None if unsafe."""
    try:
        # Allow relative paths (relative to PROJECT_DIR)
        if not os.path.isabs(filepath):
            resolved = (PROJECT_DIR / filepath).resolve()
        else:
            resolved = Path(filepath).resolve()

        # Security: must be under PROJECT_DIR (follow symlinks)
        if not str(resolved).startswith(str(PROJECT_DIR.resolve())):
            return None

        return resolved
    except Exception:
        return None


def show_file(filepath: str, offset: int = 0, limit: int = 0) -> str:
    """Read a file under PROJECT_DIR, return numbered lines with pagination.

    Args:
        filepath: Relative or absolute path (must resolve under PROJECT_DIR)
        offset: Line number to start from (0-based)
        limit: Max lines to return (0 = use MAX_FILE_LINES)
    """
    if not limit:
        limit = MAX_FILE_LINES

    resolved = _resolve_safe(filepath)
    if resolved is None:
        return f"Access denied: path must be under project directory."

    if not resolved.exists():
        return f"File not found: {filepath}"

    if resolved.is_dir():
        # List directory contents
        try:
            items = sorted(resolved.iterdir())
            lines = []
            for item in items[:100]:
                prefix = "/" if item.is_dir() else " "
                name = item.name
                lines.append(f"  {prefix} {name}")
            header = f"Directory: {resolved.relative_to(PROJECT_DIR)}\n"
            return header + "\n".join(lines)
        except Exception as e:
            from shared.agent_result import DispatchError, classify_error
            cat, retry = classify_error(e)
            return DispatchError(cat, str(e), retry, agent_name="file_ops").to_user_message()

    try:
        with open(resolved, "r", errors="replace") as f:
            all_lines = f.readlines()
    except Exception as e:
        from shared.agent_result import DispatchError, classify_error
        cat, retry = classify_error(e)
        return DispatchError(cat, str(e), retry, agent_name="file_ops").to_user_message()

    total = len(all_lines)
    end = min(offset + limit, total)
    selected = all_lines[offset:end]

    # Update pagination state
    _page_state["filepath"] = str(resolved)
    _page_state["offset"] = end
    _page_state["limit"] = limit
    _page_state["total_lines"] = total

    # Format with line numbers
    lines = []
    for i, line in enumerate(selected, start=offset + 1):
        lines.append(f"{i:4d} | {line.rstrip()}")

    rel_path = resolved.relative_to(PROJECT_DIR) if str(resolved).startswith(str(PROJECT_DIR)) else resolved
    header = f"{rel_path} (lines {offset + 1}-{end} of {total})"

    if end < total:
        header += "\nSay 'more' for next page."

    return header + "\n" + "\n".join(lines)


def continue_pagination() -> str:
    """Continue reading from the last file/offset."""
    if not _page_state["filepath"]:
        return "Nothing to paginate. Use 'show: <filepath>' first."

    filepath = _page_state["filepath"]
    offset = _page_state["offset"]
    total = _page_state["total_lines"]

    if offset >= total:
        return f"End of file ({total} lines total)."

    return show_file(filepath, offset=offset, limit=_page_state["limit"])


def show_logs(n: int = 50) -> str:
    """Tail the main jobpulse log file."""
    log_file = LOGS_DIR / "jobpulse.log"
    if not log_file.exists():
        # Try telegram listener log
        log_file = LOGS_DIR / "telegram-listener.log"
    if not log_file.exists():
        return "No log files found."

    try:
        with open(log_file, "r", errors="replace") as f:
            all_lines = f.readlines()

        tail = all_lines[-n:] if len(all_lines) > n else all_lines
        output = f"Last {len(tail)} lines from {log_file.name}:\n\n"
        output += "".join(tail)

        if len(output) > 4000:
            output = output[:4000] + "\n... truncated"
        return output
    except Exception as e:
        from shared.agent_result import DispatchError, classify_error
        cat, retry = classify_error(e)
        return DispatchError(cat, str(e), retry, agent_name="file_ops").to_user_message()


def show_errors() -> str:
    """Query process_logger for recent error steps."""
    try:
        from jobpulse.process_logger import _get_conn
        conn = _get_conn()
        rows = conn.execute("""
            SELECT run_id, agent_type, step_name, step_output, created_at
            FROM agent_process_trails
            WHERE status = 'error'
            ORDER BY created_at DESC
            LIMIT 10
        """).fetchall()
        conn.close()

        if not rows:
            return "No recent errors."

        lines = [f"Last {len(rows)} errors:\n"]
        for r in rows:
            lines.append(
                f"  [{r['created_at'][:16]}] {r['agent_type']}: "
                f"{r['step_name']} - {(r['step_output'] or '')[:100]}"
            )
        return "\n".join(lines)
    except Exception as e:
        from shared.agent_result import DispatchError, classify_error
        cat, retry = classify_error(e)
        return DispatchError(cat, str(e), retry, agent_name="file_ops").to_user_message()


def system_status() -> str:
    """Combine daemon health + agent stats + git branch + disk usage."""
    parts = []

    # Git branch
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
            cwd=str(PROJECT_DIR), shell=False,
        )
        branch = result.stdout.strip()
        parts.append(f"Branch: {branch}")
    except Exception:
        parts.append("Branch: unknown")

    # Daemon health
    try:
        from jobpulse.healthcheck import read_heartbeat
        hb = read_heartbeat()
        if hb:
            parts.append(f"Daemon: {hb.get('status', 'unknown')} (last beat: {hb.get('timestamp', 'N/A')})")
        else:
            parts.append("Daemon: no heartbeat found")
    except Exception:
        parts.append("Daemon: health check unavailable")

    # Agent stats from process logger
    try:
        from jobpulse.process_logger import _get_conn
        conn = _get_conn()
        rows = conn.execute("""
            SELECT agent_type, COUNT(*) as runs,
                   SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors
            FROM agent_process_trails
            WHERE created_at > datetime('now', '-24 hours')
            GROUP BY agent_type
            ORDER BY runs DESC
        """).fetchall()
        conn.close()
        if rows:
            parts.append("\nAgent stats (24h):")
            for r in rows:
                err_str = f" ({r['errors']} errors)" if r['errors'] else ""
                parts.append(f"  {r['agent_type']}: {r['runs']} runs{err_str}")
        else:
            parts.append("\nNo agent activity in last 24h.")
    except Exception:
        parts.append("\nAgent stats: unavailable")

    # Disk usage of data/
    try:
        result = subprocess.run(
            ["du", "-sh", str(DATA_DIR)],
            capture_output=True, text=True, timeout=5,
            shell=False,
        )
        if result.stdout.strip():
            size = result.stdout.strip().split("\t")[0]
            parts.append(f"\nData dir: {size}")
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("Data dir size check failed: %s", e)

    return "System Status\n\n" + "\n".join(parts)
