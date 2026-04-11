"""Email pre-classifier — rule-based triage to skip unnecessary LLM calls.

4-tier system: Learning → Static Rules → LLM → Feedback.
Every classification includes evidence-based attribution.
"""

import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from shared.logging_config import get_logger
from shared.agents import is_local_llm, get_model_name

logger = get_logger(__name__)
_is_local_llm = is_local_llm()

RULES_PATH = Path(__file__).parent.parent / "data" / "gmail_preclassifier_rules.json"
LEARNED_RULES_PATH = Path(__file__).parent.parent / "data" / "gmail_learned_rules.json"

# Category constants (same as gmail_agent.py)
SELECTED = "SELECTED_NEXT_ROUND"
INTERVIEW = "INTERVIEW_SCHEDULING"
REJECTED = "REJECTED"
OTHER = "OTHER"


@dataclass
class PreClassification:
    """Result of pre-classification with evidence attribution."""
    category: str = None            # None = send to LLM
    confidence: float = 0.0         # 0.0-1.0
    evidence: dict = field(default_factory=lambda: {
        "rule_name": None, "matched_patterns": [], "sender_signal": None, "reasoning": None
    })
    likely_recruiter: bool = False  # hint for LLM
    skip_llm: bool = False          # True if confidence >= 0.9
    flagged_for_review: bool = False


# ── Rule Loading ──────────────────────────────────────────────────────────

def _load_rules() -> dict:
    """Load static rules from JSON."""
    try:
        with open(RULES_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("Could not load pre-classifier rules: %s", e)
        return {}


def _load_learned_rules() -> dict:
    """Load dynamically learned rules from JSON."""
    try:
        with open(LEARNED_RULES_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"sender_rules": [], "subject_rules": [], "body_rules": []}


def _save_learned_rules(rules: dict):
    """Write learned rules to JSON file."""
    try:
        with open(LEARNED_RULES_PATH, "w") as f:
            json.dump(rules, f, indent=2)
    except Exception as e:
        logger.error("Failed to save learned rules: %s", e)


# ── Rule Checks ───────────────────────────────────────────────────────────

def _check_sender_other(sender_lower: str, rules: dict) -> PreClassification:
    """Check if sender matches obvious OTHER patterns."""
    for rule in rules.get("sender_other_patterns", []):
        if rule["pattern"] in sender_lower:
            return PreClassification(
                category=OTHER,
                confidence=rule["confidence"],
                evidence={
                    "rule_name": rule["name"],
                    "matched_patterns": [f"sender contains '{rule['pattern']}'"],
                    "sender_signal": f"{rule['pattern']} → known non-recruiter pattern",
                    "reasoning": f"Sender matches auto-OTHER pattern: {rule['pattern']}"
                },
                skip_llm=False if _is_local_llm else rule["confidence"] >= 0.9,
                flagged_for_review=rule["confidence"] < 0.9,
            )
    return None


def _check_domain_other(sender_lower: str, rules: dict) -> PreClassification:
    """Check if sender domain matches obvious OTHER domains."""
    for rule in rules.get("domain_other_patterns", []):
        if rule["pattern"] in sender_lower:
            return PreClassification(
                category=OTHER,
                confidence=rule["confidence"],
                evidence={
                    "rule_name": rule["name"],
                    "matched_patterns": [f"domain contains '{rule['pattern']}'"],
                    "sender_signal": f"{rule['pattern']} → known newsletter/notification domain",
                    "reasoning": f"Sender domain matches auto-OTHER: {rule['pattern']}"
                },
                skip_llm=False if _is_local_llm else rule["confidence"] >= 0.9,
                flagged_for_review=rule["confidence"] < 0.9,
            )
    return None


def _check_subject_other(subject_lower: str, rules: dict) -> PreClassification:
    """Check if subject matches obvious OTHER patterns."""
    for rule in rules.get("subject_other_patterns", []):
        if rule["pattern"] in subject_lower:
            return PreClassification(
                category=OTHER,
                confidence=rule["confidence"],
                evidence={
                    "rule_name": rule["name"],
                    "matched_patterns": [f"subject contains '{rule['pattern']}'"],
                    "sender_signal": None,
                    "reasoning": f"Subject matches auto-OTHER pattern: {rule['pattern']}"
                },
                skip_llm=False if _is_local_llm else rule["confidence"] >= 0.9,
                flagged_for_review=rule["confidence"] < 0.9,
            )
    return None


def _check_ats_domain(sender_lower: str, rules: dict) -> PreClassification:
    """Check if sender is from a known ATS — likely recruiter, still goes to LLM."""
    for rule in rules.get("ats_domains", []):
        if rule["pattern"] in sender_lower:
            return PreClassification(
                category=None,  # LLM decides
                confidence=0.0,
                evidence={
                    "rule_name": rule["name"],
                    "matched_patterns": [f"domain contains '{rule['pattern']}'"],
                    "sender_signal": f"{rule['pattern']} → known ATS domain",
                    "reasoning": "Known ATS domain detected — sending to LLM with recruiter hint"
                },
                likely_recruiter=True,
                skip_llm=False,
            )
    return None


def _check_recruiter_sender(sender_lower: str, rules: dict) -> PreClassification:
    """Check if sender contains recruiter-related keywords."""
    for rule in rules.get("recruiter_sender_patterns", []):
        if rule["pattern"] in sender_lower:
            return PreClassification(
                category=None,
                confidence=0.0,
                evidence={
                    "rule_name": rule["name"],
                    "matched_patterns": [f"sender contains '{rule['pattern']}'"],
                    "sender_signal": f"{rule['pattern']} → likely recruiter sender",
                    "reasoning": "Recruiter sender pattern — sending to LLM with hint"
                },
                likely_recruiter=True,
                skip_llm=False,
            )
    return None


def _check_recruiter_subject(subject_lower: str, rules: dict) -> PreClassification:
    """Check if subject contains recruiter-related keywords."""
    for rule in rules.get("recruiter_subject_patterns", []):
        if rule["pattern"] in subject_lower:
            return PreClassification(
                category=None,
                confidence=0.0,
                evidence={
                    "rule_name": rule["name"],
                    "matched_patterns": [f"subject contains '{rule['pattern']}'"],
                    "sender_signal": None,
                    "reasoning": "Recruiter subject pattern — sending to LLM with hint"
                },
                likely_recruiter=True,
                skip_llm=False,
            )
    return None


def _check_rejected_dual(subject_lower: str, body_lower: str, rules: dict) -> PreClassification:
    """Check dual subject+body patterns for auto-REJECTED."""
    for rule in rules.get("rejected_dual_patterns", []):
        subj_match = rule["subject_pattern"] in subject_lower
        try:
            body_match = bool(re.search(rule["body_pattern"], body_lower))
        except re.error:
            body_match = rule["body_pattern"] in body_lower

        if subj_match and body_match:
            return PreClassification(
                category=REJECTED,
                confidence=rule["confidence"],
                evidence={
                    "rule_name": rule["name"],
                    "matched_patterns": [
                        f"subject: '{rule['subject_pattern']}'",
                        f"body: '{rule['body_pattern']}'"
                    ],
                    "sender_signal": None,
                    "reasoning": f"Dual subject+body rejection pattern: {rule['name']}"
                },
                skip_llm=False if _is_local_llm else rule["confidence"] >= 0.9,
                flagged_for_review=True,  # Always flag rejections for review
            )
    return None


def _check_selected_dual(subject_lower: str, body_lower: str, rules: dict) -> PreClassification:
    """Check dual subject+body patterns for auto-SELECTED."""
    for rule in rules.get("selected_dual_patterns", []):
        subj_match = rule["subject_pattern"] in subject_lower
        try:
            body_match = bool(re.search(rule["body_pattern"], body_lower))
        except re.error:
            body_match = rule["body_pattern"] in body_lower

        if subj_match and body_match:
            return PreClassification(
                category=SELECTED,
                confidence=rule["confidence"],
                evidence={
                    "rule_name": rule["name"],
                    "matched_patterns": [
                        f"subject: '{rule['subject_pattern']}'",
                        f"body: '{rule['body_pattern']}'"
                    ],
                    "sender_signal": None,
                    "reasoning": f"Dual subject+body selection pattern: {rule['name']}"
                },
                skip_llm=False if _is_local_llm else rule["confidence"] >= 0.9,
                flagged_for_review=True,  # Always flag selections for review
            )
    return None


def _check_learned_rules(sender_lower: str, subject_lower: str, body_lower: str) -> PreClassification:
    """Check dynamically learned rules."""
    learned = _load_learned_rules()

    for rule in learned.get("sender_rules", []):
        if rule["pattern"] in sender_lower and rule.get("confidence", 0) >= 0.7:
            return PreClassification(
                category=rule["category"],
                confidence=rule["confidence"],
                evidence={
                    "rule_name": f"learned_{rule.get('name', 'sender')}",
                    "matched_patterns": [f"sender: '{rule['pattern']}' (learned)"],
                    "sender_signal": f"Learned from {rule.get('matches', 0)} examples",
                    "reasoning": f"Learned sender rule: {rule['pattern']} → {rule['category']}"
                },
                skip_llm=False if _is_local_llm else rule["confidence"] >= 0.9,
                flagged_for_review=rule["confidence"] < 0.85,
            )

    for rule in learned.get("subject_rules", []):
        pattern = rule["pattern"]
        try:
            match = bool(re.search(pattern, subject_lower))
        except re.error:
            match = pattern in subject_lower
        if match and rule.get("confidence", 0) >= 0.7:
            return PreClassification(
                category=rule["category"],
                confidence=rule["confidence"],
                evidence={
                    "rule_name": f"learned_{rule.get('name', 'subject')}",
                    "matched_patterns": [f"subject: '{pattern}' (learned)"],
                    "sender_signal": None,
                    "reasoning": f"Learned subject rule: {pattern} → {rule['category']}"
                },
                skip_llm=False if _is_local_llm else rule["confidence"] >= 0.9,
                flagged_for_review=rule["confidence"] < 0.85,
            )

    return None


# ── Main Entry Point ──────────────────────────────────────────────────────

def preclassify(sender: str, subject: str, body: str) -> PreClassification:
    """Run email through rule-based pre-classifier.

    Returns PreClassification with:
    - category set + skip_llm=True → classified by rule, skip LLM
    - category=None + likely_recruiter=True → send to LLM with hint
    - category=None + skip_llm=False → send to LLM (no match)

    Every result includes evidence attribution.
    """
    sender_lower = sender.lower()
    subject_lower = subject.lower()
    body_lower = body.lower() if body else ""

    rules = _load_rules()

    # Priority order: dual-match patterns first (most specific)
    # 1. REJECTED dual match (subject + body)
    result = _check_rejected_dual(subject_lower, body_lower, rules)
    if result:
        logger.info("Pre-classified as REJECTED: %s (conf=%.2f)", result.evidence["rule_name"], result.confidence)
        return result

    # 2. SELECTED dual match (subject + body)
    result = _check_selected_dual(subject_lower, body_lower, rules)
    if result:
        logger.info("Pre-classified as SELECTED: %s (conf=%.2f)", result.evidence["rule_name"], result.confidence)
        return result

    # 3. ATS domain check (recruiter hint, still goes to LLM)
    result = _check_ats_domain(sender_lower, rules)
    if result:
        logger.info("ATS domain detected: %s", result.evidence["rule_name"])
        return result

    # 4. Recruiter sender/subject keywords (hint, still goes to LLM)
    result = _check_recruiter_sender(sender_lower, rules)
    if result:
        logger.info("Recruiter sender hint: %s", result.evidence["rule_name"])
        return result

    result = _check_recruiter_subject(subject_lower, rules)
    if result:
        logger.info("Recruiter subject hint: %s", result.evidence["rule_name"])
        return result

    # 5. Sender OTHER patterns
    result = _check_sender_other(sender_lower, rules)
    if result:
        logger.info("Pre-classified as OTHER (sender): %s", result.evidence["rule_name"])
        return result

    # 6. Domain OTHER patterns
    result = _check_domain_other(sender_lower, rules)
    if result:
        logger.info("Pre-classified as OTHER (domain): %s", result.evidence["rule_name"])
        return result

    # 7. Subject OTHER patterns
    result = _check_subject_other(subject_lower, rules)
    if result:
        logger.info("Pre-classified as OTHER (subject): %s", result.evidence["rule_name"])
        return result

    # 8. Learned rules (dynamic)
    result = _check_learned_rules(sender_lower, subject_lower, body_lower)
    if result:
        logger.info("Pre-classified by learned rule: %s → %s", result.evidence["rule_name"], result.category)
        return result

    # 9. No match — pass through to LLM
    return PreClassification(
        category=None,
        confidence=0.0,
        evidence={"rule_name": None, "matched_patterns": [], "sender_signal": None, "reasoning": None},
        likely_recruiter=False,
        skip_llm=False,
    )


# ── Audit System ──────────────────────────────────────────────────────────

def get_audit_rate() -> float:
    """Return current audit sampling rate based on emails processed.

    Decays as the system learns:
    - 0-100 emails:   50% (learning)
    - 100-500 emails:  30% (calibrating)
    - 500-1000 emails: 20% (tuning)
    - 1000+ emails:    10% (stable)
    """
    from jobpulse import db
    state = db.get_preclassifier_state()
    total = state.get("total_processed", 0)

    if total < 100:
        return 0.50
    elif total < 500:
        return 0.30
    elif total < 1000:
        return 0.20
    return 0.10


def should_audit(pre: PreClassification) -> bool:
    """Decide whether to LLM-verify a pre-classified email.

    - High confidence (>=0.9): audit at current rate
    - Mid confidence (0.6-0.9): always audit
    - Low confidence (<0.6): already goes to LLM anyway
    """
    if pre.confidence < 0.6:
        return False  # Already going to LLM
    if pre.confidence < 0.9:
        return True   # Mid-confidence — always audit
    return random.random() < get_audit_rate()


def record_audit(email_id: str, pre: PreClassification, llm_category: str) -> bool:
    """Record an audit result — compare pre-classifier vs LLM. Returns True if correct."""
    from jobpulse import db
    is_correct = 1 if pre.category == llm_category else 0

    db.store_audit(
        email_id=email_id,
        rule_category=pre.category,
        rule_confidence=pre.confidence,
        rule_name=pre.evidence.get("rule_name"),
        llm_category=llm_category,
        is_correct=is_correct,
    )

    state = db.get_preclassifier_state()
    db.update_preclassifier_state(
        total_audited=state["total_audited"] + 1,
        total_correct=state["total_correct"] + is_correct,
    )

    if not is_correct:
        logger.warning("Audit mismatch: rule=%s, LLM=%s for email %s (rule: %s)",
                       pre.category, llm_category, email_id, pre.evidence.get("rule_name"))

    return bool(is_correct)


# ── Learning Phase ────────────────────────────────────────────────────────

def is_learning_phase() -> bool:
    """Check if system is still in learning phase."""
    from jobpulse import db
    state = db.get_preclassifier_state()
    return bool(state.get("learning_phase", 1)) and not bool(state.get("graduated", 0))


def increment_processed():
    """Increment total_processed counter."""
    from jobpulse import db
    state = db.get_preclassifier_state()
    db.update_preclassifier_state(total_processed=state["total_processed"] + 1)


def extract_patterns_from_email(sender: str, subject: str, body: str, category: str) -> dict:
    """During learning phase, LLM analyzes email to extract classification patterns."""
    from shared.agents import get_openai_client

    client = get_openai_client()

    prompt = f"""Analyze this email classification to extract reusable patterns.

Email:
- Sender: {sender}
- Subject: {subject}
- Body (first 300 chars): {body[:300]}
- Classified as: {category}

Extract:
1. sender_type: "ats_automated" | "recruiter_personal" | "newsletter" | "transactional" | "social" | "personal" | "unknown"
2. key_signals: list of 2-5 specific words/phrases that indicate the category
3. suggested_rule: if this pattern is repeatable, suggest a rule with:
   - type: "sender" | "subject" | "body" | "dual_match"
   - pattern: the string to match
   - category: the category it should map to
   - confidence: 0.0-1.0

Respond in JSON only."""

    try:
        response = client.chat.completions.create(
            model=get_model_name(),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600 if _is_local_llm else 300,
            temperature=0,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error("Pattern extraction failed: %s", e)
        return {"sender_type": "unknown", "key_signals": [], "suggested_rule": None}


# ── Learned Rules ─────────────────────────────────────────────────────────

def save_learned_rule(rule_type: str, pattern: str, category: str,
                      confidence: float = 0.7, name: str = None):
    """Add a new learned rule to the JSON file."""
    learned = _load_learned_rules()
    key = f"{rule_type}_rules"
    if key not in learned:
        learned[key] = []

    # Check for duplicate
    for existing in learned[key]:
        if existing["pattern"] == pattern:
            existing["matches"] = existing.get("matches", 0) + 1
            if existing["matches"] >= 10:
                existing["confidence"] = min(0.95, existing["confidence"] + 0.01)
            _save_learned_rules(learned)
            return

    learned[key].append({
        "pattern": pattern,
        "category": category,
        "confidence": confidence,
        "name": name or f"learned_{rule_type}_{len(learned[key])}",
        "matches": 1,
        "user_verified": 0,
        "user_corrections": 0,
    })
    learned["updated_at"] = datetime.now().isoformat()[:10]
    _save_learned_rules(learned)
    logger.info("New learned rule: %s → %s (conf=%.2f)", pattern, category, confidence)


# ── Graduation ────────────────────────────────────────────────────────────

def check_graduation() -> bool:
    """Check if the pre-classifier should graduate from learning phase.

    Graduates when:
    - At least 100 emails processed
    - At least 20 audits completed
    - Audit accuracy >= 95%
    """
    from jobpulse import db
    state = db.get_preclassifier_state()

    if state.get("graduated", 0):
        return True

    total = state.get("total_processed", 0)
    audited = state.get("total_audited", 0)

    if total < 100 or audited < 20:
        return False

    accuracy = db.get_audit_accuracy(limit=50)
    if accuracy >= 0.95:
        db.update_preclassifier_state(graduated=1, learning_phase=0)
        logger.info("Pre-classifier GRADUATED: accuracy=%.2f%% (%d audits, %d processed)",
                    accuracy * 100, audited, total)
        return True

    return False
