"""Fill dropdown/select elements — native <select> and custom React widgets."""

from __future__ import annotations

import re as _re

from shared.logging_config import get_logger

from jobpulse.form_engine.models import FillResult

logger = get_logger(__name__)

# Common abbreviation→full mappings for fuzzy matching
_ABBREVIATIONS: dict[str, str] = {
    "uk": "united kingdom",
    "gb": "united kingdom",
    "+44": "united kingdom",
    "44": "united kingdom",
    "us": "united states",
    "usa": "united states of america",
}


def _normalize(text: str) -> str:
    """Lowercase, strip whitespace and punctuation for comparison."""
    return text.lower().strip().strip(".,;:!?")


def _token_overlap(a: str, b: str) -> float:
    """Jaccard similarity of token sets after normalization."""
    # Strip possessives ('s) before tokenizing so "bachelor's" matches "bachelor"
    a_clean = _re.sub(r"'s?\b", '', a.lower().strip())
    b_clean = _re.sub(r"'s?\b", '', b.lower().strip())
    tokens_a = set(_re.split(r'[\s\-_/,;:]+', a_clean))
    tokens_b = set(_re.split(r'[\s\-_/,;:]+', b_clean))
    tokens_a.discard('')
    tokens_b.discard('')
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _numeric_range_match(value: str, option: str) -> bool:
    """Check if numeric values/ranges in both strings overlap."""
    nums_v = [int(n) for n in _re.findall(r'\d+', value)]
    nums_o = [int(n) for n in _re.findall(r'\d+', option)]
    if not nums_v or not nums_o:
        return False
    # Detect "less than N" in option — treat as upper-bound range (0, N-1)
    if _re.search(r'\bless\s+than\b|\bunder\b|\bbelow\b', option.lower()):
        upper = nums_o[0] - 1
        range_o = (0, upper)
        range_v = (min(nums_v), max(nums_v))
        return range_v[0] <= range_o[1] and range_o[0] <= range_v[1]
    # If both have the same first number, likely a match (e.g., "3-5" vs "3 to 5")
    if nums_v[0] == nums_o[0]:
        return True
    # Check range overlap
    range_v = (min(nums_v), max(nums_v))
    range_o = (min(nums_o), max(nums_o))
    return range_v[0] <= range_o[1] and range_o[0] <= range_v[1]


def _fuzzy_match_option(value: str, options: list[str]) -> str | None:
    """Find the best matching option for a value.

    Priority: exact → abbreviation → startswith → numeric range → token overlap → contains → None.
    Numeric range is checked before contains to avoid substring false positives
    (e.g., "1 year" is a substring of "Less than 1 year" but should match "1-2 years").
    """
    norm_value = _normalize(value)

    # Check abbreviation expansion
    expanded = _ABBREVIATIONS.get(norm_value, norm_value)

    for opt in options:
        if _normalize(opt) == expanded:
            return opt

    for opt in options:
        if _normalize(opt).startswith(expanded):
            return opt

    # Tier 3: Numeric range match (before contains to avoid substring false positives)
    nums_in_value = _re.findall(r'\d+', expanded)
    if nums_in_value:
        for opt in options:
            if _numeric_range_match(expanded, _normalize(opt)):
                return opt

    # Tier 4: Token overlap (handles word reordering, formatting differences)
    best_overlap = 0.0
    best_match = None
    for opt in options:
        score = _token_overlap(expanded, _normalize(opt))
        if score > best_overlap:
            best_overlap = score
            best_match = opt
    if best_overlap >= 0.5 and best_match is not None:
        return best_match

    # Tier 5: Substring contains (broad fallback)
    for opt in options:
        if expanded in _normalize(opt):
            return opt

    return None


async def fill_select(
    page,
    selector: str,
    value: str,
    timeout: int = 5000,
) -> FillResult:
    """Fill a native <select> element by matching visible option text.

    Tries exact match first, then fuzzy match (abbreviations, startswith, contains).
    """
    try:
        element = await page.query_selector(selector)
        if element is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value, error=f"Element {selector} not found",
            )

        # Check if disabled/readonly
        disabled = await element.get_attribute("disabled")
        if disabled is not None:
            return FillResult(
                success=True, selector=selector,
                value_attempted=value, skipped=True,
            )

        # Get available options
        options = await page.eval_on_selector_all(
            f"{selector} option",
            "els => els.map(e => e.textContent.trim())",
        )

        if not options:
            # Might be async-loaded — wait and retry
            await page.wait_for_timeout(2000)
            options = await page.eval_on_selector_all(
                f"{selector} option",
                "els => els.map(e => e.textContent.trim())",
            )

        if not options:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value, error="No options found in select",
            )

        # Find the best match
        match = _fuzzy_match_option(value, options)
        if match is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value,
                error=f"No matching option for '{value}' in {options[:5]}",
            )

        await page.select_option(selector, label=match)
        verified_text = await page.eval_on_selector(
            selector, "el => el.options[el.selectedIndex]?.text?.trim() || ''"
        )
        logger.debug("select_filler: filled %s with '%s'", selector, match)
        return FillResult(
            success=True, selector=selector,
            value_attempted=value, value_set=match,
            value_verified=(_normalize(verified_text) == _normalize(match)),
        )

    except Exception as exc:
        logger.error("select_filler: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=value, error=str(exc),
        )


async def fill_custom_select(
    page,
    trigger_selector: str,
    value: str,
    options_selector: str = "[role='option'], li",
    timeout: int = 5000,
) -> FillResult:
    """Fill a custom React/JS dropdown widget.

    Flow: click trigger → wait for options panel → fuzzy match → click option.
    """
    try:
        trigger = await page.query_selector(trigger_selector)
        if trigger is None:
            return FillResult(
                success=False, selector=trigger_selector,
                value_attempted=value, error=f"Trigger {trigger_selector} not found",
            )

        # Click to open the dropdown
        await trigger.scroll_into_view_if_needed()
        await trigger.click()
        await page.wait_for_timeout(500)

        # Try typing to filter if there's a search input inside
        search_input = await page.query_selector(
            f"{trigger_selector} input, [role='combobox'] input"
        )
        if search_input:
            await search_input.fill(value)
            await page.wait_for_timeout(1000)  # wait for debounce

        # Get visible options
        option_els = await page.query_selector_all(options_selector)
        option_texts = []
        for el in option_els:
            text = await el.text_content()
            if text and text.strip():
                option_texts.append((text.strip(), el))

        if not option_texts:
            return FillResult(
                success=False, selector=trigger_selector,
                value_attempted=value, error="No options visible after opening dropdown",
            )

        # Fuzzy match
        texts_only = [t for t, _ in option_texts]
        match = _fuzzy_match_option(value, texts_only)
        if match is None:
            # Press Escape to close and report failure
            await page.keyboard.press("Escape")
            return FillResult(
                success=False, selector=trigger_selector,
                value_attempted=value,
                error=f"No matching option for '{value}' in {texts_only[:5]}",
            )

        # Click the matching option
        for text, el in option_texts:
            if text == match:
                await el.click()
                break

        logger.debug("custom_select: filled %s with '%s'", trigger_selector, match)
        return FillResult(
            success=True, selector=trigger_selector,
            value_attempted=value, value_set=match,
            value_verified=True,
        )

    except Exception as exc:
        logger.error("custom_select: error filling %s: %s", trigger_selector, exc)
        return FillResult(
            success=False, selector=trigger_selector,
            value_attempted=value, error=str(exc),
        )
