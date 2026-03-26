# Feature: Smart Gmail Classification — Relevant vs Irrelevant

## Problem

The Gmail agent currently scans **every email** in the inbox and classifies each one with an LLM call. Out of 36 emails last week:
- 33 were OTHER (newsletters, promotions, receipts) — wasted 33 LLM calls
- 3 were actual recruiter emails

That's **92% wasted processing**. The agent should pre-filter before spending money on LLM classification.

## Current Flow (Broken)

```
Inbox (50 emails)
     │
     ▼ fetch ALL
LLM classify EACH ($0.001 × 50 = $0.05)
     │
     ▼
33 = OTHER (wasted)
3 = RECRUITER (useful)
```

## Proposed Flow (Smart)

```
Inbox (50 emails)
     │
     ▼ fetch ALL
Pre-filter: sender + subject keywords (FREE, instant)
     │
     ├── 35 = SKIP (newsletters, noreply, promotions)
     │         → No LLM call. Silently ignored.
     │
     └── 15 = MAYBE RELEVANT
              │
              ▼
         LLM classify ($0.001 × 15 = $0.015)
              │
              ├── 12 = OTHER (borderline, correctly filtered)
              └── 3 = RECRUITER → alert
```

**Savings: 70% fewer LLM calls. Same recruiter detection.**

## Pre-Filter Rules (Tier 1 — Free, Instant)

### Auto-SKIP: Never classify these

| Rule | Examples | Why |
|------|----------|-----|
| **Sender is noreply@** | noreply@linkedin.com, noreply@amazon.co.uk | Automated, never recruiter |
| **Sender domain is known newsletter** | *.substack.com, newsletter@*, digest@* | Subscriptions |
| **Sender domain is known promotion** | *.marketing.*, promo@*, offers@* | Marketing |
| **Subject contains promo keywords** | "% off", "sale", "unsubscribe", "your order", "receipt", "invoice", "delivery" | Shopping/receipts |
| **Subject contains social keywords** | "liked your", "commented on", "new follower", "connection request" | Social media notifications |
| **Sender in user's skip list** | User adds: "skip: amazon.co.uk" | Personal blocklist |

### Auto-CLASSIFY: Always send to LLM

| Rule | Examples | Why |
|------|----------|-----|
| **Sender domain is known recruiter** | @greenhouse.io, @lever.co, @workday.com, @smartrecruiters.com, @icims.com, @jobvite.com | ATS platforms |
| **Subject contains job keywords** | "application", "interview", "role", "position", "opportunity", "shortlisted", "selected", "rejected", "next round" | Job-related |
| **Sender contains recruiter keywords** | "talent", "recruit", "hiring", "HR", "people team" | Recruiter titles |
| **Sender in user's watch list** | User adds: "watch: tui.com" | Companies in pipeline |

### MAYBE: Send to LLM for classification

Everything else — personal emails, unknown senders, ambiguous subjects.

## Configuration

### Sender Skip List (auto-populated + user-editable)

```yaml
# data/gmail_filters.yaml

skip_senders:
  # Auto-populated from OTHER classifications
  - noreply@linkedin.com
  - noreply@amazon.co.uk
  - newsletter@substack.com
  - notifications@github.com
  - no-reply@accounts.google.com

skip_domains:
  - marketing.
  - promo.
  - newsletter.
  - noreply.

skip_subject_keywords:
  - "% off"
  - "sale ends"
  - "your order"
  - "delivery update"
  - "receipt"
  - "invoice"
  - "unsubscribe"
  - "your subscription"
  - "verify your email"
  - "reset your password"
  - "new sign-in"
  - "security alert"

watch_senders:
  # Companies in your job pipeline
  - greenhouse.io
  - lever.co
  - workday.com
  - smartrecruiters.com
  - icims.com
  - jobvite.com
  - tui.com
  - millenniumre.com

watch_subject_keywords:
  - "application"
  - "interview"
  - "role"
  - "position"
  - "opportunity"
  - "shortlisted"
  - "selected"
  - "next round"
  - "regret"
  - "unfortunately"
  - "pleased to inform"
  - "congratulations"
  - "assessment"
  - "coding challenge"
  - "technical test"
```

### Auto-Learning

After each LLM classification:
- If result is OTHER → add sender to `skip_senders` (so it's skipped next time)
- If result is SELECTED/INTERVIEW/REJECTED → add sender domain to `watch_senders`

Over time, the skip list grows and fewer emails need LLM calls.

## Telegram Commands

| Command | What |
|---------|------|
| `skip: amazon.co.uk` | Add domain to skip list |
| `watch: barclays.com` | Add domain to watch list (always classify) |
| `show filters` | Show current skip/watch lists |
| `reset filters` | Clear learned filters, keep defaults |

## Implementation

### Files to Create

| File | Purpose |
|------|---------|
| `jobpulse/gmail_filter.py` | Pre-filter logic: skip/watch/maybe decisions |
| `data/gmail_filters.yaml` | Configurable skip/watch lists |

### Files to Modify

| File | Change |
|------|--------|
| `jobpulse/gmail_agent.py` | Insert pre-filter before LLM classification |
| `jobpulse/command_router.py` | Add GMAIL_FILTER intent for skip/watch commands |
| `jobpulse/dispatcher.py` | Add filter management handler |

### Modified check_emails() Flow

```python
def check_emails():
    messages = fetch_inbox()

    for msg in messages:
        sender = msg.sender
        subject = msg.subject

        # Tier 1: Pre-filter (free)
        decision = pre_filter(sender, subject)

        if decision == "SKIP":
            # Don't waste an LLM call
            db.store_email(msg_id, sender, subject, "SKIPPED", ...)
            continue

        if decision == "WATCH":
            # Definitely classify — this is likely a recruiter
            category = classify_email(subject, body)  # LLM call
        else:
            # MAYBE — classify to be safe
            category = classify_email(subject, body)  # LLM call

        # Auto-learn from result
        if category == "OTHER":
            add_to_skip_list(sender)
        elif category in ("SELECTED", "INTERVIEW", "REJECTED"):
            add_to_watch_list(extract_domain(sender))

        # Alert + store as before
        ...
```

## Cost Impact

| Metric | Before | After |
|--------|--------|-------|
| Emails scanned per check | 50 | 50 |
| LLM calls per check | 50 | ~15 |
| Cost per check | $0.05 | ~$0.015 |
| Daily cost (3 checks) | $0.15 | ~$0.045 |
| Monthly cost | $4.50 | ~$1.35 |
| **Savings** | — | **70%** |

## Accuracy Safeguards

- **False skip rate target: <1%** — if a recruiter email gets skipped, the sender domain gets added to watch list on the next correct classification
- **Weekly audit**: morning briefing includes "Skipped X emails this week" count. If unusually high, review the skip list
- **Conservative default**: when in doubt, classify (MAYBE). Only skip when sender/subject clearly matches skip patterns
- **User override**: "watch: company.com" forces classification for any domain

## Edge Cases

| Scenario | Handling |
|----------|---------|
| Recruiter emails from gmail.com (personal address) | Subject keywords catch it ("interview", "application") |
| Company uses noreply@ for recruiter emails | watch_senders overrides skip rules — if domain is in watch list, always classify |
| User gets a job offer from an unknown company | MAYBE bucket → LLM classifies → domain auto-added to watch list |
| Newsletter from a company you're interviewing with | watch_senders catches the domain even if subject looks like newsletter |

## Priority of Rules

```
1. watch_senders (always classify) — HIGHEST
2. watch_subject_keywords (always classify)
3. skip_senders (skip)
4. skip_domains (skip)
5. skip_subject_keywords (skip)
6. Everything else → MAYBE (classify with LLM)
```

Watch rules ALWAYS override skip rules. If `tui.com` is in both skip_domains and watch_senders, it gets classified.
