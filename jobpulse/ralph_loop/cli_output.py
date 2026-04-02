"""Rich CLI output for Ralph Loop test results."""

from __future__ import annotations

from typing import Any

from jobpulse.ralph_loop.test_runner import TestRunResult


_VERDICT_LABELS = {
    "success": "SUCCESS",
    "partial": "PARTIAL",
    "blocked": "BLOCKED",
    "error": "ERROR",
}


def format_test_result(
    result: TestRunResult,
    iteration_details: list[dict[str, Any]],
) -> str:
    """Format a test run result as a readable string.

    Uses Rich Table if available, falls back to plain text.
    """
    try:
        return _format_rich(result, iteration_details)
    except ImportError:
        return _format_plain(result, iteration_details)


def _format_rich(
    result: TestRunResult,
    iteration_details: list[dict[str, Any]],
) -> str:
    from io import StringIO
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel

    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)

    verdict_label = _VERDICT_LABELS.get(result.verdict, result.verdict.upper())

    header = (
        f"Ralph Loop Test -- {result.platform.title()}\n"
        f"URL: {result.url[:60]}"
    )
    console.print(Panel(header, expand=False))

    if iteration_details:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Iter", justify="center", width=5)
        table.add_column("Diagnosis", width=25)
        table.add_column("Fix Type", width=15)

        for detail in iteration_details:
            diag = detail.get("diagnosis") or "No issues found"
            fix = detail.get("fix_type") or "--"
            table.add_row(str(detail.get("iteration", "?")), diag[:25], fix)

        console.print(table)

    fields_total = result.fields_filled + result.fields_failed
    footer = (
        f"Verdict: {verdict_label}  |  "
        f"Fields: {result.fields_filled}/{fields_total}  |  "
        f"{len(result.fixes_applied)} fixes  |  "
        f"{result.duration_ms / 1000:.1f}s"
    )
    if result.screenshot_dir:
        footer += f"\nScreenshots: {result.screenshot_dir}"
    if result.error_summary:
        footer += f"\nError: {result.error_summary[:80]}"

    console.print(footer)
    return buf.getvalue()


def _format_plain(
    result: TestRunResult,
    iteration_details: list[dict[str, Any]],
) -> str:
    verdict_label = _VERDICT_LABELS.get(result.verdict, result.verdict.upper())
    lines = [
        f"Ralph Loop Test -- {result.platform.title()}",
        f"URL: {result.url[:60]}",
        f"Verdict: {verdict_label}",
        f"Iterations: {result.iterations}",
        f"Fields filled: {result.fields_filled}",
        f"Fields failed: {result.fields_failed}",
        f"Fixes applied: {len(result.fixes_applied)}",
        f"Duration: {result.duration_ms / 1000:.1f}s",
    ]
    if iteration_details:
        lines.append("")
        for d in iteration_details:
            diag = d.get("diagnosis") or "No issues"
            fix = d.get("fix_type") or "--"
            lines.append(f"  Iter {d.get('iteration', '?')}: {diag} [{fix}]")
    if result.screenshot_dir:
        lines.append(f"Screenshots: {result.screenshot_dir}")
    if result.error_summary:
        lines.append(f"Error: {result.error_summary[:80]}")
    return "\n".join(lines)
