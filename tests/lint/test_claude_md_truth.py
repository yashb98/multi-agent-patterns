"""Pattern-test for `pipeline-bugs.md` Section 6 contract lies.

Closes the audit-doc-drift bug class: every change to architecture docs
(`CLAUDE.md`, `docs/job-application-pipeline.md`, sub-`CLAUDE.md` files) must
keep them in sync with code reality. This test enforces three families of
checks:

  1. **Forbidden phrases** — claims the audit found in the docs that *no
     longer match the code*. The fix is to remove or rewrite the phrase.

  2. **Qualified claims** — true-but-misleading statements that must remain
     accompanied by a qualifier elsewhere in the same file (e.g. "ALL memory
     access goes through MemoryManager" is fine *as long as* the
     cognitive/_classifier exception is also documented).

  3. **Source-file annotation truth** — type annotations or docstrings whose
     stated contract diverges from what the function actually does.

When this test fails, the message points to the offending audit row in
`docs/audits/pipeline-bugs.md` so the fix has a paper trail.

The test is intentionally string-based (no AST) so contributors can paste a
new entry in seconds when a future audit surfaces another contract lie.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _root() -> Path:
    # tests/lint/test_claude_md_truth.py -> repo root
    return Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (_root() / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Forbidden phrases — the fix is to remove these from the doc.
# ---------------------------------------------------------------------------

# (relative_path, forbidden_substring, audit_id, why_it_lies)
FORBIDDEN_PHRASES: list[tuple[str, str, str, str]] = [
    (
        "CLAUDE.md",
        "Verify 3 self-adaptation layers",
        "S3 doc-1",
        "Cognitive Escalation was the third layer; it was deleted from the "
        "navigator in the 2026-05-07 audit. Only 2 layers remain "
        "(CorrectionCapture, strategy_reflector). Cognitive escalation now "
        "runs in-line during form fill via _escalate_fill, not post-apply.",
    ),
    (
        "jobpulse/CLAUDE.md",
        "screening_defaults()",
        "S12 D-12.2",
        "BasePlatformStrategy.screening_defaults was deliberately removed "
        "(PII policy — answers come from ScreeningPipeline, not the strategy). "
        "Listing it as a strategy method misleads contributors about the "
        "platform-strategy contract.",
    ),
    (
        "docs/job-application-pipeline.md",
        "└──▶ ③ CognitiveEngine.flush() ──▶ EscalationClassifier",
        "S6 D-1 / S3 doc-1",
        "Diagram showed CognitiveEngine.flush() as a third self-adaptation "
        "layer, but flush is a write-back of queued strategy templates, not "
        "an adaptation layer. Cognitive escalation runs in-line during form "
        "fill via _escalate_fill.",
    ),
    (
        "docs/job-application-pipeline.md",
        "verify_submission` as a separate",
        "S3 doc-3",
        "_navigator.verify_submission was wired by _bind_compat_aliases but "
        "is not called in the apply path — only the SubmissionVerifier "
        "inside NativeFormFiller runs. Treating verify_submission as a "
        "separate post-submit verifier in the pipeline doc would mislead "
        "contributors.",
    ),
    (
        "docs/job-application-pipeline.md",
        "OverlayDismisser as the single source",
        "S3 doc-4",
        "Legacy cookie_dismisser.dismiss + dismiss_cookie_banner_playwright "
        "paths run in production; OverlayDismisser non-LinkedIn methods are "
        "D-tagged. Calling OverlayDismisser the 'single source of truth' "
        "for overlay dismissal would be a contract lie.",
    ),
]


# ---------------------------------------------------------------------------
# 2. Qualified claims — phrase must remain, but a qualifier MUST appear too.
# ---------------------------------------------------------------------------

# (relative_path, primary_phrase, required_qualifier, audit_id, why)
QUALIFIED_CLAIMS: list[tuple[str, str, str, str, str]] = [
    (
        "shared/memory_layer/CLAUDE.md",
        "6-signal decay",
        "AutonomousLinker",
        "S11 D-11.1",
        "3 of 6 forgetting signals (connectivity/impact/uniqueness) depend "
        "on AutonomousLinker.link_with_neighbors having populated Neo4j "
        "edges. Until that wiring lands (M-11.A), those 3 return defaults "
        "and compute_decay is half-functional. The qualifier must be "
        "present whenever 6-signal decay is mentioned.",
    ),
    (
        "jobpulse/CLAUDE.md",
        "3-engine memory stack",
        "get_procedural_entries",
        "S11 D-11.2",
        "The claim is correct for the WRITE path. The READ path is "
        "asymmetric: get_procedural_entries / get_episodic_entries still "
        "read from JSON-only legacy stores while SQLite has 19 800 / 200 "
        "rows respectively. Cognitive consumers see ~1/4 of procedural "
        "memory until M-11.C lands.",
    ),
    (
        "shared/CLAUDE.md",
        "All memory access goes through MemoryManager",
        "_classifier",
        "S11 D-11.3 / S6 W-2",
        "True except for shared/cognitive/_classifier.py reaching into "
        "memory.semantic.facts.items() directly because MemoryManager has "
        "no public query_facts_by_domain accessor. Documenting the "
        "exception prevents future contributors from copying the pattern.",
    ),
    (
        "shared/optimization/CLAUDE.md",
        "transfer",
        "No aggregator detector",
        "S10 D-10.1",
        "transfer signal type was added to VALID_SIGNAL_TYPES but no "
        "aggregator pattern-detection rule consumes it. Producer fires; "
        "consumer is dormant. Documenting the gap so contributors know "
        "to wire one before adding more transfer producers.",
    ),
    (
        "docs/job-application-pipeline.md",
        "BasePlatformStrategy",
        "6 are reachable",
        "S12 D-12.1",
        "Of 17 declared strategy methods, only 6 are reachable in the "
        "default apply path. The rest are FormFillEngine-only (B-tier; "
        "UNIFIED_FORM_ENGINE not set in prod) or D-tier dead.",
    ),
    (
        "docs/job-application-pipeline.md",
        "Gate 4B",
        "before Gate 4B",
        "S8 D-3",
        "PDF generation runs before Gate 4B in scan_pipeline.generate_materials, "
        "so a Needs-Review verdict still leaves the rendered PDF on disk. "
        "Architecture doc must call this out.",
    ),
    (
        "docs/job-application-pipeline.md",
        "lazy",
        "ensure_tailored_cv_for_job",
        "S8 D-1",
        "Two CV-generation paths exist: eager via generate_materials and "
        "lazy via application_materials.ensure_tailored_cv_for_job (used "
        "by live-review and handle_apply_review). Mentioning 'lazy' in the "
        "doc requires naming the lazy entrypoint.",
    ),
    (
        "docs/job-application-pipeline.md",
        "process_single_url",
        "skips Gate 0",
        "S7 W-2",
        "process_single_url skips Gate 0 + Gate 4A relative to the cron "
        "path. Architecture doc must document the asymmetry so manual URL "
        "submitters know which gates they're bypassing.",
    ),
    (
        "docs/job-application-pipeline.md",
        "forced_level_overrides",
        "agent_name",
        "S10 D-10.2",
        "cognitive_outcomes is keyed by agent identity; "
        "forced_level_overrides is keyed by domain. The shape divergence "
        "was fixed at the read-path level (S10 B-1) but contributors must "
        "be told which key to pass where.",
    ),
]


# ---------------------------------------------------------------------------
# 3. Source-file annotation/import truth.
# ---------------------------------------------------------------------------

# (relative_path, forbidden_substring, audit_id, why)
SOURCE_TYPE_LIES: list[tuple[str, str, str, str]] = [
    (
        "jobpulse/process_logger.py",
        "step_input: str = None",
        "S5 m-5.6",
        "step_input accepts None at runtime; the annotation must be "
        "`str | None = None`, not `str = None`.",
    ),
    (
        "jobpulse/screening_semantic_cache.py",
        "field_type: str,\n    ) -> CacheHit:",
        "S4 m-4",
        "_align_to_options returns None when the aligned answer is not in "
        "field_options; the return annotation must be `CacheHit | None`.",
    ),
    (
        "jobpulse/screening_pipeline.py",
        "lookup_canned_answer",
        "S4 m-9",
        "Docstring previously referenced screening_answers.lookup_canned_answer "
        "but no such function exists. The S4 audit cleaned up the docstring; "
        "this lint guard prevents the phrase from being reintroduced.",
    ),
]

# (relative_path, primary_phrase, required_qualifier, audit_id, why)
SOURCE_QUALIFIED: list[tuple[str, str, str, str, str]] = [
    (
        "jobpulse/cross_platform_field_transfer.py",
        "Optional[Any]",
        "from typing import Any",
        "S5 m-5.3",
        "Optional[Any] is referenced but `Any` is only saved by "
        "`from __future__ import annotations`. The import must be present "
        "explicitly so the annotation is correct under runtime evaluation.",
    ),
    (
        "shared/cognitive/_strategy.py",
        "STRATEGY_PAYLOAD_KEYS",
        "Aspirational",
        "S6 m-5",
        "STRATEGY_PAYLOAD_KEYS claims a canonical payload-key set, but "
        "_engine.flush, _reflexion._store_success, and _tot.explore each "
        "emit slightly different context strings. Comment must call out "
        "the aspirational nature so consumers tolerate missing keys.",
    ),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rel,phrase,audit_id,reason",
    FORBIDDEN_PHRASES + SOURCE_TYPE_LIES,
    ids=[f"{p[2]}:{p[0]}" for p in FORBIDDEN_PHRASES + SOURCE_TYPE_LIES],
)
def test_no_forbidden_contract_lie(rel: str, phrase: str, audit_id: str, reason: str) -> None:
    text = _read(rel)
    assert phrase not in text, (
        f"\n{rel} still contains contract lie ({audit_id}): {phrase!r}\n"
        f"\nWhy this lies: {reason}\n"
        f"\nFix: edit {rel} to remove or rewrite the phrase. See "
        f"docs/audits/pipeline-bugs.md row {audit_id} for the full audit.\n"
    )


@pytest.mark.parametrize(
    "rel,primary,qualifier,audit_id,reason",
    QUALIFIED_CLAIMS + SOURCE_QUALIFIED,
    ids=[f"{p[3]}:{p[0]}" for p in QUALIFIED_CLAIMS + SOURCE_QUALIFIED],
)
def test_qualified_contract_claim(
    rel: str, primary: str, qualifier: str, audit_id: str, reason: str
) -> None:
    text = _read(rel)
    if primary not in text:
        pytest.skip(
            f"{rel} does not currently mention {primary!r}; nothing to qualify."
        )
    assert qualifier in text, (
        f"\n{rel} mentions {primary!r} but is missing required qualifier "
        f"{qualifier!r} ({audit_id}).\n"
        f"\nWhy this needs a qualifier: {reason}\n"
        f"\nFix: add the qualifier near the primary phrase, or remove the "
        f"primary phrase entirely. See docs/audits/pipeline-bugs.md row "
        f"{audit_id}.\n"
    )
