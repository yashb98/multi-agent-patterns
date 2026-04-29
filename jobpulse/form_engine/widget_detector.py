"""WidgetLibraryDetector — dynamically detect which UI library a form element belongs to.

Detects React-Select, MUI Autocomplete, Ant Design Select, intl-tel-input,
SmartRecruiters spl-*, and other common widget libraries by inspecting
ancestor class names, shadow DOM hosts, and data attributes.

Usage:
    detector = WidgetLibraryDetector(page)
    lib = await detector.detect_for_field(field_locator)
    # lib → "react_select", "mui_autocomplete", etc.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


# ── Registry of known widget libraries ──
# Each entry defines detection indicators and interaction selectors.

WIDGET_LIBRARY_REGISTRY: dict[str, dict[str, Any]] = {
    "react_select": {
        "indicators": [
            ".select__control",
            ".select__menu",
            "[class*='react-select']",
        ],
        "ancestor_depth": 4,
        "option_selector": ".select__option",
        "value_selector": ".select__single-value",
        "menu_selector": ".select__menu",
        "clear_selector": ".select__clear-indicator",
        "interaction": "click_control_then_option",
    },
    "mui_autocomplete": {
        "indicators": [
            ".MuiAutocomplete-root",
            ".MuiAutocomplete-input",
            "[class*='MuiAutocomplete']",
        ],
        "ancestor_depth": 4,
        "option_selector": ".MuiAutocomplete-option",
        "value_selector": ".MuiAutocomplete-input",
        "menu_selector": ".MuiAutocomplete-listbox",
        "clear_selector": ".MuiAutocomplete-clearIndicator",
        "interaction": "type_then_option",
    },
    "ant_select": {
        "indicators": [
            ".ant-select",
            ".ant-select-dropdown",
            "[class*='ant-select']",
        ],
        "ancestor_depth": 4,
        "option_selector": ".ant-select-item-option-content",
        "value_selector": ".ant-select-selection-item",
        "menu_selector": ".ant-select-dropdown",
        "clear_selector": ".ant-select-clear",
        "interaction": "click_then_option",
    },
    "intl_tel_input": {
        "indicators": [
            ".iti__selected-country",
            ".iti__country-list",
            "[class*='iti__']",
        ],
        "ancestor_depth": 3,
        "search_selector": "[class*='iti__search-input']",
        "listbox_selector": "[class*='iti__country-listbox']",
        "option_selector": "[class*='iti__country']",
        "value_selector": ".iti__selected-country",
        "interaction": "open_search_then_option",
    },
    "smartrecruiters_spl": {
        "indicators": [
            "spl-",
            "[class^='spl-']",
        ],
        "ancestor_depth": 5,
        "option_selector": "[role='option']",
        "value_selector": "[role='combobox']",
        "interaction": "type_then_arrow_down_enter",
    },
    "workday_wd": {
        "indicators": [
            "[data-automation-id='formField']",
            "[data-automation-id='dropdownArrow']",
        ],
        "ancestor_depth": 3,
        "option_selector": "[data-automation-id='menuItem']",
        "value_selector": "[data-automation-id='formField']",
        "menu_selector": "[data-automation-id='menu']",
        "interaction": "click_then_option",
    },
    "greenhouse_custom": {
        "indicators": [
            ".application__field",
            ".select2",
        ],
        "ancestor_depth": 3,
        "option_selector": ".select2-results__option",
        "value_selector": ".select2-selection__rendered",
        "interaction": "click_then_option",
    },
}


class WidgetLibraryDetector:
    """Detect which UI library a form element or page uses."""

    def __init__(self, page: "Page") -> None:
        self._page = page
        self._page_cache: dict[str, bool] | None = None

    # ── Public API ──

    async def detect_for_field(self, field_locator: Any) -> str | None:
        """Detect widget library for a specific field locator.

        Checks ancestor elements up to the configured depth for library
        indicator class names or shadow DOM hosts.
        """
        try:
            if not await field_locator.count():
                return None
        except Exception:
            return None

        for lib_name, config in WIDGET_LIBRARY_REGISTRY.items():
            depth = config.get("ancestor_depth", 4)
            indicators = config.get("indicators", [])

            matched = await self._check_ancestors(field_locator, indicators, depth)
            if matched:
                logger.debug("WidgetDetector: field matched '%s'", lib_name)
                return lib_name

        return None

    async def detect_for_page(self) -> dict[str, str]:
        """Scan the entire page and return a mapping of field selectors → libraries.

        More efficient than calling detect_for_field() per field because
        it batches ancestor checks.
        """
        results: dict[str, str] = {}

        for lib_name, config in WIDGET_LIBRARY_REGISTRY.items():
            indicators = config.get("indicators", [])
            for indicator in indicators:
                try:
                    # Find all elements matching this indicator
                    locs = await self._page.locator(indicator).all()
                    for loc in locs:
                        try:
                            # Try to find the nearest input/combobox inside
                            inputs = await loc.locator("input, [role='combobox'], [role='textbox'], select").all()
                            for inp in inputs:
                                sel = await self._build_selector(inp)
                                if sel:
                                    results[sel] = lib_name
                        except Exception:
                            continue
                except Exception:
                    continue

        return results

    def get_config(self, library: str) -> dict[str, Any] | None:
        """Return the configuration dict for a detected library."""
        return WIDGET_LIBRARY_REGISTRY.get(library)

    # ── Internal ──

    async def _check_ancestors(
        self, locator: Any, indicators: list[str], max_depth: int
    ) -> bool:
        """Check if any ancestor of the locator matches any indicator."""
        try:
            # Fast path: check the element itself and immediate parent via JS
            indicator_list = ",".join(f'"{i}"' for i in indicators)
            result = await locator.evaluate(
                f"""(el, indicators, maxDepth) => {{
                    for (let d = 0; el && d <= maxDepth; d++) {{
                        for (const ind of indicators) {{
                            if (ind.startsWith('[') && ind.endsWith(']')) {{
                                // Attribute selector — use matches if supported
                                try {{ if (el.matches(ind)) return true; }} catch(e) {{}}
                            }} else if (ind.startsWith('.')) {{
                                // Class selector
                                const cls = ind.slice(1);
                                if (el.classList && el.classList.contains(cls)) return true;
                                if (el.className && el.className.includes(cls)) return true;
                            }} else if (ind.startsWith('#')) {{
                                if (el.id === ind.slice(1)) return true;
                            }} else if (el.tagName && el.tagName.toLowerCase().startsWith(ind.toLowerCase())) {{
                                // Tag/prefix match (e.g. "spl-")
                                return true;
                            }} else if (el.matches && el.matches(ind)) {{
                                return true;
                            }}
                        }}
                        // Shadow DOM host check
                        const root = el.getRootNode();
                        if (root && root.host) {{
                            el = root.host;
                            continue;
                        }}
                        el = el.parentElement;
                    }}
                    return false;
                }}""",
                [indicators, max_depth],
            )
            return bool(result)
        except Exception as exc:
            logger.debug("Ancestor check failed: %s", exc)
            return False

    @staticmethod
    async def _build_selector(locator: Any) -> str | None:
        """Build a CSS selector for a Playwright locator (best effort)."""
        try:
            return await locator.evaluate(
                """el => {
                    if (el.id) return '#' + el.id;
                    if (el.name) return '[name="' + el.name + '"]';
                    const cls = el.className?.split(' ')?.filter(c => c)?.[0];
                    if (cls) return el.tagName.toLowerCase() + '.' + cls;
                    return el.tagName.toLowerCase();
                }"""
            )
        except Exception:
            return None


# ── Convenience helpers ──

def get_widget_config(library: str | None) -> dict[str, Any] | None:
    """Return configuration for a widget library name."""
    if not library:
        return None
    return WIDGET_LIBRARY_REGISTRY.get(library)


def list_supported_libraries() -> list[str]:
    """Return all supported widget library names."""
    return list(WIDGET_LIBRARY_REGISTRY.keys())
