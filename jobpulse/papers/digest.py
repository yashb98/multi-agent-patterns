"""DigestBuilder — formats ranked papers into Telegram messages."""

from __future__ import annotations

from jobpulse.papers.models import RankedPaper


class DigestBuilder:
    """Formats RankedPaper lists into Telegram-ready daily and weekly digests."""

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def format_daily(
        self,
        papers: list[RankedPaper],
        digest_date: str = "",
    ) -> str:
        """Return a numbered Telegram message for a daily paper digest.

        Args:
            papers: Ranked and fact-checked papers (highest score first).
            digest_date: Optional ISO date string shown in the header.

        Returns:
            Formatted message string.  Empty string when *papers* is empty.
        """
        if not papers:
            return "No papers today."

        header_date = f" — {digest_date}" if digest_date else ""
        lines: list[str] = [f"📄 *Daily AI Papers{header_date}*\n"]

        for idx, paper in enumerate(papers, start=1):
            lines.append(self._format_daily_entry(idx, paper))

        lines.append(self._command_hints_daily())
        return "\n".join(lines)

    def format_weekly(
        self,
        papers: list[RankedPaper],
        themes: list[str],
        start_date: str = "",
        end_date: str = "",
    ) -> str:
        """Return a Telegram message summarising a week of papers.

        Args:
            papers: All ranked papers for the week.
            themes: High-level theme strings extracted by the ranker.
            start_date: Optional ISO start date for the header.
            end_date: Optional ISO end date for the header.

        Returns:
            Formatted message string.
        """
        date_range = ""
        if start_date and end_date:
            date_range = f" ({start_date} – {end_date})"
        elif start_date:
            date_range = f" ({start_date})"

        lines: list[str] = [f"📚 *Weekly AI Papers Digest{date_range}*\n"]

        # Stats line
        total = len(papers)
        lines.append(f"📊 *{total} paper{'s' if total != 1 else ''} this week*\n")

        # Themes section
        if themes:
            lines.append("🔬 *Themes*")
            for theme in themes:
                lines.append(f"• {theme}")
            lines.append("")

        # Top papers (up to 5)
        if papers:
            lines.append("🏆 *Top Papers*")
            for idx, paper in enumerate(papers[:5], start=1):
                lines.append(self._format_weekly_entry(idx, paper))

        lines.append(self._command_hints_weekly())
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    def _format_daily_entry(self, idx: int, paper: RankedPaper) -> str:
        """Format a single paper entry for the daily digest."""
        parts: list[str] = []

        tag = f"[{paper.category_tag}]" if paper.category_tag else ""
        score_str = f"{paper.impact_score:.1f}"
        parts.append(f"*{idx}. {paper.title}* {tag} — ⭐ {score_str}")

        # Authors (max 2 shown)
        if paper.authors:
            shown = paper.authors[:2]
            suffix = " et al." if len(paper.authors) > 2 else ""
            parts.append(f"   _{', '.join(shown)}{suffix}_")

        # Key technique
        if paper.key_technique:
            parts.append(f"   🔧 {paper.key_technique}")

        # Summary
        if paper.summary:
            parts.append(f"   {paper.summary}")

        # Fact-check score
        if paper.fact_check is not None:
            fc_score = paper.fact_check.score
            parts.append(f"   ✅ Fact-check: {fc_score:.3f}/10")

        # HuggingFace signals
        hf_parts: list[str] = []
        if paper.hf_upvotes is not None:
            hf_parts.append(f"👍 {paper.hf_upvotes} upvotes")
        if paper.linked_models:
            n = len(paper.linked_models)
            hf_parts.append(f"🤗 {n} model{'s' if n != 1 else ''}")
        if hf_parts:
            parts.append(f"   {' · '.join(hf_parts)}")

        # S2 citations
        if paper.s2_citation_count > 0:
            parts.append(f"   📊 {paper.s2_citation_count} citations")

        # Source attribution
        if paper.sources:
            source_names = {"huggingface": "HuggingFace", "hackernews": "HackerNews",
                           "reddit": "Reddit", "bluesky": "Bluesky",
                           "semantic_scholar": "Semantic Scholar", "arxiv_rss": "arXiv",
                           "arxiv": "arXiv"}
            names = [source_names.get(s, s) for s in paper.sources]
            parts.append(f"   📡 Found on: {', '.join(names)}")

        # Links
        links: list[str] = []
        if paper.arxiv_url:
            links.append(f"[arXiv]({paper.arxiv_url})")
        if paper.pdf_url:
            links.append(f"[PDF]({paper.pdf_url})")
        if paper.github_url:
            gh_label = f"GitHub ⭐{paper.github_stars}" if paper.github_stars else "GitHub"
            links.append(f"[{gh_label}]({paper.github_url})")
        if links:
            parts.append(f"   {' · '.join(links)}")

        parts.append("")  # blank line between entries
        return "\n".join(parts)

    def _format_weekly_entry(self, idx: int, paper: RankedPaper) -> str:
        """Format a compact single paper entry for the weekly digest."""
        tag = f"[{paper.category_tag}]" if paper.category_tag else ""
        score_str = f"{paper.impact_score:.1f}"
        line = f"{idx}. *{paper.title}* {tag} ⭐ {score_str}"

        hf_parts: list[str] = []
        if paper.hf_upvotes is not None:
            hf_parts.append(f"👍 {paper.hf_upvotes}")
        if paper.linked_models:
            n = len(paper.linked_models)
            hf_parts.append(f"🤗 {n} model{'s' if n != 1 else ''}")
        if hf_parts:
            line += f"  {' · '.join(hf_parts)}"

        if paper.arxiv_url:
            line += f"  [arXiv]({paper.arxiv_url})"

        return line

    @staticmethod
    def _command_hints_daily() -> str:
        return (
            "\n💬 *Commands:* `paper <n>` · `blog <n>` · `read <n>` · `papers stats`"
        )

    @staticmethod
    def _command_hints_weekly() -> str:
        return (
            "\n💬 *Commands:* `paper <n>` · `blog <n>` · `papers stats` · `weekly report`"
        )
