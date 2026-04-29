from jobpulse.job_scanners.totaljobs import scan_totaljobs
from jobpulse.job_scanners.indeed import scan_glassdoor
from jobpulse.models.application_models import SearchConfig
from shared.web_search import WebSearchHit


class _FakeRow:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return dict(self._payload)


class _FakeFrame:
    def __init__(self, rows):
        self._rows = rows

    def iterrows(self):
        for index, row in enumerate(self._rows):
            yield index, _FakeRow(row)


def test_scan_glassdoor_uses_jobspy_normalization(monkeypatch):
    monkeypatch.setattr(
        "jobpulse.job_scanners.indeed.scrape_jobs",
        lambda **kwargs: _FakeFrame([
            {
                "title": "Data Scientist",
                "company": "Acme",
                "location": "London",
                "description": "Model work",
                "job_url": "https://glassdoor.com/job/123",
            }
        ]),
    )

    results = scan_glassdoor(["Data Scientist"], "London", max_results=5)

    assert len(results) == 1
    assert results[0]["platform"] == "glassdoor"
    assert results[0]["job_id"]


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
