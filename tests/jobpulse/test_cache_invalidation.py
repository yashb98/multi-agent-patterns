"""Tests for PageReasoner.invalidate(snapshot)."""
from jobpulse.page_analysis.page_reasoner import PageReasoner, PageAction


def _snap(url="https://example.com/page"):
    return {
        "url": url, "page_text_preview": "hello world",
        "dialog_text": "", "fields": [], "buttons": [],
    }


class TestInvalidate:
    def test_invalidate_removes_matching_entry(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        action = PageAction(
            page_understanding="x", action="fill_form", target_text="",
            reasoning="t", confidence=0.9, page_type="application_form",
        )
        snap = _snap()
        key = pr._cache_key(snap["url"], snap["page_text_preview"], snap["dialog_text"],
                            snap["fields"], snap["buttons"])
        pr._set_cache(key, action)
        # Confirm cached
        assert pr._get_cached(key) is not None
        # Invalidate via public API
        removed = pr.invalidate(snap)
        assert removed == 1
        assert pr._get_cached(key) is None

    def test_invalidate_no_entry_returns_zero(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        assert pr.invalidate(_snap()) == 0

    def test_invalidate_handles_missing_keys(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        # Snapshot with only minimal data should not crash
        assert pr.invalidate({"url": "https://example.com"}) == 0
