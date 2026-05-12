from jobpulse.job_scanners.totaljobs import scan_totaljobs
from jobpulse.models.application_models import SearchConfig
from shared.web_search import WebSearchHit


def test_scan_totaljobs_maps_search_results(monkeypatch):
    config = SearchConfig(titles=["Data Scientist"], location="London")
    monkeypatch.setattr(
        "shared.web_search.search_web",
        lambda *args, **kwargs: [
            WebSearchHit(
                title="Data Scientist - Acme - Totaljobs",
                url="https://www.totaljobs.com/job/data-scientist/acme-job123",
                snippet="London based ML role",
                source="duckduckgo_html",
            )
        ],
    )

    results = scan_totaljobs(config)

    assert len(results) == 1
    assert results[0]["platform"] == "totaljobs"
    assert results[0]["company"] == "Acme"
    assert results[0]["description"] == "London based ML role"
