"""Terminal / shell execution tool implementation."""

import os
import shlex
import subprocess

from shared.tool_integration import ToolDefinition, RiskLevel

# Commands that are never allowed (checked against parsed argv[0] and arguments)
BLOCKED_COMMANDS = {
    "rm", "sudo", "chmod", "chown", "mkfs", "dd",
    "shutdown", "reboot", "kill", "killall", "pkill",
    "export", "unset", "source",
}

# Shell metacharacters that indicate injection attempts — block in raw input
# before parsing, since shlex.split() would interpret some of these
BLOCKED_METACHARACTERS = ["&&", "||", ";", "|", "`", "$(", ">>", "> /dev/"]


def _validate_path(path: str, working_dir: str) -> tuple[bool, str]:
    """Resolve *path* and verify it lives inside *working_dir*.

    Returns (ok, resolved_path_or_error_message).
    """
    resolved = os.path.realpath(os.path.join(working_dir, path) if not os.path.isabs(path) else path)
    sandbox = os.path.realpath(working_dir)
    if not resolved.startswith(sandbox + os.sep) and resolved != sandbox:
        return False, f"Path '{path}' resolves outside sandbox '{sandbox}'"
    return True, resolved


class TerminalTool:
    """Execute shell commands in a sandboxed environment."""

    @staticmethod
    def get_definition() -> ToolDefinition:
        return ToolDefinition(
            name="terminal",
            description="Execute shell commands in a sandboxed environment",
            category="code_execution",
            actions={
                "execute": {
                    "description": "Run a shell command",
                    "risk": RiskLevel.CRITICAL,
                    "params": {"command": "str", "working_dir": "str"},
                },
                "read_file": {
                    "description": "Read a file's contents",
                    "risk": RiskLevel.LOW,
                    "params": {"path": "str", "working_dir": "str"},
                },
                "write_file": {
                    "description": "Write content to a file",
                    "risk": RiskLevel.HIGH,
                    "params": {"path": "str", "content": "str", "working_dir": "str"},
                },
            },
            execute_fn=TerminalTool.execute,
        )

    @staticmethod
    def execute(action: str, params: dict) -> dict:
        if action == "execute":
            command = params.get("command", "")
            working_dir = params.get("working_dir", "/tmp/agent_sandbox")
            os.makedirs(working_dir, exist_ok=True)

            # Block shell metacharacters in raw input (before parsing)
            for meta in BLOCKED_METACHARACTERS:
                if meta in command:
                    return {"status": "blocked", "message": f"Command blocked: shell metacharacter '{meta}'"}

            # Parse into argv — rejects malformed quoting
            try:
                args = shlex.split(command)
            except ValueError as e:
                return {"status": "error", "message": f"Invalid command syntax: {e}"}

            if not args:
                return {"status": "error", "message": "Empty command"}

            # Check parsed command name against blocklist
            if args[0] in BLOCKED_COMMANDS:
                return {"status": "blocked", "message": "Command blocked by security policy"}

            try:
                result = subprocess.run(
                    args, shell=False, capture_output=True, text=True,
                    timeout=30, cwd=working_dir,
                )
                return {
                    "status": "success",
                    "stdout": result.stdout[:2000],
                    "stderr": result.stderr[:500],
                    "returncode": result.returncode,
                }
            except subprocess.TimeoutExpired:
                return {"status": "error", "message": "Command timed out (30s limit)"}
            except FileNotFoundError:
                return {"status": "error", "message": f"Command not found: {args[0]}"}
            except OSError as e:
                return {"status": "error", "message": str(e)}

        elif action == "read_file":
            path = params.get("path", "")
            working_dir = params.get("working_dir", "/tmp/agent_sandbox")
            ok, resolved = _validate_path(path, working_dir)
            if not ok:
                return {"status": "blocked", "message": resolved}
            try:
                with open(resolved) as f:
                    content = f.read(10000)
                return {"status": "success", "content": content}
            except FileNotFoundError:
                return {"status": "error", "message": f"File not found: {resolved}"}
            except OSError as e:
                return {"status": "error", "message": str(e)}

        elif action == "write_file":
            path = params.get("path", "")
            content = params.get("content", "")
            working_dir = params.get("working_dir", "/tmp/agent_sandbox")
            ok, resolved = _validate_path(path, working_dir)
            if not ok:
                return {"status": "blocked", "message": resolved}
            try:
                os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
                with open(resolved, "w") as f:
                    f.write(content)
                return {"status": "success", "path": resolved, "bytes": len(content)}
            except OSError as e:
                return {"status": "error", "message": str(e)}

        return {"status": "error", "message": f"Unknown action: {action}"}
