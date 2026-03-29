"""File upload handling — standard, hidden, and drag-drop zone inputs."""

from __future__ import annotations

from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.form_engine.models import FillResult

logger = get_logger(__name__)


def _check_file_type(file_path: Path, accept_attr: str | None) -> bool:
    """Check if file extension matches the accept attribute."""
    if not accept_attr:
        return True

    suffix = file_path.suffix.lower()
    accepted = [ext.strip().lower() for ext in accept_attr.split(",")]

    for pattern in accepted:
        if pattern.startswith(".") and suffix == pattern:
            return True
        if pattern == "application/pdf" and suffix == ".pdf":
            return True
        if pattern == "application/msword" and suffix in (".doc", ".docx"):
            return True

    return False


async def fill_file_upload(
    page,
    selector: str,
    file_path: Path,
    timeout: int = 30000,
) -> FillResult:
    """Upload a file to an input[type='file'] element.

    Validates file existence and type before uploading.
    Waits for upload progress indicators to complete.
    """
    try:
        if not file_path.exists():
            return FillResult(
                success=False, selector=selector,
                value_attempted=str(file_path),
                error=f"File does not exist: {file_path}",
            )

        el = await page.query_selector(selector)
        if el is None:
            # Try finding hidden file input in drag-drop zone
            el = await page.query_selector("input[type='file']")
            if el is None:
                return FillResult(
                    success=False, selector=selector,
                    value_attempted=str(file_path),
                    error=f"No file input found for {selector}",
                )

        # Check accept attribute
        accept = await el.get_attribute("accept")
        if not _check_file_type(file_path, accept):
            return FillResult(
                success=False, selector=selector,
                value_attempted=str(file_path),
                error=f"File type {file_path.suffix} not accepted. Allowed: {accept}",
            )

        await el.set_input_files(str(file_path))

        # Wait for upload progress to finish (if any indicator exists)
        try:
            await page.wait_for_selector(
                "[class*='progress'], [class*='upload'][class*='complete'], [class*='success']",
                timeout=5000,
                state="attached",
            )
            await page.wait_for_timeout(1000)
        except Exception:
            pass  # No progress indicator — upload was instant

        logger.debug("file_filler: uploaded %s to %s", file_path.name, selector)
        return FillResult(
            success=True, selector=selector,
            value_attempted=str(file_path), value_set=file_path.name,
        )

    except Exception as exc:
        logger.error("file_filler: error uploading to %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=str(file_path), error=str(exc),
        )


async def find_file_inputs(page) -> dict[str, str]:
    """Scan page for file upload fields and categorise by label.

    Returns dict: {"resume": selector, "cover_letter": selector, ...}
    """
    inputs = await page.query_selector_all("input[type='file']")
    categorised: dict[str, str] = {}

    for inp in inputs:
        inp_id = await inp.get_attribute("id") or ""
        inp_name = await inp.get_attribute("name") or ""
        label_text = ""

        if inp_id:
            label_el = await page.query_selector(f"label[for='{inp_id}']")
            if label_el:
                label_text = (await label_el.text_content() or "").lower()

        combined = f"{inp_id} {inp_name} {label_text}".lower()
        if inp_id:
            selector = f"#{inp_id}"
        elif inp_name:
            selector = f"input[name='{inp_name}']"
        else:
            selector = "input[type='file']"

        if any(kw in combined for kw in ("resume", "cv", "curriculum")):
            categorised["resume"] = selector
        elif any(kw in combined for kw in ("cover", "letter", "motivation")):
            categorised["cover_letter"] = selector
        else:
            categorised.setdefault("other", selector)

    return categorised
