"""Terminal / shell execution tool implementation."""

import os
import subprocess

from shared.tool_integration import ToolDefinition, RiskLevel


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
                    "params": {"path": "str"},
                },
                "write_file": {
                    "description": "Write content to a file",
                    "risk": RiskLevel.HIGH,
                    "params": {"path": "str", "content": "str"},
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

            dangerous = ["rm -rf /", "sudo", "chmod 777", "mkfs", "> /dev/"]
            if any(d in command for d in dangerous):
                return {"status": "blocked", "message": "Command blocked by security policy"}

            try:
                result = subprocess.run(
                    command, shell=True, capture_output=True, text=True,
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
            except Exception as e:
                return {"status": "error", "message": str(e)}

        elif action == "read_file":
            path = params.get("path", "")
            try:
                with open(path) as f:
                    content = f.read(10000)
                return {"status": "success", "content": content}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        elif action == "write_file":
            path = params.get("path", "")
            content = params.get("content", "")
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w") as f:
                    f.write(content)
                return {"status": "success", "path": path, "bytes": len(content)}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        return {"status": "error", "message": f"Unknown action: {action}"}
