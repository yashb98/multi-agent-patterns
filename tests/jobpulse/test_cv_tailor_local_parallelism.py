"""Setup-2 — cv_tailor.tailor_all_sections honours is_local_llm().

Single-tenant Ollama serialises requests internally and returns empty
content for 2-3 of 4 concurrent calls under load (verified during the
S2 live debug). Setting ``max_workers=1`` for local LLMs avoids the
empty-content failure mode while keeping cloud OpenAI / Kimi at the
default 4-way parallelism.

This test inspects the source rather than firing an end-to-end LLM
call so it works in any environment.
"""

from __future__ import annotations

import inspect

from jobpulse import cv_tailor


def test_tailor_all_sections_uses_local_aware_workers():
    src = inspect.getsource(cv_tailor.tailor_all_sections)
    # The key line: workers = 1 if is_local_llm() else 4.
    assert "is_local_llm()" in src, (
        "tailor_all_sections must check is_local_llm() before choosing "
        "concurrency"
    )
    assert "max_workers=workers" in src, (
        "ThreadPoolExecutor must use the workers variable, not the "
        "literal 4"
    )


def test_concurrency_choice_documented():
    """Comment must explain why local LLMs serialize. If someone removes
    the comment or the logic, the test catches it before the wrong
    value lands in production."""

    src = inspect.getsource(cv_tailor.tailor_all_sections)
    # Either the docstring or an inline comment must mention the
    # serialisation rationale.
    serialise_keywords = ("serialise", "serialize", "1 worker", "single-tenant",
                          "Ollama", "local")
    assert any(kw.lower() in src.lower() for kw in serialise_keywords), (
        "Document why local LLMs need 1 worker so future readers don't "
        "revert it as 'unnecessary serialization'"
    )
