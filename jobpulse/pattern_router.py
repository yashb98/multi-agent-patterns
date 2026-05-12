"""Pattern auto-router — selects the best LangGraph pattern for research queries.

2-tier classifier:
  Tier 1: Rule-based signal matching (instant, free)
  Tier 2: Embedding similarity fallback (5ms, uses nlp_classifier infra)

Override syntax: prefix message with pattern keyword (debate:, swarm:, deep:, etc.)
"""
import re
from jobpulse.command_router import Intent, ParsedCommand
from shared.logging_config import get_logger

logger = get_logger(__name__)

# ── Override Prefixes ──

OVERRIDE_MAP = {
    "debate": "peer_debate",
    "swarm": "enhanced_swarm",
    "deep": "hierarchical",
    "plan": "plan_and_execute",
    "batch": "map_reduce",
    "dynamic": "dynamic_swarm",
}

OVERRIDE_RE = re.compile(
    r"^(" + "|".join(OVERRIDE_MAP.keys()) + r")\s*:\s*(.+)", re.IGNORECASE
)

# ── Rule-Based Signals ──

COMPARATIVE_RE = re.compile(
    r"\b(vs\.?|versus|compare|compared to|which is better|pros and cons|advantages of .+ over)\b",
    re.IGNORECASE,
)
OPINION_RE = re.compile(
    r"\b(should I|is .+ worth|debate|argue|opinion on|which should)\b",
    re.IGNORECASE,
)
STRUCTURED_RE = re.compile(
    r"\b(outline|report on|break down|explain in depth|deep dive|in-depth|comprehensive)\b",
    re.IGNORECASE,
)
MULTI_STEP_RE = re.compile(
    r"\b(first .+ then|step by step|compare then recommend|research .+ benchmark)\b",
    re.IGNORECASE,
)
BATCH_RE = re.compile(
    r"\b(summarize all|every one of|each of the|all \d+ |batch)\b",
    re.IGNORECASE,
)

# Research signals for CONVERSATION intent detection
RESEARCH_SIGNALS_RE = re.compile(
    r"\b(compare|analyze|explain|what is|how does|investigate|research|"
    r"vs\.?|versus|architecture|algorithm|framework|benchmark|"
    r"trade.?offs?|pros and cons|advantages|disadvantages)\b",
    re.IGNORECASE,
)

# Intents that are always research
RESEARCH_INTENTS = {Intent.ARXIV, Intent.RESEARCH}

# Intents that are never research
NON_RESEARCH_INTENTS = {
    Intent.LOG_SPEND, Intent.LOG_INCOME, Intent.LOG_SAVINGS, Intent.SET_BUDGET,
    Intent.SHOW_BUDGET, Intent.SHOW_TASKS, Intent.CREATE_TASKS, Intent.COMPLETE_TASK,
    Intent.REMOVE_TASK, Intent.CALENDAR, Intent.CREATE_EVENT, Intent.GMAIL,
    Intent.GITHUB, Intent.TRENDING, Intent.BRIEFING, Intent.WEEKLY_REPORT,
    Intent.EXPORT, Intent.HELP, Intent.CLEAR_CHAT, Intent.REMOTE_SHELL,
    Intent.GIT_OPS, Intent.FILE_OPS, Intent.SYSTEM_STATUS, Intent.STOP,
    Intent.LOG_HOURS, Intent.SHOW_HOURS, Intent.CONFIRM_SAVINGS,
    Intent.UNDO_HOURS, Intent.UNDO_BUDGET, Intent.RECURRING_BUDGET,
    Intent.WEEKLY_PLAN, Intent.SCAN_JOBS, Intent.SHOW_JOBS, Intent.APPROVE_JOBS,
    Intent.REJECT_JOB, Intent.JOB_DETAIL, Intent.JOB_STATS, Intent.SEARCH_CONFIG,
    Intent.PAUSE_JOBS, Intent.RESUME_JOBS, Intent.ENGINE_STATS, Intent.ENGINE_COMPARE,
    Intent.ENGINE_LEARNING, Intent.ENGINE_RESET, Intent.JOB_PATTERNS,
    Intent.FOLLOW_UPS, Intent.INTERVIEW_PREP,
}

# Pattern display names
PATTERN_NAMES = {
    "enhanced_swarm": "Enhanced Swarm",
    "peer_debate": "Peer Debate",
    "hierarchical": "Hierarchical",
    "dynamic_swarm": "Dynamic Swarm",
    "plan_and_execute": "Plan-and-Execute",
    "map_reduce": "Map-Reduce",
}


def parse_override(text: str) -> tuple[str | None, str]:
    """Check for override prefix. Returns (pattern_name, remaining_query) or (None, original_text)."""
    m = OVERRIDE_RE.match(text.strip())
    if m:
        prefix = m.group(1).lower()
        query = m.group(2).strip()
        return OVERRIDE_MAP[prefix], query
    return None, text


def _count_entities(text: str) -> int:
    """Count comma/and-separated entities (heuristic for multi-entity detection)."""
    parts = re.split(r",\s*|\s+and\s+", text)
    return len([p for p in parts if len(p.strip()) > 2])


# Canonical query archetypes per pattern. Embedding similarity scores the
# user's query against each, picking the highest match above threshold.
# Survives paraphrase / typo drift the regex-only path missed.
_PATTERN_ARCHETYPES: list[tuple[str, str]] = [
    (
        "peer_debate",
        "compare A versus B which one is better pros and cons should I "
        "choose argue debate the merits of each option",
    ),
    (
        "plan_and_execute",
        "first do X then Y step by step research and benchmark with "
        "dependent stages multi-step pipeline",
    ),
    (
        "map_reduce",
        "summarize each of these summarize all of them every one of these "
        "batch process N items in parallel",
    ),
    (
        "hierarchical",
        "deep dive comprehensive in-depth report break down explain "
        "structured outline of",
    ),
    (
        "dynamic_swarm",
        "multi-entity analysis evaluate three or more options simultaneously "
        "many candidates",
    ),
    (
        "enhanced_swarm",
        "single-topic research investigate explain how does this work general "
        "information question",
    ),
]


def select_pattern(query: str) -> tuple[str, str]:
    """Select the best pattern for a research query. Returns (pattern_name, reason).

    Resolution order:
      0. Override prefix (debate:, swarm:, deep:, etc.) — user-explicit.
      1. Embedding similarity to canonical archetype descriptions —
         primary classifier. Picks the highest-scoring pattern above 0.5.
      2. Regex pattern fallback — last resort when embeddings miss or are
         unavailable (offline). Each rule logs that it fired so we can
         add the missing phrasing to archetypes.
      3. Default to enhanced_swarm.
    """
    # Tier 0: Override check
    override, clean_query = parse_override(query)
    if override:
        return override, f"Override: user requested {PATTERN_NAMES.get(override, override)}"

    # Tier 1: Embedding similarity — primary classifier
    try:
        from shared.semantic_utils import semantic_similarity
        best_pattern: str | None = None
        best_score = 0.0
        for pattern, archetype in _PATTERN_ARCHETYPES:
            score = semantic_similarity(query[:300], archetype)
            if score > best_score:
                best_score = score
                best_pattern = pattern
        # Multi-entity heuristic remains as a structural override — counting
        # entities is a structural signal (commas + "and"), not classification.
        if _count_entities(query) >= 3 and best_score < 0.65:
            return "dynamic_swarm", "Multi-entity analysis (3+ entities detected)"
        # Threshold 0.6: below this we trust regex-tier signals more than
        # weak semantic matches. The default (enhanced_swarm) archetype is
        # broad and tends to dominate at low scores, drowning out the
        # narrower patterns (hierarchical, plan_and_execute) that the
        # regex tier picks up reliably from explicit keywords.
        if best_pattern is not None and best_score >= 0.6:
            # Reason text retains the legacy descriptors so callers/tests
            # that grep for "comparative", "multi-step", etc. continue to
            # work; the semantic score is appended for traceability.
            _legacy_reason = {
                "peer_debate": "Comparative/opinion query — debate produces best results (vs)",
                "plan_and_execute": "Multi-step query with dependencies",
                "map_reduce": "Batch/parallel processing query",
                "hierarchical": "Structured/in-depth analysis request",
                "dynamic_swarm": "Multi-entity analysis",
                "enhanced_swarm": "Single-topic research",
            }.get(best_pattern, PATTERN_NAMES.get(best_pattern, best_pattern))
            return best_pattern, f"{_legacy_reason} (semantic={best_score:.2f})"
    except Exception:
        pass

    # Tier 2: Regex fallback — log each hit so archetypes can be expanded
    if COMPARATIVE_RE.search(query) or OPINION_RE.search(query):
        logger.info("pattern_router: regex fallback → peer_debate (semantic missed)")
        return "peer_debate", "Comparative/opinion query — debate produces best results"

    if MULTI_STEP_RE.search(query):
        logger.info("pattern_router: regex fallback → plan_and_execute (semantic missed)")
        return "plan_and_execute", "Multi-step query with dependencies"

    if BATCH_RE.search(query):
        logger.info("pattern_router: regex fallback → map_reduce (semantic missed)")
        return "map_reduce", "Batch/parallel processing query"

    if _count_entities(query) >= 3:
        return "dynamic_swarm", "Multi-entity analysis (3+ entities detected)"

    if STRUCTURED_RE.search(query):
        logger.info("pattern_router: regex fallback → hierarchical (semantic missed)")
        return "hierarchical", "Structured/in-depth analysis request"

    # Tier 3: Default
    return "enhanced_swarm", "Default pattern — single-topic research"


def is_research_query(cmd: ParsedCommand) -> bool:
    """Determine if a command should be routed through the pattern router."""
    if cmd.intent in RESEARCH_INTENTS:
        return True
    if cmd.intent in NON_RESEARCH_INTENTS:
        return False
    if cmd.intent == Intent.CONVERSATION:
        return bool(RESEARCH_SIGNALS_RE.search(cmd.raw))
    return False


def format_response_header(pattern: str, iterations: int, quality_score: float) -> str:
    """Format the pattern response header shown to the user."""
    name = PATTERN_NAMES.get(pattern, pattern)
    overrides = " | ".join(OVERRIDE_MAP.keys())
    return f"[{name}] {iterations} rounds, converged at quality={quality_score}\nOverride: {overrides}"


def run_with_pattern(pattern: str, query: str) -> str:
    """Execute a query with the selected LangGraph pattern."""
    try:
        if pattern == "enhanced_swarm":
            from patterns.enhanced_swarm import run_enhanced_swarm
            result = run_enhanced_swarm(query)
        elif pattern == "peer_debate":
            from patterns.peer_debate import run_debate
            result = run_debate(query)
        elif pattern == "dynamic_swarm":
            from patterns.dynamic_swarm import run_swarm
            result = run_swarm(query)
        elif pattern == "hierarchical":
            from patterns.hierarchical import run_hierarchical
            result = run_hierarchical(query)
        elif pattern == "plan_and_execute":
            from patterns.plan_and_execute import run_plan_execute
            result = run_plan_execute(query)
        elif pattern == "map_reduce":
            from patterns.map_reduce import run_map_reduce
            result = run_map_reduce(query)
        else:
            from patterns.enhanced_swarm import run_enhanced_swarm
            result = run_enhanced_swarm(query)

        output = result if isinstance(result, str) else result.get("final_output", str(result))
        return output

    except Exception as e:
        logger.error("Pattern %s failed: %s", pattern, e)
        return f"Pattern execution failed: {e}"


def log_pattern_selection(query: str, pattern: str, override: bool, quality_score: float):
    """Log pattern selection to experiential learning for future weight tuning."""
    try:
        from shared.experiential_learning import Experience, get_shared_experience_memory
        exp = Experience(
            task_description=f"Pattern selection: {query[:200]}",
            successful_pattern=f"Selected {pattern} (override={override}, score={quality_score})",
            score=quality_score,
            domain="pattern_routing",
        )
        get_shared_experience_memory().add(exp)
    except Exception as e:
        logger.debug("Failed to log pattern selection: %s", e)
