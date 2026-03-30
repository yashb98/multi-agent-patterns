# 7-Day Brutal Pre-Screen Experiment

> **Start:** 2026-03-31 (Monday)
> **End:** 2026-04-06 (Sunday)
> **Hypothesis:** Ultra-strict pre-screening (≥4/5 top skills, ≥20 matches, ≥92% required) produces fewer but higher-quality applications that convert to more interviews.

---

## Thresholds (Locked for 7 Days)

| Gate | Threshold | Rationale |
|------|-----------|-----------|
| Gate 0 | Title fuzzy match + exclude keywords | Filter irrelevant roles pre-LLM |
| Gate 1 K1 | Kill if JD requires ≥5 years | Seniority mismatch |
| Gate 1 K2 | Kill if primary required skill missing | No chance without core skill |
| Gate 1 K3 | Kill if top-3 all from foreign domain | Wrong specialization |
| **Gate 2 M1** | **≥4 of top-5 required skills** | Near-perfect core match required |
| Gate 2 M2 | ≥2 projects with 3+ skill overlap | Must demonstrate, not just claim |
| **Gate 2 M3** | **≥20 absolute matches AND ≥92% required** | ATS keyword density must be near-total |
| Gate 3 | Score ≥55 to proceed, ≥75 = strong | 5-dimension competitiveness |

## Daily Tracking

Fill in each day. Track what matters: how many pass, how many get applied, any interview callbacks.

### Day 1 — Monday 2026-03-31

| Metric | Value |
|--------|-------|
| Raw jobs scanned | |
| Gate 0 filtered | |
| Duplicates removed | |
| Gate 1 rejected (kill signals) | |
| Gate 2 skipped (must-haves fail) | |
| Gate 3 skipped (score < 55) | |
| **Jobs passed all gates** | |
| CVs generated | |
| Applications submitted | |
| LLM calls made | |
| Best match score | |
| Notes | |

### Day 2 — Tuesday 2026-04-01

| Metric | Value |
|--------|-------|
| Raw jobs scanned | |
| Gate 0 filtered | |
| Duplicates removed | |
| Gate 1 rejected | |
| Gate 2 skipped | |
| Gate 3 skipped | |
| **Jobs passed all gates** | |
| CVs generated | |
| Applications submitted | |
| LLM calls made | |
| Best match score | |
| Notes | |

### Day 3 — Wednesday 2026-04-02

| Metric | Value |
|--------|-------|
| Raw jobs scanned | |
| Gate 0 filtered | |
| Duplicates removed | |
| Gate 1 rejected | |
| Gate 2 skipped | |
| Gate 3 skipped | |
| **Jobs passed all gates** | |
| CVs generated | |
| Applications submitted | |
| LLM calls made | |
| Best match score | |
| Notes | |

### Day 4 — Thursday 2026-04-03

| Metric | Value |
|--------|-------|
| Raw jobs scanned | |
| Gate 0 filtered | |
| Duplicates removed | |
| Gate 1 rejected | |
| Gate 2 skipped | |
| Gate 3 skipped | |
| **Jobs passed all gates** | |
| CVs generated | |
| Applications submitted | |
| LLM calls made | |
| Best match score | |
| Notes | |

### Day 5 — Friday 2026-04-04

| Metric | Value |
|--------|-------|
| Raw jobs scanned | |
| Gate 0 filtered | |
| Duplicates removed | |
| Gate 1 rejected | |
| Gate 2 skipped | |
| Gate 3 skipped | |
| **Jobs passed all gates** | |
| CVs generated | |
| Applications submitted | |
| LLM calls made | |
| Best match score | |
| Notes | |

### Day 6 — Saturday 2026-04-05

| Metric | Value |
|--------|-------|
| Raw jobs scanned | |
| Gate 0 filtered | |
| Duplicates removed | |
| Gate 1 rejected | |
| Gate 2 skipped | |
| Gate 3 skipped | |
| **Jobs passed all gates** | |
| CVs generated | |
| Applications submitted | |
| LLM calls made | |
| Best match score | |
| Notes | |

### Day 7 — Sunday 2026-04-06

| Metric | Value |
|--------|-------|
| Raw jobs scanned | |
| Gate 0 filtered | |
| Duplicates removed | |
| Gate 1 rejected | |
| Gate 2 skipped | |
| Gate 3 skipped | |
| **Jobs passed all gates** | |
| CVs generated | |
| Applications submitted | |
| LLM calls made | |
| Best match score | |
| Notes | |

---

## Weekly Summary (Fill on Day 7)

| Metric | Total | Daily Avg |
|--------|-------|-----------|
| Raw jobs scanned | | |
| Jobs passed all 4 gates | | |
| Applications submitted | | |
| LLM calls made | | |
| Estimated LLM cost | | |
| Interview callbacks received | | |
| **Conversion rate** (interviews / applications) | | |

## Comparison: Before vs After

| Metric | Before (no pre-screen) | This Experiment |
|--------|----------------------|-----------------|
| Apps/day | ~25 (spray & pray) | |
| Interview callbacks/week | | |
| Conversion rate | | |
| LLM cost/week | ~$1.33 | |
| Avg ATS score of submitted | | |

## Decision After 7 Days

- [ ] If conversion rate > previous → **keep brutal thresholds**
- [ ] If 0 applications pass → **loosen M3 to ≥15 matches, ≥85% required**
- [ ] If applications pass but 0 interviews → **review Gate 3 scoring weights**
- [ ] If interview rate improves → **make this the permanent default**

## Risk Assessment

**Expected:** With ≥92% required and ≥20 matches, very few jobs will pass. Maybe 1-3/day or even 0 some days. This is intentional — we're testing whether applying to ONLY near-perfect matches produces interviews.

**If zero jobs pass for 3+ consecutive days:** The thresholds may be too strict for the current job market. Consider:
1. Lowering M3 to ≥18 matches, ≥88%
2. Keeping M1 at ≥4/5 (this is the most impactful gate)
3. Never loosening Gate 1 kill signals (these are absolute dealbreakers)

## How To Read the Logs

```bash
# Check today's gate stats
grep "Gate 0" logs/jobs.log | tail -5
grep "Gates 1-3" logs/jobs.log | tail -5
grep "REJECTED\|SKIPPED\|AUTO-APPLIED" logs/jobs.log | tail -20

# Check pre-screen breakdown
grep "M1 fail\|M2 fail\|M3 fail" logs/jobs.log | tail -20

# Check competitiveness scores
grep "gate3_score" logs/jobs.log | tail -10
```
