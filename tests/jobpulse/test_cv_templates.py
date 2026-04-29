from __future__ import annotations


def test_normalize_linkedin_url_adds_https_and_www():
    from jobpulse.cv_templates import normalize_linkedin_url

    assert (
        normalize_linkedin_url("www.linkedin.com/in/yash-bishnoi")
        == "https://www.linkedin.com/in/yash-bishnoi"
    )


def test_normalize_linkedin_url_preserves_non_linkedin_urls():
    from jobpulse.cv_templates import normalize_linkedin_url

    assert normalize_linkedin_url("https://example.com/profile") == "https://example.com/profile"
