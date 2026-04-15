"""Tests for shared/tools/terminal.py — command execution, injection, path traversal."""

import os
import subprocess
from unittest.mock import patch

import pytest

from shared.tools.terminal import TerminalTool, _validate_path


# ── Helpers ──

def run(action, params):
    return TerminalTool.execute(action, params)


# ── Normal execution ──

class TestExecuteCommand:
    def test_echo(self, tmp_path):
        result = run("execute", {"command": "echo hello", "working_dir": str(tmp_path)})
        assert result["status"] == "success"
        assert "hello" in result["stdout"]
        assert result["returncode"] == 0

    def test_ls(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        result = run("execute", {"command": "ls", "working_dir": str(tmp_path)})
        assert result["status"] == "success"
        assert "a.txt" in result["stdout"]

    def test_nonexistent_command(self, tmp_path):
        result = run("execute", {"command": "nonexistent_cmd_xyz", "working_dir": str(tmp_path)})
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    def test_stdout_truncated_to_2000(self, tmp_path):
        # Generate output longer than 2000 chars
        result = run("execute", {"command": "python3 -c \"print('A' * 3000)\"", "working_dir": str(tmp_path)})
        assert result["status"] == "success"
        assert len(result["stdout"]) <= 2000

    def test_unknown_action(self, tmp_path):
        result = run("bogus", {"working_dir": str(tmp_path)})
        assert result["status"] == "error"
        assert "Unknown action" in result["message"]


# ── Dangerous command blocking ──

class TestBlockedCommands:
    @pytest.mark.parametrize("cmd", [
        "rm -rf /",
        "rm file.txt",
        "sudo ls",
        "chmod 777 file",
        "mkfs /dev/sda",
        "kill -9 1234",
        "killall python",
    ])
    def test_blocked_commands(self, tmp_path, cmd):
        result = run("execute", {"command": cmd, "working_dir": str(tmp_path)})
        assert result["status"] == "blocked"


# ── Shell injection attempts ──

class TestShellInjection:
    @pytest.mark.parametrize("cmd", [
        "echo hello; rm -rf /",
        "echo hello && rm -rf /",
        "echo hello || rm -rf /",
        "echo hello | cat /etc/passwd",
        "echo $(whoami)",
        "echo `whoami`",
        "ls >> /etc/passwd",
    ])
    def test_metacharacter_injection(self, tmp_path, cmd):
        result = run("execute", {"command": cmd, "working_dir": str(tmp_path)})
        assert result["status"] == "blocked"
        assert "metacharacter" in result["message"].lower() or "blocked" in result["message"].lower()

    def test_shell_false_prevents_expansion(self, tmp_path):
        """Even if metacharacter check missed something, shell=False prevents execution."""
        # With shell=False, '$HOME' is literal, not expanded
        result = run("execute", {"command": "echo $HOME", "working_dir": str(tmp_path)})
        assert result["status"] == "success"
        # shell=False means $HOME is literal text, not expanded
        assert result["stdout"].strip() == "$HOME"

    def test_invalid_quoting(self, tmp_path):
        result = run("execute", {"command": "echo 'unterminated", "working_dir": str(tmp_path)})
        assert result["status"] == "error"
        assert "syntax" in result["message"].lower()

    def test_empty_command(self, tmp_path):
        result = run("execute", {"command": "", "working_dir": str(tmp_path)})
        assert result["status"] == "error"


# ── Timeout ──

class TestTimeout:
    def test_command_timeout(self, tmp_path):
        with patch("shared.tools.terminal.subprocess.run", side_effect=subprocess.TimeoutExpired("sleep", 30)):
            result = run("execute", {"command": "sleep 60", "working_dir": str(tmp_path)})
        assert result["status"] == "error"
        assert "timed out" in result["message"].lower()


# ── Path validation ──

class TestPathValidation:
    def test_valid_path_inside_sandbox(self, tmp_path):
        ok, resolved = _validate_path("subdir/file.txt", str(tmp_path))
        assert ok
        assert resolved.startswith(str(tmp_path))

    def test_traversal_blocked(self, tmp_path):
        ok, msg = _validate_path("../../etc/passwd", str(tmp_path))
        assert not ok
        assert "outside sandbox" in msg

    def test_absolute_path_outside_blocked(self, tmp_path):
        ok, msg = _validate_path("/etc/passwd", str(tmp_path))
        assert not ok
        assert "outside sandbox" in msg

    def test_symlink_escape(self, tmp_path):
        """Symlink pointing outside sandbox should be blocked."""
        link = tmp_path / "escape"
        link.symlink_to("/etc")
        ok, msg = _validate_path("escape/passwd", str(tmp_path))
        assert not ok
        assert "outside sandbox" in msg


# ── read_file sandboxing ──

class TestReadFile:
    def test_read_existing_file(self, tmp_path):
        (tmp_path / "hello.txt").write_text("world")
        result = run("read_file", {"path": "hello.txt", "working_dir": str(tmp_path)})
        assert result["status"] == "success"
        assert result["content"] == "world"

    def test_read_absolute_inside_sandbox(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("ok")
        result = run("read_file", {"path": str(f), "working_dir": str(tmp_path)})
        assert result["status"] == "success"

    def test_read_traversal_blocked(self, tmp_path):
        result = run("read_file", {"path": "../../etc/passwd", "working_dir": str(tmp_path)})
        assert result["status"] == "blocked"
        assert "outside sandbox" in result["message"]

    def test_read_absolute_outside_blocked(self, tmp_path):
        result = run("read_file", {"path": "/etc/passwd", "working_dir": str(tmp_path)})
        assert result["status"] == "blocked"

    def test_read_nonexistent(self, tmp_path):
        result = run("read_file", {"path": "nope.txt", "working_dir": str(tmp_path)})
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()


# ── write_file sandboxing ──

class TestWriteFile:
    def test_write_new_file(self, tmp_path):
        result = run("write_file", {"path": "out.txt", "content": "data", "working_dir": str(tmp_path)})
        assert result["status"] == "success"
        assert (tmp_path / "out.txt").read_text() == "data"

    def test_write_creates_subdirs(self, tmp_path):
        result = run("write_file", {"path": "sub/dir/f.txt", "content": "nested", "working_dir": str(tmp_path)})
        assert result["status"] == "success"
        assert (tmp_path / "sub" / "dir" / "f.txt").read_text() == "nested"

    def test_write_traversal_blocked(self, tmp_path):
        result = run("write_file", {"path": "../../../tmp/evil.txt", "content": "x", "working_dir": str(tmp_path)})
        assert result["status"] == "blocked"

    def test_write_absolute_outside_blocked(self, tmp_path):
        result = run("write_file", {"path": "/tmp/evil.txt", "content": "x", "working_dir": str(tmp_path)})
        assert result["status"] == "blocked"


def test_execute_rejects_arbitrary_working_dir():
    """working_dir outside project root must be rejected."""
    from shared.tools.terminal import TerminalTool
    result = TerminalTool.execute("execute", {
        "command": "ls",
        "working_dir": "/etc",
    })
    assert result["status"] == "error"
    assert "outside" in result["message"].lower() or "sandbox" in result["message"].lower()
