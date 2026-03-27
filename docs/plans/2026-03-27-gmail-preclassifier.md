# Gmail Pre-Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a rule-based pre-classifier to Gmail agent that eliminates 70-85% of unnecessary LLM calls by filtering obvious emails before they reach gpt-4o-mini.

**Architecture:** 4-tier email triage (Learning → Rules → LLM → Feedback). Static rules handle obvious cases (newsletters, ATS rejections, template selections). Learning phase reads all emails via LLM to build pattern knowledge. Adaptive audit decay starts at 50% and drops to 10% as accuracy improves. Telegram review flow lets user correct classifications with ✅/❌/🔄.

**Tech Stack:** Python 3, SQLite, OpenAI gpt-4o-mini, Telegram Bot API, pytest

**Spec:** `docs/feature-gmail-preclassifier.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `jobpulse/email_preclassifier.py` | **NEW** — Rule engine, confidence scoring, evidence attribution, learned rules loader, audit logic, graduation check |
| `jobpulse/email_review.py` | **NEW** — Telegram review reply handler (mirrors approval.py pattern) |
| `jobpulse/gmail_agent.py` | **MODIFY** — Wire pre-classifier before `_classify_email()`, add learning phase path |
| `jobpulse/db.py` | **MODIFY** — Add `preclassifier_audits` and `preclassifier_state` tables |
| `jobpulse/telegram_listener.py` | **MODIFY** — Hook review reply handling before approval check |
| `data/gmail_preclassifier_rules.json` | **NEW** — Static rules (sender patterns, subject keywords, dual-match) |
| `data/gmail_learned_rules.json` | **NEW** — Auto-generated rules from LLM + user feedback |
| `tests/test_email_preclassifier.py` | **NEW** — Unit tests for rules, confidence, evidence, audit, graduation |
| `docs/agents.md` | **MODIFY** — Document pre-classifier in Gmail Agent section |
| `CLAUDE.md` | **MODIFY** — Update stats |

---

## Phase 1: Core Pre-Classifier Engine + Static Rules + Evidence Attribution

### Task 1.1: Add DB tables for pre-classifier

**Files:**
- Modify: `jobpulse/db.py:15-43`

- [ ] **Step 1: Add pre-classifier tables to init_db()**

In `jobpulse/db.py`, add after the existing `CREATE INDEX` statements (line 35) inside the `executescript()` call:

```sql
CREATE TABLE IF NOT EXISTS preclassifier_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT NOT NULL,
    rule_category TEXT,
    rule_confidence REAL,
    rule_name TEXT,
    llm_category TEXT,
    user_category TEXT,
    is_correct INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (email_id) REFERENCES processed_emails(email_id)
);

CREATE TABLE IF NOT EXISTS preclassifier_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total_processed INTEGER DEFAULT 0,
    total_correct INTEGER DEFAULT 0,
    total_audited INTEGER DEFAULT 0,
    learning_phase INTEGER DEFAULT 1,
    graduated INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_email ON preclassifier_audits(email_id);
CREATE INDEX IF NOT EXISTS idx_audit_correct ON preclassifier_audits(is_correct);
```

- [ ] **Step 2: Add DB helper functions**

Add these functions at the end of `jobpulse/db.py` (before the `init_db()` call at line 96):

```python
def store_audit(email_id: str, rule_category: str, rule_confidence: float,
                rule_name: str, llm_category: str = None, user_category: str = None,
                is_correct: int = None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO preclassifier_audits (email_id, rule_category, rule_confidence, rule_name, llm_category, user_category, is_correct) VALUES (?,?,?,?,?,?,?)",
        (email_id, rule_category, rule_confidence, rule_name, llm_category, user_category, is_correct)
    )
    conn.commit()
    conn.close()


def get_preclassifier_state() -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM preclassifier_state WHERE id=1").fetchone()
    if not row:
        conn.execute("INSERT INTO preclassifier_state (id, total_processed, total_correct, total_audited, learning_phase, graduated) VALUES (1, 0, 0, 0, 1, 0)")
        conn.commit()
        row = conn.execute("SELECT * FROM preclassifier_state WHERE id=1").fetchone()
    conn.close()
    return dict(row)


def update_preclassifier_state(**kwargs):
    conn = get_conn()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values())
    conn.execute(f"UPDATE preclassifier_state SET {sets}, updated_at=datetime('now') WHERE id=1", vals)
    conn.commit()
    conn.close()


def get_audit_accuracy(limit: int = 100) -> float:
    """Return accuracy of last N audited pre-classifications."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as total, SUM(CASE WHEN is_correct=1 THEN 1 ELSE 0 END) as correct FROM (SELECT is_correct FROM preclassifier_audits WHERE is_correct IS NOT NULL ORDER BY created_at DESC LIMIT ?)",
        (limit,)
    ).fetchone()
    conn.close()
    if not row or row["total"] == 0:
        return 0.0
    return row["correct"] / row["total"]
```

- [ ] **Step 3: Run existing tests to verify no breakage**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All existing tests pass (DB schema changes are additive)

- [ ] **Step 4: Commit**

```bash
git add jobpulse/db.py
git commit -m "feat(gmail): add pre-classifier DB tables for audits and state tracking"
```

---

### Task 1.2: Create static rules JSON

**Files:**
- Create: `data/gmail_preclassifier_rules.json`

- [ ] **Step 1: Write the static rules file**

```json
{
  "version": "1.0",
  "updated_at": "2026-03-27",
  "sender_other_patterns": [
    {"pattern": "noreply@", "confidence": 0.95, "name": "noreply_sender"},
    {"pattern": "no-reply@", "confidence": 0.95, "name": "noreply_sender_alt"},
    {"pattern": "notifications@", "confidence": 0.90, "name": "notifications_sender"},
    {"pattern": "notify@", "confidence": 0.90, "name": "notify_sender"},
    {"pattern": "marketing@", "confidence": 0.95, "name": "marketing_sender"},
    {"pattern": "promo@", "confidence": 0.95, "name": "promo_sender"},
    {"pattern": "offers@", "confidence": 0.95, "name": "offers_sender"},
    {"pattern": "newsletter@", "confidence": 0.95, "name": "newsletter_sender"},
    {"pattern": "digest@", "confidence": 0.90, "name": "digest_sender"},
    {"pattern": "updates@", "confidence": 0.85, "name": "updates_sender"},
    {"pattern": "support@", "confidence": 0.85, "name": "support_sender"},
    {"pattern": "billing@", "confidence": 0.90, "name": "billing_sender"},
    {"pattern": "receipts@", "confidence": 0.95, "name": "receipts_sender"},
    {"pattern": "mailer-daemon@", "confidence": 0.99, "name": "mailer_daemon"},
    {"pattern": "postmaster@", "confidence": 0.99, "name": "postmaster"}
  ],
  "domain_other_patterns": [
    {"pattern": "substack.com", "confidence": 0.95, "name": "substack_domain"},
    {"pattern": "medium.com", "confidence": 0.90, "name": "medium_domain"},
    {"pattern": "mailchimp.com", "confidence": 0.95, "name": "mailchimp_domain"},
    {"pattern": "sendgrid.net", "confidence": 0.90, "name": "sendgrid_domain"},
    {"pattern": "facebookmail.com", "confidence": 0.95, "name": "facebook_domain"},
    {"pattern": "twitter.com", "confidence": 0.90, "name": "twitter_domain"},
    {"pattern": "instagram.com", "confidence": 0.90, "name": "instagram_domain"},
    {"pattern": "accounts.google.com", "confidence": 0.90, "name": "google_accounts"},
    {"pattern": "amazonses.com", "confidence": 0.85, "name": "amazon_ses"}
  ],
  "subject_other_patterns": [
    {"pattern": "order confirmation", "confidence": 0.95, "name": "order_confirmation"},
    {"pattern": "receipt", "confidence": 0.90, "name": "receipt_subject"},
    {"pattern": "invoice", "confidence": 0.90, "name": "invoice_subject"},
    {"pattern": "shipping", "confidence": 0.85, "name": "shipping_subject"},
    {"pattern": "verify your email", "confidence": 0.95, "name": "verify_email"},
    {"pattern": "confirm your account", "confidence": 0.95, "name": "confirm_account"},
    {"pattern": "your weekly", "confidence": 0.90, "name": "weekly_digest"},
    {"pattern": "your monthly", "confidence": 0.90, "name": "monthly_digest"},
    {"pattern": "your daily", "confidence": 0.90, "name": "daily_digest"},
    {"pattern": "unsubscribe", "confidence": 0.85, "name": "unsubscribe_subject"}
  ],
  "ats_domains": [
    {"pattern": "greenhouse.io", "confidence": 0.90, "name": "greenhouse_ats"},
    {"pattern": "lever.co", "confidence": 0.90, "name": "lever_ats"},
    {"pattern": "workday.com", "confidence": 0.90, "name": "workday_ats"},
    {"pattern": "myworkday.com", "confidence": 0.90, "name": "myworkday_ats"},
    {"pattern": "smartrecruiters.com", "confidence": 0.90, "name": "smartrecruiters_ats"},
    {"pattern": "icims.com", "confidence": 0.90, "name": "icims_ats"},
    {"pattern": "taleo.net", "confidence": 0.90, "name": "taleo_ats"},
    {"pattern": "jobvite.com", "confidence": 0.90, "name": "jobvite_ats"},
    {"pattern": "ashbyhq.com", "confidence": 0.90, "name": "ashby_ats"},
    {"pattern": "bamboohr.com", "confidence": 0.90, "name": "bamboo_ats"}
  ],
  "recruiter_sender_patterns": [
    {"pattern": "recruit", "confidence": 0.80, "name": "recruiter_keyword"},
    {"pattern": "talent", "confidence": 0.80, "name": "talent_keyword"},
    {"pattern": "hiring", "confidence": 0.80, "name": "hiring_keyword"},
    {"pattern": "hr@", "confidence": 0.75, "name": "hr_sender"},
    {"pattern": "careers@", "confidence": 0.80, "name": "careers_sender"},
    {"pattern": "jobs@", "confidence": 0.80, "name": "jobs_sender"},
    {"pattern": "people@", "confidence": 0.70, "name": "people_sender"}
  ],
  "recruiter_subject_patterns": [
    {"pattern": "application", "confidence": 0.75, "name": "application_subject"},
    {"pattern": "role", "confidence": 0.65, "name": "role_subject"},
    {"pattern": "position", "confidence": 0.70, "name": "position_subject"},
    {"pattern": "interview", "confidence": 0.85, "name": "interview_subject"},
    {"pattern": "opportunity", "confidence": 0.65, "name": "opportunity_subject"},
    {"pattern": "candidate", "confidence": 0.80, "name": "candidate_subject"}
  ],
  "rejected_dual_patterns": [
    {
      "subject_pattern": "unfortunately",
      "body_pattern": "other candidates",
      "confidence": 0.92,
      "name": "rejection_unfortunately_others"
    },
    {
      "subject_pattern": "regret to inform",
      "body_pattern": "not (selected|moving forward)",
      "confidence": 0.93,
      "name": "rejection_regret"
    },
    {
      "subject_pattern": "update on your application",
      "body_pattern": "decided not to proceed",
      "confidence": 0.91,
      "name": "rejection_not_proceed"
    },
    {
      "subject_pattern": "application status",
      "body_pattern": "not moving forward",
      "confidence": 0.92,
      "name": "rejection_not_moving"
    }
  ],
  "selected_dual_patterns": [
    {
      "subject_pattern": "congratulations",
      "body_pattern": "next (round|stage|step)",
      "confidence": 0.90,
      "name": "selected_congrats_next"
    },
    {
      "subject_pattern": "pleased to inform",
      "body_pattern": "(selected|progressed|shortlisted)",
      "confidence": 0.91,
      "name": "selected_pleased"
    },
    {
      "subject_pattern": "moving forward",
      "body_pattern": "(your application|your candidacy)",
      "confidence": 0.89,
      "name": "selected_moving_forward"
    }
  ]
}
```

- [ ] **Step 2: Commit**

```bash
git add data/gmail_preclassifier_rules.json
git commit -m "feat(gmail): add static pre-classifier rules JSON"
```

---

### Task 1.3: Create the core pre-classifier module

**Files:**
- Create: `jobpulse/email_preclassifier.py`

- [ ] **Step 1: Write tests first**

Create `tests/test_email_preclassifier.py`:

```python
"""Tests for email pre-classifier — rule engine, confidence, evidence attribution."""

import os
import json
import pytest
from unittest.mock import patch, MagicMock

os.environ["JOBPULSE_TEST_MODE"] = "1"


class TestSenderOtherRules:
    """Tier 1A: Obvious OTHER emails by sender pattern."""

    def test_noreply_classified_as_other(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("noreply@company.com", "Your weekly update", "")
        assert result.category == "OTHER"
        assert result.confidence >= 0.9
        assert result.evidence["rule_name"] == "noreply_sender"

    def test_newsletter_domain_classified_as_other(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("hello@substack.com", "New post from blog", "")
        assert result.category == "OTHER"
        assert result.confidence >= 0.9

    def test_marketing_sender_classified_as_other(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("marketing@store.com", "50% off sale", "Buy now")
        assert result.category == "OTHER"
        assert result.confidence >= 0.9

    def test_mailer_daemon_classified_as_other(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("mailer-daemon@server.com", "Delivery failure", "")
        assert result.category == "OTHER"
        assert result.confidence >= 0.95


class TestSubjectOtherRules:
    """Tier 1A: Obvious OTHER emails by subject pattern."""

    def test_order_confirmation_classified_as_other(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("orders@amazon.co.uk", "Order confirmation #123", "")
        assert result.category == "OTHER"
        assert result.confidence >= 0.9

    def test_verify_email_classified_as_other(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("auth@service.com", "Verify your email address", "")
        assert result.category == "OTHER"
        assert result.confidence >= 0.9


class TestRecruiterHintRules:
    """Tier 1B: Likely recruiter — still goes to LLM but with hint."""

    def test_ats_domain_flagged_as_recruiter(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("no-reply@greenhouse.io", "Application update", "")
        assert result.category is None  # goes to LLM
        assert result.likely_recruiter is True
        assert "greenhouse_ats" in result.evidence["rule_name"]

    def test_recruiter_sender_keyword(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("talent@bigcorp.com", "Exciting role", "")
        assert result.likely_recruiter is True

    def test_interview_subject_flagged(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("jane@company.com", "Interview scheduling", "")
        assert result.likely_recruiter is True


class TestDualMatchRules:
    """Tier 1C/1D: REJECTED and SELECTED by dual subject+body match."""

    def test_rejection_dual_match(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify(
            "hr@company.com",
            "Unfortunately about your application",
            "We have decided to move forward with other candidates"
        )
        assert result.category == "REJECTED"
        assert result.confidence >= 0.9
        assert len(result.evidence["matched_patterns"]) == 2

    def test_rejection_single_keyword_passes_through(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify(
            "hr@company.com",
            "Unfortunately we need to reschedule",
            "Can we find another time?"
        )
        # Single keyword match — should NOT auto-classify
        assert result.category is None or result.confidence < 0.6

    def test_selected_dual_match(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify(
            "hr@company.com",
            "Congratulations on your application",
            "We'd like to invite you to the next round"
        )
        assert result.category == "SELECTED_NEXT_ROUND"
        assert result.confidence >= 0.85

    def test_selected_single_keyword_passes_through(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify(
            "friend@gmail.com",
            "Congratulations on your birthday!",
            "Hope you have a great day"
        )
        assert result.category is None or result.category == "OTHER"


class TestEvidenceAttribution:
    """Every classification includes traceable evidence."""

    def test_evidence_has_required_fields(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("noreply@spam.com", "Weekly deals", "")
        assert "rule_name" in result.evidence
        assert "matched_patterns" in result.evidence
        assert "reasoning" in result.evidence

    def test_passthrough_has_empty_evidence(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("john@personalmail.com", "Quick question", "Hey, how are you?")
        assert result.category is None
        assert result.confidence == 0.0


class TestConfidenceThresholds:
    """Confidence scoring determines action."""

    def test_high_confidence_skips_llm(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("noreply@company.com", "Your receipt", "")
        assert result.confidence >= 0.9
        assert result.skip_llm is True

    def test_low_confidence_goes_to_llm(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("john@company.com", "Following up", "Wanted to check in")
        assert result.confidence < 0.6
        assert result.skip_llm is False

    def test_ambiguous_email_not_classified(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("unknown@newdomain.xyz", "Hello there", "Some random content")
        assert result.skip_llm is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_email_preclassifier.py -v --tb=short`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobpulse.email_preclassifier'`

- [ ] **Step 3: Implement the core pre-classifier**

Create `jobpulse/email_preclassifier.py`:

```python
"""Email pre-classifier — rule-based triage to skip unnecessary LLM calls.

4-tier system: Learning → Static Rules → LLM → Feedback.
Every classification includes evidence-based attribution.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from shared.logging_config import get_logger

logger = get_logger(__name__)

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
                skip_llm=rule["confidence"] >= 0.9,
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
                skip_llm=rule["confidence"] >= 0.9,
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
                skip_llm=rule["confidence"] >= 0.9,
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
                    "reasoning": f"Known ATS domain detected — sending to LLM with recruiter hint"
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
                    "reasoning": f"Recruiter sender pattern — sending to LLM with hint"
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
                    "reasoning": f"Recruiter subject pattern — sending to LLM with hint"
                },
                likely_recruiter=True,
                skip_llm=False,
            )
    return None


def _check_rejected_dual(subject_lower: str, body_lower: str, rules: dict) -> PreClassification:
    """Check dual subject+body patterns for auto-REJECTED."""
    for rule in rules.get("rejected_dual_patterns", []):
        subj_match = rule["subject_pattern"] in subject_lower
        # Body pattern can be regex
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
                skip_llm=rule["confidence"] >= 0.9,
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
                skip_llm=rule["confidence"] >= 0.9,
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
                skip_llm=rule["confidence"] >= 0.9,
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
                skip_llm=rule["confidence"] >= 0.9,
                flagged_for_review=rule["confidence"] < 0.85,
            )

    return None


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_email_preclassifier.py -v --tb=short`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/email_preclassifier.py tests/test_email_preclassifier.py
git commit -m "feat(gmail): core pre-classifier engine with static rules and evidence attribution"
```

---

### Task 1.4: Phase 1 push

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 2: Push Phase 1**

```bash
git push origin main
```

---

## Phase 2: Wire Into gmail_agent.py + Learning Phase

### Task 2.1: Integrate pre-classifier into check_emails()

**Files:**
- Modify: `jobpulse/gmail_agent.py:1-241`

- [ ] **Step 1: Add import at top of gmail_agent.py**

After the existing imports (line 12), add:

```python
from jobpulse.email_preclassifier import preclassify, PreClassification
```

- [ ] **Step 2: Add pre-classifier step in check_emails() email loop**

Replace the classify block at lines 171-177 with pre-classifier + fallback:

```python
            # Step: Pre-classify with rules
            with trail.step("decision", f"Pre-classify email #{i+1}",
                             step_input=f"Sender: {sender}\nSubject: {subject}") as s:
                pre = preclassify(sender, subject, body)
                s["output"] = f"Pre-class: {pre.category or 'PASS-THROUGH'} (conf={pre.confidence:.2f})"
                s["decision"] = pre.evidence.get("reasoning") or "No rule matched — sending to LLM"
                s["metadata"] = {
                    "rule_name": pre.evidence.get("rule_name"),
                    "confidence": pre.confidence,
                    "likely_recruiter": pre.likely_recruiter,
                    "skip_llm": pre.skip_llm,
                }

            # Use pre-classifier result or fall back to LLM
            if pre.skip_llm and pre.category:
                category = pre.category
                logger.info("Pre-classified %s as %s (skip LLM, conf=%.2f)",
                           subject[:50], category, pre.confidence)
            else:
                # Step: Classify with LLM
                with trail.step("llm_call", f"Classify email #{i+1}",
                                 step_input=f"Subject: {subject}\nBody: {body[:200]}") as s:
                    category = _classify_email(subject, body)
                    s["output"] = f"LLM classification: {category}"
                    s["decision"] = f"LLM classified as {category}"
                    if pre.likely_recruiter:
                        s["metadata"] = {"category": category, "sender": sender,
                                        "recruiter_hint": True}
                    else:
                        s["metadata"] = {"category": category, "sender": sender}
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add jobpulse/gmail_agent.py
git commit -m "feat(gmail): wire pre-classifier into check_emails() before LLM call"
```

---

### Task 2.2: Add learning phase — LLM reads all emails for pattern extraction

**Files:**
- Modify: `jobpulse/email_preclassifier.py`
- Modify: `jobpulse/gmail_agent.py`

- [ ] **Step 1: Add learning phase extraction function to email_preclassifier.py**

Add at the end of `jobpulse/email_preclassifier.py`:

```python
def extract_patterns_from_email(sender: str, subject: str, body: str, category: str) -> dict:
    """During learning phase, LLM analyzes email to extract classification patterns.

    Returns pattern analysis with suggested rules for future use.
    """
    from openai import OpenAI
    from jobpulse.config import OPENAI_API_KEY

    client = OpenAI(api_key=OPENAI_API_KEY)

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
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error("Pattern extraction failed: %s", e)
        return {"sender_type": "unknown", "key_signals": [], "suggested_rule": None}


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
```

- [ ] **Step 2: Update check_emails() to use learning phase**

In `gmail_agent.py`, after the category is determined (after the pre-classify/LLM block) and before the `# Step: Store` block, add:

```python
            # Learning phase: extract patterns from every email
            if pre.skip_llm and preclassify.__module__:
                from jobpulse.email_preclassifier import is_learning_phase, extract_patterns_from_email, increment_processed
                if is_learning_phase():
                    with trail.step("llm_call", f"Learning: analyze email #{i+1} patterns",
                                     step_input=f"Category: {category}") as s:
                        patterns = extract_patterns_from_email(sender, subject, body, category)
                        s["output"] = f"Patterns: {json.dumps(patterns.get('key_signals', []))}"
                        s["metadata"] = patterns
                increment_processed()
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 4: Commit and push Phase 2**

```bash
git add jobpulse/email_preclassifier.py jobpulse/gmail_agent.py
git commit -m "feat(gmail): add learning phase — LLM reads all emails for pattern extraction"
git push origin main
```

---

## Phase 3: Learned Rules + Adaptive Audit Decay

### Task 3.1: Create learned rules file and rule generation

**Files:**
- Create: `data/gmail_learned_rules.json`
- Modify: `jobpulse/email_preclassifier.py`

- [ ] **Step 1: Create initial empty learned rules file**

Create `data/gmail_learned_rules.json`:

```json
{
  "version": "1.0",
  "updated_at": "2026-03-27",
  "sender_rules": [],
  "subject_rules": [],
  "body_rules": []
}
```

- [ ] **Step 2: Add rule generation from accumulated patterns**

Add to `jobpulse/email_preclassifier.py`:

```python
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
            # Boost confidence with more matches (cap at 0.95)
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


def _save_learned_rules(rules: dict):
    """Write learned rules to JSON file."""
    try:
        with open(LEARNED_RULES_PATH, "w") as f:
            json.dump(rules, f, indent=2)
    except Exception as e:
        logger.error("Failed to save learned rules: %s", e)
```

Add the missing import at the top of the file:

```python
from datetime import datetime
```

- [ ] **Step 3: Commit**

```bash
git add data/gmail_learned_rules.json jobpulse/email_preclassifier.py
git commit -m "feat(gmail): add learned rules file and rule generation"
```

---

### Task 3.2: Implement adaptive audit decay

**Files:**
- Modify: `jobpulse/email_preclassifier.py`

- [ ] **Step 1: Add audit logic**

Add to `jobpulse/email_preclassifier.py`:

```python
import random


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
    - Low confidence (<0.6): always goes to LLM anyway
    """
    if pre.confidence < 0.6:
        return False  # Already going to LLM
    if pre.confidence < 0.9:
        return True   # Mid-confidence — always audit
    return random.random() < get_audit_rate()


def record_audit(email_id: str, pre: PreClassification, llm_category: str):
    """Record an audit result — compare pre-classifier vs LLM."""
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

    # Update running accuracy
    state = db.get_preclassifier_state()
    db.update_preclassifier_state(
        total_audited=state["total_audited"] + 1,
        total_correct=state["total_correct"] + is_correct,
    )

    if not is_correct:
        logger.warning("Audit mismatch: rule=%s, LLM=%s for email %s (rule: %s)",
                       pre.category, llm_category, email_id, pre.evidence.get("rule_name"))

    return is_correct
```

- [ ] **Step 2: Add tests for audit logic**

Add to `tests/test_email_preclassifier.py`:

```python
class TestAdaptiveAuditDecay:
    """Audit rate decreases as system learns."""

    def test_initial_audit_rate_is_50_percent(self):
        from jobpulse.email_preclassifier import get_audit_rate
        with patch("jobpulse.db.get_preclassifier_state", return_value={"total_processed": 0}):
            assert get_audit_rate() == 0.50

    def test_calibrating_audit_rate_is_30_percent(self):
        from jobpulse.email_preclassifier import get_audit_rate
        with patch("jobpulse.db.get_preclassifier_state", return_value={"total_processed": 200}):
            assert get_audit_rate() == 0.30

    def test_tuning_audit_rate_is_20_percent(self):
        from jobpulse.email_preclassifier import get_audit_rate
        with patch("jobpulse.db.get_preclassifier_state", return_value={"total_processed": 700}):
            assert get_audit_rate() == 0.20

    def test_stable_audit_rate_is_10_percent(self):
        from jobpulse.email_preclassifier import get_audit_rate
        with patch("jobpulse.db.get_preclassifier_state", return_value={"total_processed": 1500}):
            assert get_audit_rate() == 0.10

    def test_mid_confidence_always_audited(self):
        from jobpulse.email_preclassifier import should_audit, PreClassification
        pre = PreClassification(category="OTHER", confidence=0.75)
        assert should_audit(pre) is True

    def test_low_confidence_not_audited(self):
        from jobpulse.email_preclassifier import should_audit, PreClassification
        pre = PreClassification(category=None, confidence=0.3)
        assert should_audit(pre) is False
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_email_preclassifier.py -v --tb=short`
Expected: All tests pass

- [ ] **Step 4: Wire audit into gmail_agent.py**

In `gmail_agent.py`, update the pre-classifier block to include audit:

After the line `category = pre.category` (when pre.skip_llm is True), add audit check:

```python
            if pre.skip_llm and pre.category:
                category = pre.category
                logger.info("Pre-classified %s as %s (skip LLM, conf=%.2f)",
                           subject[:50], category, pre.confidence)
                # Audit check: sometimes verify rule decisions with LLM
                from jobpulse.email_preclassifier import should_audit, record_audit
                if should_audit(pre):
                    with trail.step("llm_call", f"Audit email #{i+1}",
                                     step_input=f"Auditing rule: {pre.evidence.get('rule_name')}") as s:
                        llm_category = _classify_email(subject, body)
                        is_correct = record_audit(msg_id, pre, llm_category)
                        s["output"] = f"Audit: rule={category}, LLM={llm_category}, correct={is_correct}"
                        s["decision"] = "Audit passed" if is_correct else f"MISMATCH: using LLM result {llm_category}"
                        if not is_correct:
                            category = llm_category  # Trust LLM over rule on mismatch
```

- [ ] **Step 5: Run full tests and commit + push Phase 3**

```bash
python -m pytest tests/ -v --tb=short
git add jobpulse/email_preclassifier.py jobpulse/gmail_agent.py tests/test_email_preclassifier.py
git commit -m "feat(gmail): adaptive audit decay (50%→10%) with LLM verification loop"
git push origin main
```

---

## Phase 4: Telegram Review Flow + User Feedback

### Task 4.1: Create email_review.py

**Files:**
- Create: `jobpulse/email_review.py`

- [ ] **Step 1: Write tests for review handler**

Add to `tests/test_email_preclassifier.py`:

```python
class TestEmailReview:
    """Telegram review reply handling."""

    def test_approve_emoji_resolves_review(self):
        from jobpulse.email_review import request_review, process_review_reply
        request_review("msg_123", "hr@company.com", "Your application", "REJECTED", 0.92, "rejection_rule")
        result = process_review_reply("✅")
        assert result is not None
        assert "Confirmed" in result

    def test_reject_emoji_corrects_review(self):
        from jobpulse.email_review import request_review, process_review_reply
        request_review("msg_456", "hr@company.com", "Interview invite", "REJECTED", 0.90, "rejection_rule")
        result = process_review_reply("❌")
        assert result is not None
        assert "Incorrect" in result or "incorrect" in result

    def test_reclassify_emoji(self):
        from jobpulse.email_review import request_review, process_review_reply
        request_review("msg_789", "hr@company.com", "Update", "OTHER", 0.85, "some_rule")
        result = process_review_reply("🔄 SELECTED")
        assert result is not None

    def test_no_pending_returns_none(self):
        from jobpulse import email_review
        email_review._pending_review = None
        result = email_review.process_review_reply("✅")
        assert result is None

    def test_non_emoji_text_returns_none(self):
        from jobpulse.email_review import request_review, process_review_reply
        request_review("msg_000", "x@y.com", "Test", "OTHER", 0.9, "rule")
        result = process_review_reply("show tasks")
        assert result is None
```

- [ ] **Step 2: Implement email_review.py**

Create `jobpulse/email_review.py`:

```python
"""Email review flow — user confirms/corrects pre-classifier decisions via Telegram.

Mirrors approval.py pattern: one pending review at a time, checked before classify().
"""

import time
from typing import Optional
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Module-level state (single pending review)
_pending_review: Optional[dict] = None


def request_review(email_id: str, sender: str, subject: str,
                   category: str, confidence: float, rule_name: str) -> str:
    """Flag an email classification for user review via Telegram.

    Returns the review message to send.
    """
    global _pending_review

    _pending_review = {
        "email_id": email_id,
        "sender": sender,
        "subject": subject,
        "category": category,
        "confidence": confidence,
        "rule_name": rule_name,
        "created_at": time.time(),
        "timeout": 3600,  # 1 hour
    }

    msg = (
        f"\U0001f4e7 Classification Review\n"
        f"From: {sender}\n"
        f"Subject: \"{subject}\"\n"
        f"\u2192 Classified: {category} (confidence: {confidence:.0%})\n"
        f"\u2192 Rule: {rule_name}\n\n"
        f"Reply: \u2705 (correct) or \u274c (wrong) or \U0001f504 CATEGORY (reclassify)"
    )

    logger.info("Review requested for email %s: %s → %s", email_id, subject[:50], category)
    return msg


def get_pending() -> Optional[dict]:
    """Return the current pending review, or None if none/expired."""
    global _pending_review
    if _pending_review is None:
        return None

    elapsed = time.time() - _pending_review["created_at"]
    if elapsed > _pending_review["timeout"]:
        logger.info("Review for %s expired after %ds", _pending_review["email_id"], int(elapsed))
        _pending_review = None
        return None

    return _pending_review


def process_review_reply(text: str) -> Optional[str]:
    """Check if text is a review reply (✅/❌/🔄).

    Returns response message if it was a review reply, None otherwise.
    Called by telegram_listener BEFORE classify().
    """
    pending = get_pending()
    if pending is None:
        return None

    global _pending_review
    stripped = text.strip()

    # ✅ Correct — confirm the classification
    if stripped in ("\u2705", "correct", "yes", "right"):
        email_id = pending["email_id"]
        category = pending["category"]
        rule_name = pending["rule_name"]
        _pending_review = None

        # Record as user-verified
        _record_user_feedback(email_id, category, is_correct=True, rule_name=rule_name)

        logger.info("User confirmed: %s → %s", email_id, category)
        return f"\u2705 Confirmed: {pending['subject'][:40]} → {category}"

    # ❌ Incorrect — mark rule as wrong
    if stripped in ("\u274c", "wrong", "incorrect", "no"):
        email_id = pending["email_id"]
        category = pending["category"]
        rule_name = pending["rule_name"]
        _pending_review = None

        _record_user_feedback(email_id, category, is_correct=False, rule_name=rule_name)

        logger.warning("User rejected: %s → %s (rule: %s)", email_id, category, rule_name)
        return f"\u274c Incorrect classification noted. Rule '{rule_name}' flagged for review."

    # 🔄 CATEGORY — reclassify
    if stripped.startswith("\U0001f504") or stripped.lower().startswith("reclassify"):
        parts = stripped.split(maxsplit=1)
        if len(parts) >= 2:
            new_category = parts[1].strip().upper()
            valid = {"SELECTED_NEXT_ROUND", "INTERVIEW_SCHEDULING", "REJECTED", "OTHER",
                     "SELECTED", "INTERVIEW"}
            # Normalize short forms
            if new_category == "SELECTED":
                new_category = "SELECTED_NEXT_ROUND"
            elif new_category == "INTERVIEW":
                new_category = "INTERVIEW_SCHEDULING"

            if new_category in valid:
                email_id = pending["email_id"]
                old_category = pending["category"]
                rule_name = pending["rule_name"]
                _pending_review = None

                _record_user_feedback(email_id, old_category, is_correct=False,
                                     rule_name=rule_name, corrected_to=new_category)

                # Update the stored email category
                _update_email_category(email_id, new_category)

                logger.info("User reclassified: %s → %s (was %s)", email_id, new_category, old_category)
                return f"\U0001f504 Reclassified: {pending['subject'][:40]} → {new_category} (was {old_category})"

        _pending_review = None
        return "\U0001f504 Usage: 🔄 SELECTED or 🔄 INTERVIEW or 🔄 REJECTED or 🔄 OTHER"

    # Not a review reply
    return None


def _record_user_feedback(email_id: str, rule_category: str, is_correct: bool,
                          rule_name: str, corrected_to: str = None):
    """Store user feedback in audit table and update learned rules."""
    try:
        from jobpulse import db
        db.store_audit(
            email_id=email_id,
            rule_category=rule_category,
            rule_confidence=None,
            rule_name=rule_name,
            llm_category=None,
            user_category=corrected_to if not is_correct else rule_category,
            is_correct=1 if is_correct else 0,
        )

        # Update learned rule confidence based on feedback
        from jobpulse.email_preclassifier import _load_learned_rules, _save_learned_rules
        learned = _load_learned_rules()
        for key in ["sender_rules", "subject_rules", "body_rules"]:
            for rule in learned.get(key, []):
                if rule.get("name") == rule_name:
                    if is_correct:
                        rule["user_verified"] = rule.get("user_verified", 0) + 1
                        # Boost confidence slightly
                        rule["confidence"] = min(0.95, rule["confidence"] + 0.02)
                    else:
                        rule["user_corrections"] = rule.get("user_corrections", 0) + 1
                        # Reduce confidence
                        rule["confidence"] = max(0.3, rule["confidence"] - 0.05)
                        # Flag for removal if 3+ corrections
                        if rule.get("user_corrections", 0) >= 3:
                            rule["confidence"] = 0.0  # Effectively disabled
                            logger.warning("Learned rule '%s' disabled after 3 corrections", rule_name)
        _save_learned_rules(learned)

    except Exception as e:
        logger.error("Failed to record user feedback: %s", e)


def _update_email_category(email_id: str, new_category: str):
    """Update a stored email's category after user correction."""
    try:
        from jobpulse import db
        conn = db.get_conn()
        conn.execute("UPDATE processed_emails SET category=? WHERE email_id=?",
                    (new_category, email_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Failed to update email category: %s", e)
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_email_preclassifier.py::TestEmailReview -v --tb=short`
Expected: All review tests pass

- [ ] **Step 4: Commit**

```bash
git add jobpulse/email_review.py tests/test_email_preclassifier.py
git commit -m "feat(gmail): Telegram review flow — ✅/❌/🔄 user feedback for classifications"
```

---

### Task 4.2: Wire review into telegram_listener.py and gmail_agent.py

**Files:**
- Modify: `jobpulse/telegram_listener.py:89-95` and `172-178`
- Modify: `jobpulse/gmail_agent.py`

- [ ] **Step 1: Add review check in telegram_listener.py poll_and_process()**

In `telegram_listener.py`, add BEFORE the approval check at line 89:

```python
        # Check for email classification review reply
        from jobpulse.email_review import process_review_reply
        review_response = process_review_reply(text)
        if review_response:
            telegram_agent.send_message(review_response)
            _log(f"Email review: {review_response[:80]}")
            continue
```

- [ ] **Step 2: Add review check in poll_continuous()**

In `telegram_listener.py`, add BEFORE the approval check at line 172:

```python
                # Check for email classification review reply
                from jobpulse.email_review import process_review_reply
                review_response = process_review_reply(text)
                if review_response:
                    telegram_agent.send_message(review_response)
                    _log(f"Email review: {review_response[:80]}")
                    continue
```

- [ ] **Step 3: Send review requests from gmail_agent.py**

In `gmail_agent.py`, after the Telegram alert is sent (after line 206 `s["output"] = f"Alert sent for {category}"`), add review request for pre-classified emails:

```python
                # Request user review for pre-classified emails
                if pre.skip_llm and pre.flagged_for_review:
                    from jobpulse.email_review import request_review
                    from jobpulse.telegram_bots import send_alert
                    review_msg = request_review(
                        msg_id, sender_short, subject, category,
                        pre.confidence, pre.evidence.get("rule_name", "unknown")
                    )
                    send_alert(review_msg)
```

Also add the same block for OTHER emails that were pre-classified and flagged (add after the `if category != OTHER:` block closes, before the exception handler):

```python
            # For pre-classified OTHER emails that are flagged, still request review
            if category == OTHER and pre.skip_llm and pre.flagged_for_review:
                from jobpulse.email_review import request_review
                from jobpulse.telegram_bots import send_alert
                review_msg = request_review(
                    msg_id, sender.split("<")[0].strip() if "<" in sender else sender,
                    subject, category, pre.confidence, pre.evidence.get("rule_name", "unknown")
                )
                send_alert(review_msg)
```

- [ ] **Step 4: Run full tests**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 5: Commit and push Phase 4**

```bash
git add jobpulse/telegram_listener.py jobpulse/gmail_agent.py
git commit -m "feat(gmail): wire Telegram review flow into listener and gmail agent"
git push origin main
```

---

## Phase 5: Auto-Graduation + Docs Update + Final Tests

### Task 5.1: Implement auto-graduation

**Files:**
- Modify: `jobpulse/email_preclassifier.py`

- [ ] **Step 1: Add graduation check**

Add to `jobpulse/email_preclassifier.py`:

```python
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
        return True  # Already graduated

    total = state.get("total_processed", 0)
    audited = state.get("total_audited", 0)

    if total < 100 or audited < 20:
        return False

    accuracy = db.get_audit_accuracy(limit=50)  # Last 50 audits
    if accuracy >= 0.95:
        db.update_preclassifier_state(graduated=1, learning_phase=0)
        logger.info("Pre-classifier GRADUATED: accuracy=%.2f%% (%d audits, %d processed)",
                    accuracy * 100, audited, total)
        return True

    return False
```

- [ ] **Step 2: Wire graduation into gmail_agent.py**

In `gmail_agent.py`, at the end of the `check_emails()` function (before `trail.finalize()`), add:

```python
    # Check for pre-classifier graduation
    from jobpulse.email_preclassifier import check_graduation
    graduated = check_graduation()
    if graduated:
        from jobpulse import db
        state = db.get_preclassifier_state()
        trail_suffix = f" Pre-classifier graduated (accuracy: {state['total_correct']}/{state['total_audited']})"
    else:
        trail_suffix = ""
```

Update the `trail.finalize()` call to include graduation info:

```python
    trail.finalize(f"Processed {len(messages)} emails. "
                   f"Recruiter: {len(new_recruiter_emails)}. Alerts sent: {len(new_recruiter_emails)}.{trail_suffix}")
```

- [ ] **Step 3: Add graduation tests**

Add to `tests/test_email_preclassifier.py`:

```python
class TestAutoGraduation:
    """System exits learning phase when accuracy > 95%."""

    def test_does_not_graduate_with_few_emails(self):
        from jobpulse.email_preclassifier import check_graduation
        with patch("jobpulse.db.get_preclassifier_state",
                   return_value={"graduated": 0, "total_processed": 50, "total_audited": 10}):
            assert check_graduation() is False

    def test_does_not_graduate_with_few_audits(self):
        from jobpulse.email_preclassifier import check_graduation
        with patch("jobpulse.db.get_preclassifier_state",
                   return_value={"graduated": 0, "total_processed": 200, "total_audited": 5}):
            assert check_graduation() is False

    def test_graduates_with_high_accuracy(self):
        from jobpulse.email_preclassifier import check_graduation
        with patch("jobpulse.db.get_preclassifier_state",
                   return_value={"graduated": 0, "total_processed": 200, "total_audited": 30}), \
             patch("jobpulse.db.get_audit_accuracy", return_value=0.97), \
             patch("jobpulse.db.update_preclassifier_state"):
            assert check_graduation() is True

    def test_does_not_graduate_with_low_accuracy(self):
        from jobpulse.email_preclassifier import check_graduation
        with patch("jobpulse.db.get_preclassifier_state",
                   return_value={"graduated": 0, "total_processed": 200, "total_audited": 30}), \
             patch("jobpulse.db.get_audit_accuracy", return_value=0.85):
            assert check_graduation() is False

    def test_already_graduated_returns_true(self):
        from jobpulse.email_preclassifier import check_graduation
        with patch("jobpulse.db.get_preclassifier_state",
                   return_value={"graduated": 1, "total_processed": 500, "total_audited": 100}):
            assert check_graduation() is True
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_email_preclassifier.py -v --tb=short`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add jobpulse/email_preclassifier.py jobpulse/gmail_agent.py tests/test_email_preclassifier.py
git commit -m "feat(gmail): auto-graduation when pre-classifier accuracy exceeds 95%"
```

---

### Task 5.2: Update documentation

**Files:**
- Modify: `docs/agents.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add pre-classifier section to docs/agents.md**

After the Gmail Agent section in `docs/agents.md`, add:

```markdown
### Email Pre-Classifier (`email_preclassifier.py`)
- Rule-based pre-classification before LLM — eliminates 70-85% of unnecessary LLM calls
- 4-tier system: Learning → Static Rules → LLM Fallback → User Feedback
- Static rules: sender patterns, domain patterns, subject keywords, dual subject+body match
- Categories: auto-OTHER (newsletters, receipts), auto-REJECTED (template rejections), auto-SELECTED (congratulations patterns)
- Evidence-based attribution: every decision logged with rule name, matched patterns, reasoning
- Adaptive audit decay: 50% → 30% → 20% → 10% as classifier processes more emails
- Learned rules: dynamically generated from LLM audits + user feedback (stored in `data/gmail_learned_rules.json`)
- Telegram review flow: ✅ (correct), ❌ (wrong), 🔄 CATEGORY (reclassify) — user corrections have 2x weight
- Auto-graduation: exits learning phase when accuracy > 95% on last 50 audits (min 100 emails, 20 audits)
- Rules priority: dual-match → ATS domain → recruiter hints → sender OTHER → domain OTHER → subject OTHER → learned
```

- [ ] **Step 2: Update CLAUDE.md stats**

Run `python scripts/update_stats.py` to auto-update stats, or manually update the Stats section.

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 4: Commit and push Phase 5**

```bash
git add docs/agents.md CLAUDE.md tests/test_email_preclassifier.py
git commit -m "docs: add email pre-classifier to agents.md, update CLAUDE.md stats"
git push origin main
```

---

## Summary

| Phase | What Gets Built | Key Files |
|-------|----------------|-----------|
| **1** | Core engine + static rules + evidence + DB tables | `email_preclassifier.py`, `db.py`, rules JSON, tests |
| **2** | Wire into gmail_agent + learning phase LLM reads | `gmail_agent.py` integration |
| **3** | Learned rules + adaptive audit (50%→10%) | Audit logic, learned rules JSON |
| **4** | Telegram ✅/❌/🔄 review + user feedback | `email_review.py`, `telegram_listener.py` |
| **5** | Auto-graduation + docs + final tests | Graduation logic, `agents.md`, `CLAUDE.md` |
