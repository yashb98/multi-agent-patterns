"""Tests for jobpulse.form_scanner — a11y-tree form discovery."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpulse.form_scanner import (
    FormField,
    FormScanResult,
    best_option_match,
    best_range_match,
    scan_form,
    scan_combobox_options,
    select_combobox_option,
)


# ── FormField dataclass ──


class TestFormField:
    def test_empty_field_is_empty(self):
        f = FormField(label="Name", role="textbox")
        assert f.is_empty is True

    def test_filled_field_not_empty(self):
        f = FormField(label="Name", role="textbox", value="Yash")
        assert f.is_empty is False

    def test_required_empty_needs_fill(self):
        f = FormField(label="Name", role="textbox", required=True)
        assert f.needs_fill is True

    def test_required_filled_no_fill(self):
        f = FormField(label="Name", role="textbox", required=True, value="Yash")
        assert f.needs_fill is False

    def test_to_dict(self):
        f = FormField(label="City", role="combobox", value="London", required=True)
        d = f.to_dict()
        assert d["label"] == "City"
        assert d["role"] == "combobox"
        assert d["value"] == "London"
        assert d["required"] is True


# ── FormScanResult ──


class TestFormScanResult:
    def test_required_empty(self):
        fields = [
            FormField(label="A", role="textbox", required=True),
            FormField(label="B", role="textbox", required=True, value="val"),
            FormField(label="C", role="combobox", required=False),
        ]
        scan = FormScanResult(fields=fields)
        assert len(scan.required_empty) == 1
        assert scan.required_empty[0].label == "A"

    def test_field_types(self):
        fields = [
            FormField(label="A", role="textbox"),
            FormField(label="B", role="combobox"),
            FormField(label="C", role="textbox"),
        ]
        scan = FormScanResult(fields=fields)
        assert scan.field_types == ["combobox", "textbox"]

    def test_summary(self):
        fields = [FormField(label="Name", role="textbox", required=True)]
        scan = FormScanResult(fields=fields)
        s = scan.summary()
        assert "1 fields" in s
        assert "Name" in s


# ── best_option_match ──


class TestBestOptionMatch:
    def test_exact_match(self):
        assert best_option_match("Male", ["Female", "Male", "Other"]) == "Male"

    def test_case_insensitive(self):
        assert best_option_match("male", ["Female", "Male", "Other"]) == "Male"

    def test_alias_match(self):
        aliases = {"he/him": ("Him/His/Himself",)}
        result = best_option_match(
            "He/Him",
            ["Her/Hers/Herself", "Him/His/Himself", "They/Their/Themselves"],
            aliases=aliases,
        )
        assert result == "Him/His/Himself"

    def test_substring_match(self):
        result = best_option_match("Indian", ["Asian or Asian British - Indian", "White"])
        assert result == "Asian or Asian British - Indian"

    def test_no_match(self):
        assert best_option_match("Klingon", ["Male", "Female"]) is None

    def test_empty_options(self):
        assert best_option_match("Male", []) is None


# ── best_range_match ──


class TestBestRangeMatch:
    def test_matches_salary_range(self):
        options = ["£20,000 - £30,000", "£30,000 - £40,000", "£40,000 - £50,000"]
        assert best_range_match(35000, options) == "£30,000 - £40,000"

    def test_matches_age_range(self):
        options = ["18 - 24", "25 - 34", "35 - 44"]
        assert best_range_match(27, options) == "25 - 34"

    def test_no_match_out_of_range(self):
        options = ["£20,000 - £30,000", "£30,000 - £40,000"]
        assert best_range_match(50000, options) is None

    def test_boundary_inclusive(self):
        options = ["£40,000 - £50,000"]
        assert best_range_match(40000, options) == "£40,000 - £50,000"
        assert best_range_match(50000, options) == "£40,000 - £50,000"


# ── scan_form (mocked CDP) ──


def _make_ax_nodes():
    return [
        {"role": {"value": "RootWebArea"}, "name": {"value": "Test Form"}, "properties": []},
        {"role": {"value": "heading"}, "name": {"value": "Personal Info"}, "properties": []},
        {
            "role": {"value": "textbox"},
            "name": {"value": "First name"},
            "value": {"value": "Yash"},
            "properties": [
                {"name": "required", "value": {"value": True}},
                {"name": "invalid", "value": {"value": "false"}},
            ],
        },
        {
            "role": {"value": "combobox"},
            "name": {"value": "Gender"},
            "value": {"value": ""},
            "properties": [
                {"name": "required", "value": {"value": True}},
                {"name": "invalid", "value": {"value": "true"}},
            ],
        },
        {
            "role": {"value": "checkbox"},
            "name": {"value": "I agree"},
            "value": {"value": ""},
            "properties": [
                {"name": "required", "value": {"value": False}},
            ],
        },
        {"role": {"value": "generic"}, "name": {"value": "wrapper"}, "properties": []},
        {"role": {"value": "InlineTextBox"}, "name": {"value": "text"}, "properties": []},
    ]


def _make_mock_scanner_page(ax_nodes):
    """Build a mock page suitable for scan_form — sync frame(), async CDP."""
    page = MagicMock()
    page.url = "https://example.com/apply"
    page.frame = MagicMock(return_value=None)
    page.frames = [MagicMock(url="about:blank")]
    page.main_frame = page.frames[0]

    cdp = AsyncMock()
    cdp.send = AsyncMock(return_value={"nodes": ax_nodes})
    cdp.detach = AsyncMock()
    page.context.new_cdp_session = AsyncMock(return_value=cdp)
    return page


class TestScanForm:
    def test_parses_fields_from_ax_tree(self):
        page = _make_mock_scanner_page(_make_ax_nodes())

        scan = asyncio.get_event_loop().run_until_complete(scan_form(page))

        assert scan.page_title == "Test Form"
        assert len(scan.fields) == 3
        assert scan.fields[0].label == "First name"
        assert scan.fields[0].value == "Yash"
        assert scan.fields[0].required is True
        assert scan.fields[1].label == "Gender"
        assert scan.fields[1].invalid is True
        assert scan.fields[2].label == "I agree"
        assert scan.headings == ["Personal Info"]

    def test_skips_structural_roles(self):
        page = _make_mock_scanner_page(_make_ax_nodes())

        scan = asyncio.get_event_loop().run_until_complete(scan_form(page))
        roles = {f.role for f in scan.fields}
        assert "generic" not in roles
        assert "InlineTextBox" not in roles

    def test_required_empty_list(self):
        page = _make_mock_scanner_page(_make_ax_nodes())

        scan = asyncio.get_event_loop().run_until_complete(scan_form(page))
        req_empty = scan.required_empty
        assert len(req_empty) == 1
        assert req_empty[0].label == "Gender"

    def test_deduplicates_fields(self):
        nodes = _make_ax_nodes()
        nodes.append(nodes[2])
        page = _make_mock_scanner_page(nodes)

        scan = asyncio.get_event_loop().run_until_complete(scan_form(page))
        labels = [f.label for f in scan.fields]
        assert labels.count("First name") == 1


# ── scan_combobox_options (mocked) ──


class TestScanComboboxOptions:
    def test_reads_options_from_ax_tree(self):
        page = MagicMock()
        combo = AsyncMock()
        combo.count = AsyncMock(return_value=1)
        combo.click = AsyncMock()
        combo.fill = AsyncMock()
        combo.press = AsyncMock()
        page.get_by_role = MagicMock(return_value=combo)

        cdp = AsyncMock()
        cdp.send = AsyncMock(return_value={
            "nodes": [
                {"role": {"value": "option"}, "name": {"value": "Male"}, "properties": []},
                {"role": {"value": "option"}, "name": {"value": "Female"}, "properties": []},
                {"role": {"value": "option"}, "name": {"value": "Other"}, "properties": []},
            ]
        })
        cdp.detach = AsyncMock()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)

        options = asyncio.get_event_loop().run_until_complete(
            scan_combobox_options(page, "Gender")
        )
        assert options == ["Male", "Female", "Other"]

    def test_returns_empty_on_no_combobox(self):
        page = MagicMock()
        combo = AsyncMock()
        combo.count = AsyncMock(return_value=0)
        page.get_by_role = MagicMock(return_value=combo)

        options = asyncio.get_event_loop().run_until_complete(
            scan_combobox_options(page, "NonExistent")
        )
        assert options == []


# ── select_combobox_option (mocked) ──


class TestSelectComboboxOption:
    def test_selects_exact_match(self):
        page = MagicMock()
        combo = AsyncMock()
        combo.count = AsyncMock(return_value=1)
        combo.click = AsyncMock()
        combo.fill = AsyncMock()
        combo.press = AsyncMock()
        page.get_by_role = MagicMock(return_value=combo)

        cdp = AsyncMock()
        cdp.send = AsyncMock(return_value={
            "nodes": [
                {"role": {"value": "option"}, "name": {"value": "Male"}, "properties": []},
                {"role": {"value": "option"}, "name": {"value": "Female"}, "properties": []},
            ]
        })
        cdp.detach = AsyncMock()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)

        option_loc = AsyncMock()
        option_loc.count = AsyncMock(return_value=1)
        option_loc.first = AsyncMock()
        option_loc.first.click = AsyncMock()

        def mock_get_by_role(role, **kwargs):
            if role == "option":
                return option_loc
            return combo

        page.get_by_role = MagicMock(side_effect=mock_get_by_role)

        result = asyncio.get_event_loop().run_until_complete(
            select_combobox_option(page, "Gender", "Male")
        )
        assert result["success"] is True
        assert result["selected"] == "Male"


# ── scan_form with container_backend_node_id ──


@pytest.mark.asyncio
async def test_scan_form_uses_partial_tree_when_container_provided():
    """When a container_backend_node_id is provided, scan_form should
    call getPartialAXTree instead of getFullAXTree."""
    from jobpulse.form_scanner import scan_form

    mock_page = AsyncMock()
    mock_page.url = "https://greenhouse.io/apply"
    mock_page.frame = MagicMock(return_value=None)
    mock_page.context = MagicMock()
    mock_page.frames = [mock_page]
    mock_page.main_frame = mock_page

    mock_cdp = AsyncMock()
    mock_page.context.new_cdp_session = AsyncMock(return_value=mock_cdp)

    mock_cdp.send = AsyncMock(return_value={"nodes": [
        {"nodeId": "1", "role": {"value": "RootWebArea"}, "name": {"value": "Apply"}, "properties": []},
        {"nodeId": "2", "role": {"value": "textbox"}, "name": {"value": "First Name"}, "properties": [
            {"name": "required", "value": {"value": True}}
        ]},
        {"nodeId": "3", "role": {"value": "textbox"}, "name": {"value": "Last Name"}, "properties": []},
    ]})

    result = await scan_form(mock_page, container_backend_node_id="42")

    mock_cdp.send.assert_called_once_with(
        "Accessibility.getPartialAXTree",
        {"backendNodeId": 42, "fetchRelatives": True},
    )
    assert len(result.fields) == 2
    assert result.fields[0].label == "First Name"
    assert result.fields[1].label == "Last Name"


@pytest.mark.asyncio
async def test_scan_form_falls_back_to_full_tree_on_partial_failure():
    """If getPartialAXTree fails, fall back to getFullAXTree."""
    from jobpulse.form_scanner import scan_form

    mock_page = AsyncMock()
    mock_page.url = "https://example.com/apply"
    mock_page.frame = MagicMock(return_value=None)
    mock_page.context = MagicMock()
    mock_page.frames = [mock_page]
    mock_page.main_frame = mock_page

    mock_cdp = AsyncMock()
    mock_page.context.new_cdp_session = AsyncMock(return_value=mock_cdp)

    call_count = 0
    async def mock_send(method, params=None):
        nonlocal call_count
        call_count += 1
        if method == "Accessibility.getPartialAXTree":
            raise Exception("Not supported")
        return {"nodes": [
            {"nodeId": "1", "role": {"value": "RootWebArea"}, "name": {"value": "Apply"}, "properties": []},
            {"nodeId": "2", "role": {"value": "textbox"}, "name": {"value": "Email"}, "properties": []},
        ]}

    mock_cdp.send = mock_send

    result = await scan_form(mock_page, container_backend_node_id="99")
    assert len(result.fields) == 1
    assert result.fields[0].label == "Email"


# ── resolve_form_container ──


@pytest.mark.asyncio
async def test_resolve_container_tier1_learned(tmp_path):
    """Tier 1: returns stored container from FormExperienceDB."""
    from jobpulse.form_experience_db import FormExperienceDB
    from jobpulse.ats_adapters.strategy import get_strategy
    from jobpulse.form_engine.field_scanner import resolve_form_container

    db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
    db.store_container("greenhouse.io", "#application")

    mock_page = AsyncMock()
    mock_page.url = "https://greenhouse.io/apply/123"
    mock_locator = AsyncMock()
    mock_locator.count = AsyncMock(return_value=1)
    mock_page.locator = MagicMock(return_value=mock_locator)

    strategy = get_strategy("greenhouse")
    result = await resolve_form_container(mock_page, strategy, db)
    assert result == "#application"


@pytest.mark.asyncio
async def test_resolve_container_tier1_stale_falls_to_tier3(tmp_path):
    """Tier 1 selector returns 0 elements -> deletes it -> falls to Tier 3 hint."""
    from jobpulse.form_experience_db import FormExperienceDB
    from jobpulse.ats_adapters.strategy import get_strategy
    from jobpulse.form_engine.field_scanner import resolve_form_container

    db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
    db.store_container("greenhouse.io", "#old-form-gone")

    mock_page = AsyncMock()
    mock_page.url = "https://greenhouse.io/apply/123"
    stale_locator = AsyncMock()
    stale_locator.count = AsyncMock(return_value=0)
    hint_locator = AsyncMock()
    hint_locator.count = AsyncMock(return_value=1)

    def mock_locator_fn(selector):
        if selector == "#old-form-gone":
            return stale_locator
        if selector == "#application":
            return hint_locator
        return stale_locator

    mock_page.locator = mock_locator_fn
    mock_page.evaluate = AsyncMock(return_value=None)

    strategy = get_strategy("greenhouse")
    result = await resolve_form_container(mock_page, strategy, db)
    assert result == "#application"
    assert db.get_container("greenhouse.io") is None


@pytest.mark.asyncio
async def test_resolve_container_returns_none_when_all_fail(tmp_path):
    """All tiers fail -> returns None for full-page scan."""
    from jobpulse.form_experience_db import FormExperienceDB
    from jobpulse.ats_adapters.strategy import get_strategy
    from jobpulse.form_engine.field_scanner import resolve_form_container

    db = FormExperienceDB(db_path=str(tmp_path / "test.db"))

    mock_page = AsyncMock()
    mock_page.url = "https://unknown-ats.com/apply"

    empty_locator = AsyncMock()
    empty_locator.count = AsyncMock(return_value=0)
    mock_page.locator = MagicMock(return_value=empty_locator)
    mock_page.evaluate = AsyncMock(return_value=None)

    strategy = get_strategy("generic")
    result = await resolve_form_container(mock_page, strategy, db)
    assert result is None
