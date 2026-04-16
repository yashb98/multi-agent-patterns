"""ChartGenerator — extract chartable data from papers and render matplotlib charts."""

from __future__ import annotations

import json
import os
import re
import uuid

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from openai import OpenAI

from jobpulse.config import OPENAI_API_KEY
from jobpulse.papers.models import Chart, Paper
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Dark theme constants
BG_COLOR = "#1e1e2e"
TEXT_COLOR = "#cdd6f4"
ACCENT_COLOR = "#1a5276"
GRID_COLOR = "#313244"
DPI = 150


def _get_openai_client() -> OpenAI:
    """Return an OpenAI client. Isolated for easy mocking in tests."""
    return OpenAI(api_key=OPENAI_API_KEY)


class ChartGenerator:
    """Extracts chartable data from research papers and renders matplotlib charts."""

    def generate(self, paper: Paper, research_notes: str, output_dir: str = "/tmp") -> list[Chart]:
        """Extract chart data from paper and render up to 3 charts.

        Args:
            paper: The paper to generate charts for.
            research_notes: Research notes / digest text.
            output_dir: Directory to save PNG files.

        Returns:
            List of Chart objects (at most 3).
        """
        chart_specs = self._extract_chart_data(paper, research_notes)
        charts: list[Chart] = []
        for spec in chart_specs[:3]:
            chart = self._render_chart(
                chart_type=spec.get("chart_type", ""),
                title=spec.get("title", ""),
                data=spec.get("data", {}),
                output_dir=output_dir,
            )
            if chart is not None:
                charts.append(chart)
        logger.info("Generated %d charts for paper %s", len(charts), paper.arxiv_id)
        return charts

    def _extract_chart_data(self, paper: Paper, notes: str) -> list[dict]:
        """Call LLM to extract structured chart specs from abstract + notes.

        Returns [] on error or when no chartable data is found.
        """
        prompt = (
            "You are a research paper analyst. Given the paper abstract and notes below, "
            "extract up to 3 charts that can visualise the key findings.\n\n"
            f"Title: {paper.title}\n"
            f"Abstract: {paper.abstract}\n"
            f"Notes: {notes}\n\n"
            "Return a JSON array (can be empty []) of chart specifications. "
            "Each spec must have:\n"
            '  "chart_type": one of "bar_comparison", "line_scaling", "radar_multi"\n'
            '  "title": short descriptive title\n'
            '  "description": one sentence description\n'
            '  "data": object matching the chart type:\n'
            '    bar_comparison  -> {"models": [...], "scores": [...]}\n'
            '    line_scaling    -> {"x": [...], "y": [...], "x_label": "...", "y_label": "..."}\n'
            '    radar_multi     -> {"labels": [...], "series": [{"name": "...", "values": [...]}]}\n\n'
            "Return ONLY the JSON array, no markdown fences, no explanation."
        )

        try:
            client = _get_openai_client()
            response = client.chat.completions.create(
                model="gpt-5-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            raw = response.choices[0].message.content or "[]"
            # Strip any accidental markdown fences
            raw = re.sub(r"```[a-z]*\n?", "", raw).strip().rstrip("`").strip()
            specs = json.loads(raw)
            if not isinstance(specs, list):
                logger.warning("LLM returned non-list chart specs, ignoring")
                return []
            return specs  # type: ignore[return-value]
        except Exception as exc:
            logger.error("Chart data extraction failed: %s", exc)
            return []

    def _render_chart(self, chart_type: str, title: str, data: dict, output_dir: str) -> Chart | None:
        """Dispatch to the appropriate renderer based on chart_type."""
        dispatch = {
            "bar_comparison": self._render_bar,
            "line_scaling": self._render_line,
            "radar_multi": self._render_radar,
        }
        renderer = dispatch.get(chart_type)
        if renderer is None:
            logger.warning("Unknown chart type: %s", chart_type)
            return None
        return renderer(title, data, output_dir)

    # ------------------------------------------------------------------
    # Individual renderers
    # ------------------------------------------------------------------

    def _render_bar(self, title: str, data: dict, output_dir: str) -> Chart | None:
        """Render a bar comparison chart.

        Expected data: {"models": [...], "scores": [...]}
        """
        models = data.get("models")
        scores = data.get("scores")
        if not models or not scores or len(models) != len(scores):
            logger.warning("Invalid bar chart data: %s", data)
            return None

        try:
            fig, ax = plt.subplots(figsize=(8, 5), facecolor=BG_COLOR)
            ax.set_facecolor(BG_COLOR)

            x = np.arange(len(models))
            bars = ax.bar(x, scores, color=ACCENT_COLOR, width=0.6, edgecolor=TEXT_COLOR, linewidth=0.5)

            # Value labels on top of bars
            for bar, score in zip(bars, scores):
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max(scores) * 0.01,
                    f"{score}",
                    ha="center",
                    va="bottom",
                    color=TEXT_COLOR,
                    fontsize=9,
                )

            ax.set_xticks(x)
            ax.set_xticklabels(models, color=TEXT_COLOR, fontsize=9)
            ax.set_ylabel("Score", color=TEXT_COLOR, fontsize=10)
            ax.set_title(title, color=TEXT_COLOR, fontsize=12, pad=12)
            ax.tick_params(colors=TEXT_COLOR)
            ax.spines[:].set_color(GRID_COLOR)
            ax.yaxis.label.set_color(TEXT_COLOR)
            ax.grid(axis="y", color=GRID_COLOR, linestyle="--", linewidth=0.5)

            png_path = os.path.join(output_dir, f"chart_{uuid.uuid4().hex}.png")
            fig.savefig(png_path, dpi=DPI, bbox_inches="tight", facecolor=BG_COLOR)
            plt.close(fig)

            return Chart(
                chart_type="bar_comparison",
                title=title,
                data=data,
                png_path=png_path,
                description=f"Bar comparison chart: {title}",
            )
        except Exception as exc:
            logger.error("Bar chart render failed: %s", exc)
            plt.close("all")
            return None

    def _render_line(self, title: str, data: dict, output_dir: str) -> Chart | None:
        """Render a line scaling chart.

        Expected data: {"x": [...], "y": [...], "x_label": "...", "y_label": "..."}
        """
        x_vals = data.get("x")
        y_vals = data.get("y")
        x_label = data.get("x_label", "x")
        y_label = data.get("y_label", "y")
        if not x_vals or not y_vals or len(x_vals) != len(y_vals):
            logger.warning("Invalid line chart data: %s", data)
            return None

        try:
            fig, ax = plt.subplots(figsize=(8, 5), facecolor=BG_COLOR)
            ax.set_facecolor(BG_COLOR)

            ax.plot(x_vals, y_vals, color=ACCENT_COLOR, linewidth=2, marker="o", markersize=5,
                    markerfacecolor=TEXT_COLOR)
            ax.set_xlabel(x_label, color=TEXT_COLOR, fontsize=10)
            ax.set_ylabel(y_label, color=TEXT_COLOR, fontsize=10)
            ax.set_title(title, color=TEXT_COLOR, fontsize=12, pad=12)
            ax.tick_params(colors=TEXT_COLOR)
            ax.spines[:].set_color(GRID_COLOR)
            ax.grid(color=GRID_COLOR, linestyle="--", linewidth=0.5)

            png_path = os.path.join(output_dir, f"chart_{uuid.uuid4().hex}.png")
            fig.savefig(png_path, dpi=DPI, bbox_inches="tight", facecolor=BG_COLOR)
            plt.close(fig)

            return Chart(
                chart_type="line_scaling",
                title=title,
                data=data,
                png_path=png_path,
                description=f"Line scaling chart: {title}",
            )
        except Exception as exc:
            logger.error("Line chart render failed: %s", exc)
            plt.close("all")
            return None

    def _render_radar(self, title: str, data: dict, output_dir: str) -> Chart | None:
        """Render a radar/spider chart.

        Expected data: {"labels": [...], "series": [{"name": "...", "values": [...]}]}
        """
        labels = data.get("labels")
        series = data.get("series")
        if not labels or not series:
            logger.warning("Invalid radar chart data: %s", data)
            return None

        # Validate series
        for s in series:
            if not isinstance(s, dict) or "values" not in s:
                logger.warning("Invalid radar series item: %s", s)
                return None
            if len(s["values"]) != len(labels):
                logger.warning("Radar series length mismatch")
                return None

        try:
            n = len(labels)
            angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
            angles += angles[:1]  # close the polygon

            fig, ax = plt.subplots(figsize=(6, 6), facecolor=BG_COLOR,
                                   subplot_kw={"polar": True})
            ax.set_facecolor(BG_COLOR)

            palette = [ACCENT_COLOR, "#e74c3c", "#2ecc71", "#f39c12", "#9b59b6"]
            for idx, s in enumerate(series):
                values = list(s["values"]) + [s["values"][0]]
                color = palette[idx % len(palette)]
                ax.plot(angles, values, color=color, linewidth=2, label=s.get("name", ""))
                ax.fill(angles, values, color=color, alpha=0.15)

            ax.set_thetagrids(np.degrees(angles[:-1]), labels, color=TEXT_COLOR, fontsize=9)
            ax.set_title(title, color=TEXT_COLOR, fontsize=12, pad=20)
            ax.tick_params(colors=TEXT_COLOR)
            ax.spines["polar"].set_color(GRID_COLOR)
            ax.grid(color=GRID_COLOR, linestyle="--", linewidth=0.5)
            ax.set_facecolor(BG_COLOR)

            if len(series) > 1:
                legend = ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1),
                                   labelcolor=TEXT_COLOR, framealpha=0)
                for text in legend.get_texts():
                    text.set_color(TEXT_COLOR)

            png_path = os.path.join(output_dir, f"chart_{uuid.uuid4().hex}.png")
            fig.savefig(png_path, dpi=DPI, bbox_inches="tight", facecolor=BG_COLOR)
            plt.close(fig)

            return Chart(
                chart_type="radar_multi",
                title=title,
                data=data,
                png_path=png_path,
                description=f"Radar chart: {title}",
            )
        except Exception as exc:
            logger.error("Radar chart render failed: %s", exc)
            plt.close("all")
            return None
