"""S12 audit regression tests.

Covers fixes shipped in commit `fix(ats_adapters): S12 audit ...`:

- M-A — `discovery._URL_PATTERNS` must include reed.co.uk so URL-only platform
  detection agrees with `jd_analyzer.detect_ats_platform`.
- M-1 / M-5 — FormExperienceDB lookup failures inside the synthesis path
  (`synthesize_strategy_for_domain`) and the registry fallback
  (`get_strategy`) must surface as `logger.warning`, not silently
  return None / fall through to GenericStrategy.
- M-2 / M-3 / M-4 — `LearnedStrategy` form_container_hint /
  expected_field_range / extra_label_mappings must `logger.warning`
  on FE DB lookup failure.
- m-1 — `get_adapter()` must take no platform parameter (covered in
  `tests/test_adapter_screening_wiring.py`; mirrored here for locality).
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

# Import the package once to populate _STRATEGY_REGISTRY.
import jobpulse.ats_adapters  # noqa: F401


# ── M-A — Reed URL pattern parity ──


def test_reed_url_classified_via_url_only_path():
    """reed.co.uk URLs must classify as 'reed' from URL alone.

    `applicator._infer_platform_from_url` calls `detect_platform_from_url`
    (URL only, snapshot=None). Pre-fix this returned 'generic' for Reed,
    disagreeing with `jd_analyzer.detect_ats_platform` ('reed') and breaking
    platform-tagged telemetry on Reed jobs.
    """
    from jobpulse.ats_adapters.discovery import detect_platform_from_url

    assert detect_platform_from_url("https://www.reed.co.uk/jobs/123") == "reed"
    assert detect_platform_from_url("https://reed.co.uk/jobs/abc") == "reed"


def test_reed_detection_agrees_with_jd_analyzer():
    """Discovery and jd_analyzer must agree on reed.co.uk so applicator's
    `ats_platform` (from discovery) and PlaywrightAdapter's recomputed
    platform (from jd_analyzer) carry the same value through telemetry."""
    from jobpulse.ats_adapters.discovery import detect_platform_from_url
    from jobpulse.jd_analyzer import detect_ats_platform

    url = "https://www.reed.co.uk/jobs/some-role/12345678"
    assert detect_platform_from_url(url) == detect_ats_platform(url) == "reed"


# ── M-1 — synthesize_strategy_for_domain logs warning on FE failure ──


def test_synthesize_warns_on_fe_lookup_failure(caplog):
    from jobpulse.ats_adapters import _strategy_synthesis

    class _BoomFE:
        def lookup(self, domain):
            raise RuntimeError("simulated FE corruption")

    with patch.object(_strategy_synthesis, "_get_fe_db", return_value=_BoomFE()):
        with caplog.at_level(logging.WARNING, logger="jobpulse.ats_adapters._strategy_synthesis"):
            result = _strategy_synthesis.synthesize_strategy_for_domain("https://example.com/jobs/1")

    assert result is None
    assert any(
        "FormExperienceDB.lookup failed" in rec.getMessage() and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), "synthesis FE failure must surface as logger.warning"


# ── M-5 — get_strategy logs warning when synthesis raises ──


def test_get_strategy_warns_when_synthesis_raises(caplog):
    """Forces an exception inside `synthesize_strategy_for_domain` to confirm
    `get_strategy`'s outer except surfaces it at WARNING."""
    from jobpulse.ats_adapters import strategy as strategy_mod

    with patch(
        "jobpulse.ats_adapters._strategy_synthesis.synthesize_strategy_for_domain",
        side_effect=RuntimeError("simulated synthesis crash"),
    ):
        with caplog.at_level(logging.WARNING, logger="jobpulse.ats_adapters.strategy"):
            s = strategy_mod.get_strategy("not_in_registry", url="https://example.com/jobs/1")

    assert s.name == "generic", "synthesis failure must fall back to GenericStrategy"
    assert any(
        "synthesis failed" in rec.getMessage() and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), "synthesis failure must surface as logger.warning"


# ── M-2 / M-3 / M-4 — LearnedStrategy FE lookups warn on failure ──


@pytest.mark.parametrize(
    "method_name,fe_method",
    [
        ("form_container_hint", "get_container"),
        ("expected_field_range", "get_field_mappings"),
        ("extra_label_mappings", "get_field_mappings"),
    ],
)
def test_learned_strategy_warns_on_fe_failure(caplog, method_name, fe_method):
    from jobpulse.ats_adapters import learned_strategy

    class _BoomFE:
        def __getattr__(self, name):
            def boom(*_a, **_kw):
                raise RuntimeError(f"simulated FE failure on {name}")
            return boom

    with patch.object(learned_strategy, "_get_fe_db", return_value=_BoomFE()):
        s = learned_strategy.LearnedStrategy(domain="example.com", apply_count=5)
        with caplog.at_level(logging.WARNING, logger="jobpulse.ats_adapters.learned_strategy"):
            getattr(s, method_name)()

    assert any(
        f"{method_name} FE lookup failed" in rec.getMessage() and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), f"LearnedStrategy.{method_name} must logger.warning on FE failure (FE method: {fe_method})"


# ── m-1 — get_adapter takes no platform parameter ──


def test_get_adapter_no_platform_parameter():
    import inspect
    from jobpulse.ats_adapters import get_adapter

    sig = inspect.signature(get_adapter)
    assert len(sig.parameters) == 0, (
        "get_adapter must not accept a platform parameter — adapter dispatch is "
        "unified post-2026-04 (single PlaywrightAdapter). Per-platform behavior "
        "lives in BasePlatformStrategy via get_strategy(platform, url)."
    )
