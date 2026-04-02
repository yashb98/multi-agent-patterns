"""BlogPipeline — 6-agent pipeline to generate blog posts from research papers."""

from __future__ import annotations

import base64
import re
import urllib.parse
from datetime import datetime, timezone

from jobpulse.papers.chart_generator import ChartGenerator
from jobpulse.papers.models import BlogPost, Chart, Paper
from shared.logging_config import get_logger

logger = get_logger(__name__)


def _get_openai_client():  # pragma: no cover
    """Return an OpenAI client, or None if the key is not configured."""
    try:
        from jobpulse.config import OPENAI_API_KEY

        if not OPENAI_API_KEY:
            return None
        from openai import OpenAI

        return OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        return None


def _llm_call(
    system: str,
    user: str,
    max_tokens: int = 2500,
    temperature: float = 0.3,
) -> str:
    """Make a single LLM call and return the response text.

    Returns empty string on failure.
    """
    client = _get_openai_client()
    if client is None:
        logger.warning("_llm_call: no OpenAI client configured")
        return ""
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        logger.warning("_llm_call: LLM call failed (%s)", exc)
        return ""


class BlogPipeline:
    """6-agent pipeline that generates a BlogPost from a Paper.

    Agents:
        1. DeepRead   — extracts research notes
        2. WriteGRPO  — generates 3 drafts, picks best (GRPO-style selection)
        3. FactCheck  — verifies key claims
        4. Revise     — applies fact-check flags
        5. Charts     — generates charts via ChartGenerator
        6. Diagram    — generates Mermaid architecture diagram
        7. Edit       — final polish and placeholder replacement
    """

    def __init__(self) -> None:
        self.chart_gen = ChartGenerator()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def generate(self, paper: Paper, output_dir: str = "/tmp") -> BlogPost:
        """Orchestrate all 7 agents and return a BlogPost.

        On LLM failure at any agent the pipeline continues with empty/fallback
        values so callers always receive a valid BlogPost object.
        """
        logger.info("BlogPipeline.generate: start for paper %s", paper.arxiv_id)

        # Agent 1 — research notes
        notes = self._deep_read(paper)

        # Agent 2 — GRPO draft selection
        draft, grpo_score = self._write_grpo(paper, notes)

        # Agent 3 — fact check
        fc_passed, flags = self._fact_check(draft, paper)

        # Agent 4 — revise
        if flags:
            draft = self._revise(draft, flags, paper)

        # Agent 5 — charts
        charts = self._generate_charts(paper, notes, output_dir)

        # Agent 6 — diagram
        mermaid_code, diagram_url = self._generate_diagram(notes, paper)

        # Agent 7 — final edit
        final_content = self._edit(draft, paper, diagram_url, charts)

        title = self._extract_title(final_content, paper)

        from jobpulse.papers.models import FactCheckResult

        fact_check_result: FactCheckResult | None = None
        if not fc_passed and flags:
            fact_check_result = FactCheckResult(
                score=0.0,
                total_claims=len(flags),
                verified_count=0,
                issues=flags,
                explanation="Fact check flagged issues; draft was revised.",
            )

        blog_post = BlogPost(
            title=title,
            content=final_content,
            charts=charts,
            mermaid_code=mermaid_code,
            diagram_url=diagram_url,
            word_count=len(final_content.split()),
            grpo_score=grpo_score,
            fact_check=fact_check_result,
            paper=paper,
            generated_at=datetime.now(tz=timezone.utc).isoformat(),
        )

        logger.info(
            "BlogPipeline.generate: done for paper %s — %d words, grpo=%.2f",
            paper.arxiv_id,
            blog_post.word_count,
            grpo_score,
        )
        return blog_post

    # ------------------------------------------------------------------
    # Agent 1: DeepRead
    # ------------------------------------------------------------------

    def _deep_read(self, paper: Paper) -> str:
        """Generate structured research notes from the paper.

        If paper.model_card_summary is available it is included as extra
        context so the notes reflect real-world deployment details.
        """
        extra = ""
        if paper.model_card_summary:
            extra = f"\n\nModel card summary:\n{paper.model_card_summary}"

        system = (
            "You are a technical research analyst. "
            "Read the paper abstract carefully and extract structured research notes "
            "covering: (1) core contribution, (2) methodology, (3) key results, "
            "(4) limitations, (5) practical implications."
        )
        user = (
            f"Paper: {paper.title}\n"
            f"Authors: {', '.join(paper.authors)}\n"
            f"Abstract:\n{paper.abstract}"
            f"{extra}\n\n"
            "Write concise research notes (300-500 words)."
        )
        notes = _llm_call(system, user, max_tokens=800, temperature=0.3)
        if not notes:
            notes = f"Research notes for: {paper.title}\n\nAbstract: {paper.abstract}"
        return notes

    # ------------------------------------------------------------------
    # Agent 2: WriteGRPO
    # ------------------------------------------------------------------

    def _write_grpo(self, paper: Paper, notes: str) -> tuple[str, float]:
        """Generate 3 draft blog posts at different temperatures, return the best."""
        temps = [0.5, 0.7, 0.9]
        system = (
            "You are a technical blog writer. "
            "Write a complete, engaging blog post about the research paper below. "
            "Include: a title (# heading), introduction, methodology section, "
            "results section, implications section, and conclusion. "
            "Target 600-900 words. Use clear subheadings."
        )
        user = (
            f"Paper: {paper.title}\n"
            f"Authors: {', '.join(paper.authors)}\n\n"
            f"Research notes:\n{notes}"
        )

        best_draft = ""
        best_score = -1.0

        for temp in temps:
            draft = _llm_call(system, user, max_tokens=1500, temperature=temp)
            if not draft:
                continue
            score = self._score_blog(draft, paper)
            if score > best_score:
                best_score = score
                best_draft = draft

        if not best_draft:
            # Fallback: minimal draft
            best_draft = (
                f"# {paper.title}\n\n"
                f"*By {', '.join(paper.authors)}*\n\n"
                f"{paper.abstract}\n"
            )
            best_score = 0.0

        return best_draft, best_score

    def _score_blog(self, draft: str, paper: Paper) -> float:
        """Heuristic scoring of a blog draft (0–10).

        Criteria:
        - Word count: ideal 600-900 → up to 4.0 pts
        - Section presence (##): up to 3.0 pts (1 pt per section up to 3)
        - No unfilled placeholders ([...]): -2.0 if found
        - Paper title mentioned: +1.0
        - Has conclusion-like ending: +2.0
        """
        score = 0.0
        words = len(draft.split())

        # Word count scoring
        if 600 <= words <= 900:
            score += 4.0
        elif 400 <= words < 600 or 900 < words <= 1200:
            score += 2.5
        elif words > 1200:
            score += 1.5
        elif words > 200:
            score += 1.0

        # Section headings
        headings = len(re.findall(r"^##\s+", draft, re.MULTILINE))
        score += min(headings, 3) * 1.0

        # Placeholder penalty
        if re.search(r"\[.*?\]", draft):
            score -= 2.0

        # Title mention
        title_words = paper.title.lower().split()[:3]
        if any(w in draft.lower() for w in title_words):
            score += 1.0

        # Conclusion marker
        if re.search(r"(conclusion|in summary|in closing|to summarize)", draft, re.IGNORECASE):
            score += 2.0

        return max(score, 0.0)

    # ------------------------------------------------------------------
    # Agent 3: FactCheck
    # ------------------------------------------------------------------

    def _fact_check(self, draft: str, paper: Paper) -> tuple[bool, list[str]]:
        """Fact-check the draft using shared.fact_checker when available.

        Returns (passed, flags) where flags is a list of issue strings.
        Falls back to a lightweight LLM-based check if shared.fact_checker
        is not importable.
        """
        try:
            from shared.fact_checker import check_claims  # type: ignore[import]

            result = check_claims(draft, reference_text=paper.abstract)
            flags = result.get("issues", [])
            passed = len(flags) == 0
            return passed, flags
        except (ImportError, Exception):
            pass

        # Lightweight LLM fallback
        system = (
            "You are a fact-checker. Given a blog post draft and the original paper abstract, "
            "identify any factual inaccuracies or unsupported claims. "
            "Return a JSON array of issue strings, or an empty array [] if everything checks out. "
            "Return ONLY the JSON array."
        )
        user = (
            f"Paper abstract:\n{paper.abstract}\n\n"
            f"Blog draft (first 1500 chars):\n{draft[:1500]}"
        )
        raw = _llm_call(system, user, max_tokens=400, temperature=0.1)
        flags: list[str] = []
        if raw:
            try:
                import json

                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    flags = [str(f) for f in parsed]
            except Exception:
                pass

        return len(flags) == 0, flags

    # ------------------------------------------------------------------
    # Agent 4: Revise
    # ------------------------------------------------------------------

    def _revise(self, draft: str, flags: list[str], paper: Paper) -> str:
        """Revise the draft to fix fact-check flags."""
        if not flags:
            return draft

        issues_text = "\n".join(f"- {f}" for f in flags)
        system = (
            "You are a technical editor. Revise the blog post to fix the factual issues listed. "
            "Keep the structure and style intact. Return only the revised blog post."
        )
        user = (
            f"Original paper title: {paper.title}\n\n"
            f"Issues to fix:\n{issues_text}\n\n"
            f"Blog post draft:\n{draft}"
        )
        revised = _llm_call(system, user, max_tokens=1800, temperature=0.3)
        return revised if revised else draft

    # ------------------------------------------------------------------
    # Agent 5: Charts
    # ------------------------------------------------------------------

    def _generate_charts(self, paper: Paper, notes: str, output_dir: str) -> list[Chart]:
        """Delegate chart generation to ChartGenerator."""
        try:
            return self.chart_gen.generate(paper, notes, output_dir)
        except Exception as exc:
            logger.warning("_generate_charts: failed for paper %s (%s)", paper.arxiv_id, exc)
            return []

    # ------------------------------------------------------------------
    # Agent 6: Diagram
    # ------------------------------------------------------------------

    def _generate_diagram(self, notes: str, paper: Paper) -> tuple[str, str]:
        """Generate a Mermaid architecture diagram for the paper.

        Returns (mermaid_code, diagram_url).
        Uses mermaid.ink for rendering.
        """
        system = (
            "You are a technical diagram designer. "
            "Given research notes about an AI paper, produce a Mermaid flowchart diagram "
            "that illustrates the key architecture or methodology. "
            "Use 'flowchart TD' syntax. Keep it concise (max 10 nodes). "
            "Return ONLY the raw Mermaid code, no markdown fences."
        )
        user = (
            f"Paper: {paper.title}\n\n"
            f"Notes:\n{notes[:800]}"
        )
        mermaid_code = _llm_call(system, user, max_tokens=400, temperature=0.3)
        if not mermaid_code:
            mermaid_code = (
                "flowchart TD\n"
                f"    A[Input] --> B[{paper.title[:40]}]\n"
                "    B --> C[Output]"
            )

        # Encode to mermaid.ink URL
        try:
            encoded = base64.urlsafe_b64encode(mermaid_code.encode()).decode()
            diagram_url = f"https://mermaid.ink/img/{encoded}"
        except Exception:
            diagram_url = ""

        return mermaid_code, diagram_url

    # ------------------------------------------------------------------
    # Agent 7: Edit (final polish)
    # ------------------------------------------------------------------

    def _edit(
        self,
        draft: str,
        paper: Paper,
        diagram_url: str,
        charts: list[Chart],
    ) -> str:
        """Final polish: replace placeholders, inject diagram/chart links, tighten prose."""
        # Build chart references to append
        chart_section = ""
        if charts:
            chart_section = "\n\n## Figures\n\n"
            for chart in charts:
                chart_section += f"**{chart.title}** — {chart.description}\n\n"

        diagram_section = ""
        if diagram_url:
            diagram_section = (
                f"\n\n## Architecture Diagram\n\n"
                f"![Architecture diagram]({diagram_url})\n"
            )

        system = (
            "You are a senior technical editor. Polish the blog post: "
            "fix any placeholder text (e.g. [INSERT ...]), improve flow, "
            "ensure consistent tone. Do not change the factual content. "
            "Return only the polished blog post."
        )
        user = (
            f"Blog post draft:\n{draft}"
            f"{chart_section}"
            f"{diagram_section}"
        )
        polished = _llm_call(system, user, max_tokens=2000, temperature=0.2)
        if not polished:
            # Fallback: append sections to raw draft
            polished = draft + chart_section + diagram_section

        return polished

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_title(self, content: str, paper: Paper) -> str:
        """Extract the blog post title from the first # heading, or use paper title."""
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if match:
            return match.group(1).strip()
        return paper.title
