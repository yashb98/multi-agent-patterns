import pytest
from jobpulse.papers.ranker import _paper_type_deboost


@pytest.mark.parametrize("paper_type,expected", [
    ("research", 0.0),
    ("survey", -1.0),
    ("tutorial", -1.5),
    ("position", -2.0),
    ("workshop", -1.5),
    ("unknown", 0.0),
])
def test_paper_type_deboost_table(paper_type, expected):
    assert _paper_type_deboost(paper_type) == pytest.approx(expected)
