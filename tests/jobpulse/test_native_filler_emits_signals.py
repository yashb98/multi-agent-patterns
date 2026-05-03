"""NativeFormFiller must emit OptimizationEngine signals for unverified fills,
so corrections from the form-fill path teach the same learning DBs as the
navigator path."""
from unittest.mock import patch, MagicMock
import pytest


class TestNativeFillerSignalEmission:
    def test_helper_function_exists(self):
        """The unified emission helper must be importable."""
        from jobpulse.native_form_filler import emit_form_fill_failures
        assert callable(emit_form_fill_failures)

    def test_helper_emits_one_signal_per_failure(self, monkeypatch):
        from jobpulse.native_form_filler import emit_form_fill_failures
        captured = []

        class FakeEngine:
            def emit(self, **kwargs):
                captured.append(kwargs)

        monkeypatch.setattr(
            "shared.optimization.get_optimization_engine",
            lambda: FakeEngine(),
        )
        failures = [
            {"label": "Email", "expected": "a@b.com", "actual": ""},
            {"label": "Phone", "expected": "555", "actual": "wrong"},
        ]
        emit_form_fill_failures(failures, domain="example.com")
        assert len(captured) == 2
        assert all(c["signal_type"] == "failure" for c in captured)
        assert all(c["source_loop"] == "form_filler" for c in captured)
        assert {c["payload"]["field"] for c in captured} == {"Email", "Phone"}

    def test_helper_no_signals_on_empty(self, monkeypatch):
        from jobpulse.native_form_filler import emit_form_fill_failures
        captured = []
        class FakeEngine:
            def emit(self, **kwargs):
                captured.append(kwargs)
        monkeypatch.setattr("shared.optimization.get_optimization_engine", lambda: FakeEngine())
        emit_form_fill_failures([], domain="example.com")
        assert len(captured) == 0
