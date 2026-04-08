"""Base ATS adapter abstract class."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


class BaseATSAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def detect(self, url: str) -> bool:
        """Return True if this adapter handles this URL."""

    @abstractmethod
    def fill_and_submit(
        self,
        url: str,
        cv_path: Path,
        cover_letter_path: Path | None,
        profile: dict,
        custom_answers: dict,
        overrides: dict[str, Any] | None = None,
        dry_run: bool = False,
        engine: str = "extension",
    ) -> dict:
        """Fill form and submit.

        Args:
            overrides: Ralph Loop learned fixes — selector overrides, wait
                adjustments, strategy switches, field remaps, interaction mods.
                Adapters can use resolve_selector() to apply selector overrides.

        Returns:
            dict with keys:
                success (bool): whether submission succeeded
                screenshot (Path | None): path to screenshot if taken
                error (str | None): error message if failed
        """

    def resolve_selector(self, selector: str, overrides: dict[str, Any] | None = None) -> str:
        """Return the override selector if one exists, otherwise the original.

        Adapters should call this before every query_selector() to benefit
        from Ralph Loop learned selector fixes.
        """
        if overrides and selector in overrides.get("selector_overrides", {}):
            new = overrides["selector_overrides"][selector]
            logger.debug("Selector override: %s → %s", selector, new)
            return new
        return selector

    def get_wait_override(self, step: str, default_ms: int, overrides: dict[str, Any] | None = None) -> int:
        """Return learned wait time for a step, or the default."""
        if overrides and step in overrides.get("wait_overrides", {}):
            return overrides["wait_overrides"][step]
        return default_ms

    def answer_screening_questions(
        self,
        page: Any,
        job_context: dict | None = None,
    ) -> int:
        """Detect and answer screening questions on the current page.

        Scans all visible form groups for label/question text, determines
        the input type, and calls ``get_answer()`` with the correct
        ``input_type`` and ``platform``.

        Args:
            page: Playwright page object.
            job_context: Dict with ``job_title``, ``company``, ``location`` keys.

        Returns:
            Number of questions answered.
        """
        from jobpulse.screening_answers import get_answer

        answered = 0

        # Common selectors for form groups across ATS platforms
        form_groups = page.query_selector_all(
            "fieldset, "
            ".field, "
            ".form-group, "
            ".application-question, "
            "[data-test-form-element], "
            ".fb-dash-form-element, "
            ".jobs-easy-apply-form-section__grouping"
        )

        for group in form_groups:
            try:
                # Find question label
                label_el = group.query_selector(
                    "label, legend, .field-label, "
                    ".application-label, span.t-14"
                )
                if not label_el:
                    continue
                question = label_el.text_content().strip()
                if not question or len(question) < 3:
                    continue

                # Find the input element
                input_el = group.query_selector(
                    "input:not([type='hidden']):not([type='file']), "
                    "select, textarea"
                )
                if not input_el:
                    continue

                tag = input_el.evaluate("el => el.tagName.toLowerCase()")

                if tag == "select":
                    answer = get_answer(
                        question, job_context,
                        input_type="select", platform=self.name,
                    )
                    if answer:
                        options = input_el.query_selector_all("option")
                        for opt in options:
                            opt_text = opt.text_content().strip()
                            if answer.lower() in opt_text.lower() or opt_text.lower() in answer.lower():
                                input_el.select_option(label=opt_text)
                                answered += 1
                                break
                        else:
                            # Fallback: select first non-placeholder option
                            for opt in options:
                                val = opt.get_attribute("value") or ""
                                text = opt.text_content().strip()
                                if val and text and "select" not in text.lower():
                                    input_el.select_option(value=val)
                                    answered += 1
                                    break

                elif tag == "textarea":
                    current = input_el.input_value() or ""
                    if not current.strip():
                        answer = get_answer(
                            question, job_context,
                            input_type="textarea", platform=self.name,
                        )
                        if answer:
                            input_el.fill(answer)
                            answered += 1

                elif tag == "input":
                    input_type = (input_el.get_attribute("type") or "text").lower()
                    current = input_el.input_value() or ""

                    if input_type == "radio":
                        radios = group.query_selector_all("input[type='radio']")
                        answer = get_answer(
                            question, job_context,
                            input_type="radio", platform=self.name,
                        )
                        if answer:
                            for radio in radios:
                                radio_label = radio.evaluate(
                                    "el => el.closest('label')?.textContent || "
                                    "el.nextSibling?.textContent || ''"
                                )
                                if answer.lower() in (radio_label or "").lower():
                                    radio.click(force=True)
                                    answered += 1
                                    break

                    elif input_type == "checkbox":
                        all_cbs = group.query_selector_all("input[type='checkbox']")
                        if len(all_cbs) <= 1 and not input_el.is_checked():
                            answer = get_answer(
                                question, job_context,
                                input_type="checkbox", platform=self.name,
                            )
                            if not (answer and answer.lower() in ("no", "false")):
                                input_el.click(force=True)
                                answered += 1

                    elif input_type in ("text", "tel", "email", "number", "date", ""):
                        if not current.strip():
                            answer = get_answer(
                                question, job_context,
                                input_type=input_type, platform=self.name,
                            )
                            if answer:
                                input_el.fill(answer)
                                answered += 1

            except Exception as exc:
                logger.debug(
                    "%s: error answering screening question: %s",
                    self.name, exc,
                )
                continue

        if answered:
            logger.info("%s: answered %d screening questions", self.name, answered)
        return answered
