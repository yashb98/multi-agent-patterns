"""Tests for ChartGenerator — bar, line, radar chart rendering and LLM data extraction."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from jobpulse.papers.chart_generator import ChartGenerator
from jobpulse.papers.models import Chart, Paper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_paper(**kwargs) -> Paper:
    defaults = dict(
        arxiv_id="2401.00001",
        title="Benchmark Bonanza: Faster Transformers",
        authors=["Alice", "Bob"],
        abstract="Our method achieves 40% speedup on GLUE. We outperform GPT-4 on MMLU.",
        categories=["cs.AI"],
        pdf_url="https://arxiv.org/pdf/2401.00001",
        arxiv_url="https://arxiv.org/abs/2401.00001",
        published_at="2026-04-01",
    )
    defaults.update(kwargs)
    return Paper(**defaults)


def _make_mock_response(content: str) -> MagicMock:
    """Build a mock openai chat completion response."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ---------------------------------------------------------------------------
# TestDataExtraction
# ---------------------------------------------------------------------------

class TestDataExtraction:
    def test_extracts_benchmark_data(self, tmp_path):
        """LLM returns a valid chart spec → _extract_chart_data returns it."""
        spec = [
            {
                "chart_type": "bar_comparison",
                "title": "GLUE Benchmark",
                "description": "Comparison of models on GLUE.",
                "data": {"models": ["Ours", "GPT-4", "Baseline"], "scores": [92, 88, 75]},
            }
        ]
        mock_resp = _make_mock_response(
            '[{"chart_type":"bar_comparison","title":"GLUE Benchmark",'
            '"description":"Comparison of models on GLUE.",'
            '"data":{"models":["Ours","GPT-4","Baseline"],"scores":[92,88,75]}}]'
        )
        gen = ChartGenerator()
        with patch("jobpulse.papers.chart_generator._get_openai_client") as mock_client_fn:
            client = MagicMock()
            client.chat.completions.create.return_value = mock_resp
            mock_client_fn.return_value = client

            result = gen._extract_chart_data(_make_paper(), "Some notes about speedup.")

        assert len(result) == 1
        assert result[0]["chart_type"] == "bar_comparison"
        assert result[0]["data"]["models"] == ["Ours", "GPT-4", "Baseline"]

    def test_returns_empty_for_theoretical_paper(self):
        """LLM returns [] for a paper with no quantitative results."""
        mock_resp = _make_mock_response("[]")
        gen = ChartGenerator()
        with patch("jobpulse.papers.chart_generator._get_openai_client") as mock_client_fn:
            client = MagicMock()
            client.chat.completions.create.return_value = mock_resp
            mock_client_fn.return_value = client

            paper = _make_paper(abstract="We discuss philosophical foundations of attention.")
            result = gen._extract_chart_data(paper, "Purely theoretical.")

        assert result == []

    def test_handles_llm_error(self):
        """When the LLM call raises an exception, returns []."""
        gen = ChartGenerator()
        with patch("jobpulse.papers.chart_generator._get_openai_client") as mock_client_fn:
            client = MagicMock()
            client.chat.completions.create.side_effect = RuntimeError("API down")
            mock_client_fn.return_value = client

            result = gen._extract_chart_data(_make_paper(), "notes")

        assert result == []


# ---------------------------------------------------------------------------
# TestChartRendering
# ---------------------------------------------------------------------------

class TestChartRendering:
    def test_bar_comparison(self, tmp_path):
        """_render_bar creates a PNG file and returns a Chart."""
        gen = ChartGenerator()
        data = {"models": ["A", "B", "C"], "scores": [80, 90, 70]}
        chart = gen._render_bar("My Bar Chart", data, str(tmp_path))

        assert chart is not None
        assert chart.chart_type == "bar_comparison"
        assert chart.title == "My Bar Chart"
        assert os.path.isfile(chart.png_path)
        assert chart.png_path.endswith(".png")

    def test_line_scaling(self, tmp_path):
        """_render_line creates a PNG and returns a Chart."""
        gen = ChartGenerator()
        data = {"x": [1, 2, 4, 8], "y": [10, 18, 32, 60], "x_label": "Params (B)", "y_label": "Score"}
        chart = gen._render_line("Scaling Law", data, str(tmp_path))

        assert chart is not None
        assert chart.chart_type == "line_scaling"
        assert os.path.isfile(chart.png_path)

    def test_radar_multi(self, tmp_path):
        """_render_radar creates a PNG and returns a Chart."""
        gen = ChartGenerator()
        data = {
            "labels": ["Speed", "Accuracy", "Memory", "Robustness"],
            "series": [
                {"name": "Our Model", "values": [9, 8, 7, 8]},
                {"name": "Baseline", "values": [6, 7, 8, 6]},
            ],
        }
        chart = gen._render_radar("Model Comparison", data, str(tmp_path))

        assert chart is not None
        assert chart.chart_type == "radar_multi"
        assert os.path.isfile(chart.png_path)

    def test_bad_data_returns_none_bar(self, tmp_path):
        """Empty dict passed to _render_bar returns None."""
        gen = ChartGenerator()
        result = gen._render_bar("Empty", {}, str(tmp_path))
        assert result is None

    def test_bad_data_returns_none_line(self, tmp_path):
        """Empty dict passed to _render_line returns None."""
        gen = ChartGenerator()
        result = gen._render_line("Empty", {}, str(tmp_path))
        assert result is None

    def test_bad_data_returns_none_radar(self, tmp_path):
        """Empty dict passed to _render_radar returns None."""
        gen = ChartGenerator()
        result = gen._render_radar("Empty", {}, str(tmp_path))
        assert result is None

    def test_bar_mismatched_lengths_returns_none(self, tmp_path):
        """Bar chart with mismatched models/scores returns None."""
        gen = ChartGenerator()
        data = {"models": ["A", "B"], "scores": [80]}
        result = gen._render_bar("Mismatch", data, str(tmp_path))
        assert result is None

    def test_unknown_chart_type_returns_none(self, tmp_path):
        """_render_chart with unknown type returns None."""
        gen = ChartGenerator()
        result = gen._render_chart("pie_chart", "title", {}, str(tmp_path))
        assert result is None


# ---------------------------------------------------------------------------
# TestGenerateFullPipeline
# ---------------------------------------------------------------------------

class TestGenerateFullPipeline:
    def _bar_spec_json(self) -> str:
        return json_str([
            {
                "chart_type": "bar_comparison",
                "title": "Accuracy Comparison",
                "description": "Model accuracy.",
                "data": {"models": ["Ours", "Baseline"], "scores": [91, 82]},
            },
            {
                "chart_type": "line_scaling",
                "title": "Scaling Behaviour",
                "description": "Perf vs params.",
                "data": {"x": [1, 2, 4], "y": [50, 65, 80], "x_label": "Params", "y_label": "Acc"},
            },
        ])

    def test_generate_returns_charts(self, tmp_path):
        """generate() returns Chart objects when LLM provides specs."""
        import json
        specs = [
            {
                "chart_type": "bar_comparison",
                "title": "Accuracy Comparison",
                "description": "Model accuracy.",
                "data": {"models": ["Ours", "Baseline"], "scores": [91, 82]},
            },
        ]
        mock_resp = _make_mock_response(json.dumps(specs))
        gen = ChartGenerator()

        with patch("jobpulse.papers.chart_generator._get_openai_client") as mock_client_fn:
            client = MagicMock()
            client.chat.completions.create.return_value = mock_resp
            mock_client_fn.return_value = client

            charts = gen.generate(_make_paper(), "Great benchmark results.", output_dir=str(tmp_path))

        assert len(charts) == 1
        assert isinstance(charts[0], Chart)
        assert os.path.isfile(charts[0].png_path)

    def test_generate_empty_when_no_data(self, tmp_path):
        """generate() returns [] when LLM reports no chartable data."""
        mock_resp = _make_mock_response("[]")
        gen = ChartGenerator()

        with patch("jobpulse.papers.chart_generator._get_openai_client") as mock_client_fn:
            client = MagicMock()
            client.chat.completions.create.return_value = mock_resp
            mock_client_fn.return_value = client

            charts = gen.generate(_make_paper(), "Pure theory paper.", output_dir=str(tmp_path))

        assert charts == []

    def test_max_3_charts(self, tmp_path):
        """generate() never returns more than 3 charts even if LLM gives 5 specs."""
        import json
        specs = [
            {
                "chart_type": "bar_comparison",
                "title": f"Chart {i}",
                "description": "desc",
                "data": {"models": ["A", "B"], "scores": [i * 10, i * 8]},
            }
            for i in range(1, 6)
        ]
        mock_resp = _make_mock_response(json.dumps(specs))
        gen = ChartGenerator()

        with patch("jobpulse.papers.chart_generator._get_openai_client") as mock_client_fn:
            client = MagicMock()
            client.chat.completions.create.return_value = mock_resp
            mock_client_fn.return_value = client

            charts = gen.generate(_make_paper(), "lots of benchmarks", output_dir=str(tmp_path))

        assert len(charts) <= 3

    def test_generate_skips_bad_render(self, tmp_path):
        """generate() skips specs whose data is invalid (render returns None)."""
        import json
        specs = [
            # Bad spec (mismatched lengths)
            {
                "chart_type": "bar_comparison",
                "title": "Bad",
                "description": "bad",
                "data": {"models": ["A"], "scores": [1, 2]},
            },
            # Good spec
            {
                "chart_type": "bar_comparison",
                "title": "Good",
                "description": "good",
                "data": {"models": ["A", "B"], "scores": [80, 70]},
            },
        ]
        mock_resp = _make_mock_response(json.dumps(specs))
        gen = ChartGenerator()

        with patch("jobpulse.papers.chart_generator._get_openai_client") as mock_client_fn:
            client = MagicMock()
            client.chat.completions.create.return_value = mock_resp
            mock_client_fn.return_value = client

            charts = gen.generate(_make_paper(), "notes", output_dir=str(tmp_path))

        # Only the good spec should render successfully
        assert len(charts) == 1
        assert charts[0].title == "Good"


# ---------------------------------------------------------------------------
# Tiny helper to avoid polluting test bodies with json.dumps
# ---------------------------------------------------------------------------

def json_str(obj) -> str:  # noqa: ANN001
    import json
    return json.dumps(obj)
