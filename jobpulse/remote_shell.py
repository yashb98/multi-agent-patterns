"""Remote Shell — execute whitelisted commands from Telegram securely.

Security model:
  - Whitelist of allowed command prefixes (git, ls, cat, etc.)
  - Blacklist of dangerous patterns (rm, sudo, pipes, redirects, etc.)
  - shlex.split() for safe argument parsing
  - shell=False to prevent injection
  - Timeout + output truncation
  - cwd locked to PROJECT_DIR
"""

import shlex
import subprocess
from shared.logging_config import get_logger
from jobpulse.config import PROJECT_DIR

logger = get_logger(__name__)

# Import configurable limits
try:
    from jobpulse.config import SHELL_TIMEOUT, SHELL_MAX_OUTPUT
except ImportError:
    SHELL_TIMEOUT = 30
    SHELL_MAX_OUTPUT = 4000

# ── Security rules ──

ALLOWED_PREFIXES = [
    "git", "ls", "cat", "head", "tail", "wc", "find",
    "python -m pytest", "python -m jobpulse",
    "pip list", "pip show",
    "which", "pwd", "df -h", "uptime", "date",
    "grep", "tree", "du -sh", "echo", "curl -s",
    "vercel", "npm", "node --version",
]

BLOCKED_PATTERNS = [
    "rm", "sudo", "chmod", "chown", "mkfs", "dd",
    "shutdown", "reboot", "kill", "killall", "pkill",
    "export", "unset", "source",
    ">>", ">", "|", "&&", "||", ";",
    "`", "$(",
]


def _is_allowed(command_str: str) -> tuple[bool, str]:
    """Check if a command is safe to execute. Returns (allowed, reason)."""
    cmd_lower = command_str.strip().lower()

    # Check blacklist first (higher priority)
    for pattern in BLOCKED_PATTERNS:
        if pattern in command_str:
            return False, f"Blocked pattern: '{pattern}'"

    # Check whitelist
    for prefix in ALLOWED_PREFIXES:
        if cmd_lower.startswith(prefix):
            return True, ""

    return False, f"Command not in whitelist. Allowed: {', '.join(ALLOWED_PREFIXES)}"


def execute(command_str: str) -> str:
    """Validate and execute a shell command. Returns output or error message."""
    command_str = command_str.strip()
    if not command_str:
        return "No command provided."

    # Security check
    allowed, reason = _is_allowed(command_str)
    if not allowed:
        logger.warning("Blocked command: %s — %s", command_str[:80], reason)
        return f"Blocked: {reason}"

    try:
        args = shlex.split(command_str)
    except ValueError as e:
        return f"Parse error: {e}"

    logger.info("Executing: %s", command_str[:100])

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT,
            cwd=str(PROJECT_DIR),
            shell=False,
        )

        output = result.stdout
        if result.stderr:
            output += ("\n--- stderr ---\n" + result.stderr) if output else result.stderr

        if not output.strip():
            output = "(no output)" if result.returncode == 0 else f"(exit code {result.returncode})"

        # Truncate
        if len(output) > SHELL_MAX_OUTPUT:
            output = output[:SHELL_MAX_OUTPUT] + f"\n... truncated ({len(output)} chars total)"

        prefix = "ok" if result.returncode == 0 else f"exit {result.returncode}"
        return f"[{prefix}]\n{output}"

    except subprocess.TimeoutExpired:
        return f"Timed out after {SHELL_TIMEOUT}s"
    except FileNotFoundError:
        return f"Command not found: {args[0]}"
    except Exception as e:
        logger.error("Shell error: %s", e)
        from shared.agent_result import DispatchError, classify_error
        cat, retry = classify_error(e)
        return DispatchError(cat, str(e), retry, agent_name="remote_shell").to_user_message()
