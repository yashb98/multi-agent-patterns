# Feature: Gmail Pre-Classifier (Rule-Based Email Triage)

**Goal:** Eliminate unnecessary LLM calls by filtering obvious non-recruiter emails before they reach gpt-4o-mini classification. Use evidence-based attribution for all classification decisions.

**Status:** APPROVED — ready for implementation

---

## Decisions (Finalized 2026-03-27)

- Pre-classify SELECTED by rule too (with evidence-based attribution)
- Every email gets read through LLM for knowledge extraction + classification learning, flagged for user review
- Audit sampling starts at 50% and decays gradually to 10% as the classifier learns
- No hardcoded junk patterns yet — system learns from reading every email initially
- Evidence-based attribution: every classification decision includes the rule/evidence that triggered it

---

## Problem

Every email fetched from Gmail gets an LLM call (`gpt-4o-mini`, ~$0.001 each). In a typical inbox:

- ~70-85% of emails are **obviously OTHER** (newsletters, promotions, receipts, automated notifications)
- ~10-20% are **obviously REJECTED** (template rejections from known ATS platforms)
- ~5-10% are **actually ambiguous** and need LLM classification

We're burning LLM calls on emails that simple rules can handle.

## Solution: 4-Tier Email Triage with Evidence-Based Attribution

```
Email arrives
    │
    ▼
┌──────────────────────────────────────────┐
│  TIER 0: LEARNING PHASE (first N days)   │  ← Reads ALL emails via LLM
│  Every email → LLM reads full content    │     to build classification knowledge
│  Builds pattern database + flags for     │     User reviews flagged decisions
│  user review                             │
└─────────────────┬────────────────────────┘
                  │ (once rules are learned)
                  ▼
┌──────────────────────────────────────────┐
│  TIER 1: Rule-Based Pre-Classifier       │  ← FREE, instant
│  (sender patterns, subject keywords)     │
│  Confidence: HIGH → classify direct      │
│  Confidence: LOW  → pass through         │
│  Evidence: logs WHY it classified        │
└─────────────────┬────────────────────────┘
                  │ (only ambiguous emails)
                  ▼
┌──────────────────────────────────────────┐
│  TIER 2: LLM Classification             │  ← $0.001, ~500ms
│  (gpt-4o-mini, as today)                │
│  Categories: SELECTED / INTERVIEW /      │
│  REJECTED / OTHER                        │
│  Feeds back into Tier 1 rule learning    │
└──────────────────────────────────────────┘
```

---

## Tier 1: Rule-Based Pre-Classifier

### 1A. Sender-Based Rules (instant discard → OTHER)

Emails from these sender patterns skip LLM entirely:

**Auto-OTHER (newsletter/promo/transactional):**
| Pattern | Examples |
|---------|----------|
| `noreply@`, `no-reply@` | Most automated emails |
| `notifications@`, `notify@` | GitHub, LinkedIn, app notifications |
| `marketing@`, `promo@`, `offers@` | Marketing blasts |
| `newsletter@`, `digest@`, `updates@` | Newsletters |
| `support@`, `billing@`, `receipts@` | Transactional |
| `mailer-daemon@`, `postmaster@` | Bounce/system |
| Known newsletter domains | `substack.com`, `medium.com`, `mailchimp.com`, etc. |
| Social media notifications | `facebookmail.com`, `twitter.com`, `instagram.com`, `linkedin.com` (notification-type only) |

**Auto-OTHER (subject keywords):**
| Pattern | Rationale |
|---------|-----------|
| `unsubscribe` in body (first 200 chars) | Newsletter/marketing indicator |
| Subject starts with `Re:` from non-recruiter sender | Thread continuation, not new outreach |
| `order confirmation`, `receipt`, `invoice`, `shipping` | E-commerce |
| `verify your email`, `confirm your account` | Account setup |
| `your weekly/monthly/daily` | Digests |

### 1B. Sender-Based Rules (likely recruiter → send to LLM with hint)

These get flagged as "probably recruiter" but still go to LLM for accurate sub-classification:

| Pattern | Hint |
|---------|------|
| Known ATS domains: `greenhouse.io`, `lever.co`, `workday.com`, `smartrecruiters.com`, `icims.com`, `myworkday.com`, `taleo.net`, `jobvite.com` | `likely_recruiter=True` |
| Subject contains: `application`, `role`, `position`, `interview`, `opportunity`, `candidate` | `likely_recruiter=True` |
| Sender contains: `recruit`, `talent`, `hiring`, `hr@`, `careers@`, `jobs@` | `likely_recruiter=True` |

### 1C. Auto-REJECTED Rules (template rejections)

Common rejection patterns that don't need LLM:

| Pattern | Classification |
|---------|---------------|
| Subject + body match: `unfortunately` + `other candidates` | REJECTED |
| Subject + body match: `regret to inform` + `not (selected\|moving forward)` | REJECTED |
| Known ATS rejection templates (Workday, Greenhouse standard rejections) | REJECTED |

> These only fire when BOTH subject AND body match. Single keyword matches still go to LLM.

### 1D. Auto-SELECTED Rules (positive signals)

| Pattern | Classification |
|---------|---------------|
| Subject + body match: `congratulations` + `next (round\|stage\|step)` | SELECTED |
| Subject + body match: `pleased to inform` + `(selected\|progressed\|shortlisted)` | SELECTED |
| Subject + body match: `moving forward` + `(your application\|your candidacy)` | SELECTED |

> Same dual-match requirement as REJECTED. Conservative — false positive on good news is worse than missing it.

### 1E. Evidence-Based Attribution

Every pre-classifier decision includes an evidence record:

```python
{
    "email_id": "msg_abc123",
    "category": "REJECTED",
    "confidence": 0.92,
    "evidence": {
        "rule_name": "ats_rejection_template",
        "matched_patterns": ["subject: 'unfortunately'", "body: 'other candidates'"],
        "sender_signal": "noreply@workday.com → known ATS domain",
        "reasoning": "Dual subject+body rejection pattern from known ATS"
    },
    "flagged_for_review": True,  # user can verify
    "audit_verified": None       # filled after LLM audit or user review
}
```

This makes every classification decision traceable and reviewable.

---

## Confidence Scoring

Each rule produces a confidence score:

| Confidence | Action |
|------------|--------|
| **>= 0.9** | Classify directly, skip LLM |
| **0.6 - 0.9** | Classify directly, but flag for LLM audit (rate varies) |
| **< 0.6** | Send to LLM (current behavior) |

### Adaptive Audit Decay

Audit sampling rate decays as the classifier learns:

| Phase | Emails Processed | Audit Rate | Rationale |
|-------|-----------------|------------|-----------|
| **Learning** | 0-100 | 50% | Build initial pattern knowledge |
| **Calibrating** | 100-500 | 30% | Validate learned rules |
| **Tuning** | 500-1000 | 20% | Fine-tune edge cases |
| **Stable** | 1000+ | 10% | Maintenance mode, catch drift |

The audit rate is stored in SQLite and auto-decrements as `total_processed` crosses thresholds. Can be manually reset if accuracy drops.

---

## Learning Phase (Tier 0)

Before the rule engine has enough data, **every email gets read through LLM** — not just for classification, but for deep knowledge extraction:

```python
def learning_phase_read(email: dict) -> dict:
    """
    LLM reads the full email and extracts:
    1. Classification (SELECTED/INTERVIEW/REJECTED/OTHER)
    2. Sender pattern analysis (is this an ATS? recruiter? newsletter?)
    3. Key signals (what words/patterns indicate the category?)
    4. Confidence reasoning (how sure is the LLM and why?)
    """
    return {
        "category": "REJECTED",
        "sender_type": "ats_automated",
        "key_signals": ["unfortunately", "other candidates", "workday.com"],
        "suggested_rule": {
            "type": "subject_body_match",
            "pattern": "unfortunately + other candidates",
            "confidence": 0.92
        },
        "flagged_for_review": True  # User sees this in Telegram
    }
```

### User Review Flow

During learning phase, flagged classifications appear in Telegram:

```
📧 Classification Review #47
From: noreply@workday.com
Subject: "Update on your application — Software Engineer"
→ Classified: REJECTED (confidence: 0.92)
→ Evidence: "unfortunately" + "other candidates" in body
→ Suggested rule: auto_rejected_ats_template

Reply: ✅ (correct) or ❌ (wrong) or 🔄 (reclassify as: SELECTED/INTERVIEW/OTHER)
```

User feedback directly trains the rule engine.

---

## Continuous Learning (from LLM corrections + user feedback)

Two learning sources:

### Source 1: LLM Audit Corrections
When LLM classifies an email that the pre-classifier was unsure about:

1. Store the (sender_pattern, subject_keywords, LLM_result) tuple
2. After 20+ accumulated examples per pattern, auto-generate new rules
3. New rules are added to a **learned rules** JSON file (not hardcoded)
4. Learned rules have lower initial confidence (0.7) until validated by 10+ correct matches

### Source 2: User Feedback (Telegram)
When user corrects a classification:

1. Immediately update the rule's confidence (up or down)
2. If user corrects 3+ times for same pattern → flag rule for removal/rewrite
3. User corrections have 2x weight vs LLM audit corrections

```
data/gmail_learned_rules.json
{
  "sender_rules": [
    {"pattern": "talent@example.com", "category": "OTHER", "confidence": 0.85, "matches": 23, "user_verified": 5}
  ],
  "subject_rules": [
    {"pattern": "your application to.*has been reviewed", "category": "REJECTED", "confidence": 0.9, "matches": 15, "user_corrections": 0}
  ]
}
```

---

## Expected Impact

| Metric | Before | After | Savings |
|--------|--------|-------|---------|
| LLM calls per 50 emails | 50 | ~8-15 | **70-85% reduction** |
| Cost per check cycle | ~$0.05 | ~$0.008-0.015 | **70-85% reduction** |
| Latency per cycle | ~25s (50 × 500ms) | ~6s | **~75% faster** |
| Accuracy (recruiter emails) | ~95% (LLM) | ~95% (LLM for ambiguous) + ~99% (rules for obvious) | **Same or better** |

The key insight: we're NOT replacing LLM classification for hard cases. We're removing it for trivially obvious cases.

---

## Implementation Plan

### Phase 1: Learning Phase + Static Rules Engine
- New file: `jobpulse/email_preclassifier.py`
  - Static rules (sender patterns, subject keywords, dual-match rules)
  - Evidence-based attribution on every decision
  - Returns `PreClassification(category, confidence, evidence, flagged_for_review)`
- Learning phase: LLM reads every email, extracts patterns, suggests rules
- Wire into `gmail_agent.py` before `_classify_email()` call
- Add process trail step: `decision` type showing pre-classifier result
- Telegram review flow for flagged classifications
- **Commit + push after completion**

### Phase 2: Learned Rules + Adaptive Audit Loop
- Add `data/gmail_learned_rules.json` for dynamically learned patterns
- Adaptive audit decay: 50% → 30% → 20% → 10% based on emails processed
- User feedback integration (Telegram ✅/❌/🔄 replies)
- Store audit results in SQLite for rule accuracy tracking
- Auto-generate new rules when patterns accumulate (20+ examples)
- **Commit + push after completion**

### Phase 3: Metrics Dashboard + Graduation
- Add pre-classifier stats to `/analytics.html`:
  - LLM calls saved per day/week
  - Pre-classifier accuracy (via audit loop + user feedback)
  - Rule hit rates (which rules fire most)
  - Cost savings estimate
  - Learning phase progress (emails processed, rules generated)
- Auto-graduation: system exits learning phase when rule accuracy > 95% on audit sample
- **Commit + push after completion**

---

## Files Changed

| File | Change |
|------|--------|
| `jobpulse/email_preclassifier.py` | **NEW** — rule engine, learning phase, evidence attribution, learned rules loader |
| `jobpulse/gmail_agent.py` | Wire pre-classifier before `_classify_email()`, add learning phase path |
| `data/gmail_preclassifier_rules.json` | **NEW** — static rules (editable, evidence-based) |
| `data/gmail_learned_rules.json` | **NEW** — auto-generated rules from LLM + user feedback |
| `jobpulse/db.py` | Add `preclassifier_audits` and `preclassifier_state` tables |
| `jobpulse/telegram_listener.py` | Handle ✅/❌/🔄 review replies for classification feedback |
| `tests/test_email_preclassifier.py` | **NEW** — unit tests for rules, confidence, evidence attribution |
| `docs/agents.md` | Document pre-classifier in Gmail Agent section |
| `CLAUDE.md` | Update stats |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Rule misclassifies a recruiter email as OTHER | High audit rate (50%) in learning phase; user review flow catches errors immediately |
| Rules become stale as sender patterns change | Learned rules auto-update; audit loop validates; user corrections have 2x weight |
| False REJECTED classification | Auto-REJECTED rules require BOTH subject AND body match; single keyword → LLM |
| False SELECTED classification | Same dual-match requirement; conservative patterns only |
| Learning phase burns LLM calls initially | By design — investing LLM calls now to save them permanently. Exits automatically when accuracy > 95% |
| User review fatigue | Flagging frequency decreases as confidence improves; only novel/uncertain patterns get flagged |
