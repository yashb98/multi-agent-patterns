"""Git Operations — status, log, diff, branch, commit (with approval), push (with approval).

All commands run with cwd=PROJECT_DIR and shell=False for safety.
Commit and push require approval via the approval flow.
"""

import subprocess
from shared.logging_config import get_logger
from jobpulse.config import PROJECT_DIR

logger = get_logger(__name__)


def _run_git(*args: str) -> str:
    """Run a git command and return output."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT_DIR),
            shell=False,
        )
        output = result.stdout
        if result.stderr and result.returncode != 0:
            output += "\n" + result.stderr
        return output.strip() if output.strip() else "(no output)"
    except subprocess.TimeoutExpired:
        return "Git command timed out."
    except Exception as e:
        from shared.agent_result import DispatchError, classify_error
        cat, retry = classify_error(e)
        return DispatchError(cat, str(e), retry, agent_name="git_ops").to_user_message()


def git_status() -> str:
    """Formatted git status with emoji."""
    raw = _run_git("status", "--short")
    if raw == "(no output)" or not raw.strip():
        return "Working tree clean."

    lines = []
    for line in raw.split("\n"):
        if not line.strip():
            continue
        code = line[:2].strip()
        filepath = line[3:].strip()
        if code == "M":
            lines.append(f"  Modified: {filepath}")
        elif code == "A":
            lines.append(f"  Added: {filepath}")
        elif code == "D":
            lines.append(f"  Deleted: {filepath}")
        elif code == "??":
            lines.append(f"  Untracked: {filepath}")
        elif code == "R":
            lines.append(f"  Renamed: {filepath}")
        else:
            lines.append(f"  {code} {filepath}")

    branch = _run_git("branch", "--show-current")
    header = f"Branch: {branch}\n\n"
    return header + "\n".join(lines) if lines else header + "Working tree clean."


def git_log(n: int = 5) -> str:
    """Last N commits formatted nicely."""
    n = min(n, 20)  # cap at 20
    raw = _run_git("log", f"-{n}", "--pretty=format:%h  %s  (%ar)", "--no-decorate")
    if not raw or raw.startswith("Error"):
        return raw or "No commits found."

    lines = [f"Last {n} commits:\n"]
    for line in raw.split("\n"):
        if line.strip():
            lines.append(f"  {line.strip()}")
    return "\n".join(lines)


def git_diff() -> str:
    """Git diff stat, truncated."""
    raw = _run_git("diff", "--stat")
    if raw == "(no output)" or not raw.strip():
        # Check staged
        raw = _run_git("diff", "--cached", "--stat")
        if raw == "(no output)" or not raw.strip():
            return "No changes to show."
        return f"Staged changes:\n{raw[:3000]}"

    output = f"Unstaged changes:\n{raw}"

    # Also check staged
    staged = _run_git("diff", "--cached", "--stat")
    if staged and staged != "(no output)":
        output += f"\n\nStaged changes:\n{staged}"

    # Truncate
    if len(output) > 3000:
        output = output[:3000] + "\n... truncated"
    return output


def git_branch() -> str:
    """Current branch info."""
    current = _run_git("branch", "--show-current")
    all_branches = _run_git("branch", "-a", "--no-color")
    return f"Current: {current}\n\nAll branches:\n{all_branches[:2000]}"


def git_commit(message: str) -> str:
    """Commit with approval flow. Sends approval request, returns status."""
    if not message.strip():
        return "No commit message provided. Usage: commit: your message here"

    from jobpulse.approval import request_approval

    def _do_commit(approved: bool) -> str:
        if not approved:
            return "Commit cancelled."
        # Stage all and commit
        add_result = _run_git("add", "-A")
        commit_result = _run_git("commit", "-m", message)
        logger.info("Git commit: %s", commit_result[:100])
        return f"Committed:\n{commit_result[:1000]}"

    # Check what would be committed
    status = _run_git("status", "--short")
    question = f"Commit with message: \"{message}\"\n\nChanges:\n{status[:500]}"
    request_approval(question, callback=_do_commit)
    return f"Awaiting approval to commit: \"{message}\"\nReply yes or no."


def git_push() -> str:
    """Push with approval flow."""
    from jobpulse.approval import request_approval

    branch = _run_git("branch", "--show-current")
    # Show what would be pushed
    unpushed = _run_git("log", "@{u}..HEAD", "--oneline")
    if "error" in unpushed.lower() or "fatal" in unpushed.lower():
        unpushed = "(could not determine unpushed commits)"

    def _do_push(approved: bool) -> str:
        if not approved:
            return "Push cancelled."
        result = _run_git("push")
        logger.info("Git push: %s", result[:100])
        return f"Pushed:\n{result[:1000]}"

    question = f"Push branch '{branch}' to remote?\n\nUnpushed commits:\n{unpushed[:500]}"
    request_approval(question, callback=_do_push)
    return f"Awaiting approval to push '{branch}'.\nReply yes or no."
