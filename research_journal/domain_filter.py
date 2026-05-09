"""Domain classifier — narrows raw paper feed to ML/LLM/SLM/VLM/finetune."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from jobpulse.papers.models import Paper
from research_journal.models import DomainTag
from shared.logging_config import get_logger

logger = get_logger(__name__)

_THRESHOLD_CORE = 0.65
_THRESHOLD_OUT = 0.70
_THRESHOLD_TANGENT = 0.60
_BORDERLINE_LOW = 0.55


class DomainClassifier:
    """Two-pass: embedding similarity (Pass 1) → LLM borderline (Pass 2)."""

    def __init__(self, anchors_path: Path | None = None) -> None:
        if anchors_path is None:
            anchors_path = Path(__file__).parent / "anchors" / "anchor_sets.json"
        data = json.loads(anchors_path.read_text())
        self.anchors_core: list[str] = data["core"]
        self.anchors_tangent: list[str] = data["tangent"]
        self.anchors_out: list[str] = data["out"]

    def classify(self, paper: Paper) -> tuple[DomainTag, float, str]:
        tag, conf, reason = self._pass1(paper)
        if tag is not None:
            return tag, conf, reason
        return self._pass2(paper)

    def _pass1(self, paper: Paper) -> tuple[DomainTag | None, float, str]:
        """Embedding similarity pass.  Returns (tag, conf, reason) or (None, …) to defer."""
        text = f"{paper.title}. {paper.abstract}"
        sim_core = self._max_cosine(text, self.anchors_core)
        sim_out = self._max_cosine(text, self.anchors_out)
        sim_tangent = self._max_cosine(text, self.anchors_tangent)

        if sim_core >= _THRESHOLD_CORE and sim_core > sim_out:
            return "core", sim_core, f"matched core anchor (sim={sim_core:.2f})"
        if sim_out >= _THRESHOLD_OUT and sim_out > sim_core:
            return "out", sim_out, f"matched reject anchor (sim={sim_out:.2f})"
        if _BORDERLINE_LOW <= sim_core < _THRESHOLD_CORE:
            return None, sim_core, "borderline — defer to LLM"
        if sim_tangent >= _THRESHOLD_TANGENT:
            return "tangent", sim_tangent, f"adjacent (sim={sim_tangent:.2f})"
        return "out", sim_core, f"below all thresholds (core={sim_core:.2f})"

    def _pass2(self, paper: Paper) -> tuple[DomainTag, float, str]:
        # Implemented in Task 6
        raise NotImplementedError

    def _max_cosine(self, text: str, anchors: list[str]) -> float:
        """Return the maximum cosine similarity between text and any anchor."""
        if not anchors:
            return 0.0
        return max(self._max_cosine_pair(text, anchor) for anchor in anchors)

    def _max_cosine_pair(self, text: str, anchor: str) -> float:
        from shared.memory_layer._embedder import embed_text
        import numpy as np

        v_text = np.asarray(embed_text(text), dtype=float)
        v_anchor = np.asarray(embed_text(anchor), dtype=float)
        denom = (np.linalg.norm(v_text) * np.linalg.norm(v_anchor)) or 1.0
        return float(np.dot(v_text, v_anchor) / denom)
