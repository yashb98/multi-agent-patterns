# Extension Form Engine v2 — Design Spec

**Date:** 2026-04-07
**Status:** Approved
**Goal:** Make content.js a complete, self-sufficient form engine that surpasses Playwright in every way

## Problem

content.js has 5 simple action handlers (fill, click, select, check, upload). Playwright adapters have 30+ interaction patterns including form group pairing, modal scoping, parent traversal, typeahead, radio label clicking, and conditional field handling. Since we're dropping Playwright, content.js must absorb ALL of these capabilities.

## Architecture: 6-Stage Pipeline

Every form interaction follows:

```
SCAN → CLASSIFY → CONTEXTUALIZE → FILL → VERIFY → ADAPT
```

1. **SCAN** — Deep scan within a scoped container (modal, fieldset, page)
2. **CLASSIFY** — Determine exact field type (16 types matching Python InputType enum)
3. **CONTEXTUALIZE** — Pair label+input, read parent text, fieldset legend, help text
4. **FILL** — Type-specific handler with human-like behavior
5. **VERIFY** — Check for validation errors, stale DOM, value acceptance
6. **ADAPT** — Re-scan for conditional fields, cascading dropdowns, new elements

## New Action Handlers (12)

| Action | Purpose |
|--------|---------|
| `fill_radio_group` | Find radio group by name, match labels, click label element |
| `fill_custom_select` | Click trigger → wait → filter → fuzzy match → click option |
| `fill_autocomplete` | Type partial → wait for suggestions → click matching option |
| `fill_tag_input` | Type value + Enter for each tag, wait between |
| `fill_date` | Handle native date, custom calendar, text date with format detection |
| `scroll_to` | Scroll element into view with smooth behavior |
| `wait_for_selector` | Poll DOM for selector with configurable timeout |
| `get_field_context` | Rich context: label, help text, parent, fieldset, validation |
| `scan_form_groups` | Pair labels+inputs within a container, return structured groups |
| `check_consent_boxes` | Auto-check all GDPR/terms/privacy checkboxes |
| `force_click` | Click even if obscured (dispatch click event directly) |
| `rescan_after_fill` | Re-scan for conditional fields + validation errors |

## Key Improvements Over Playwright

1. Form group pairing (label+input as unit, not flat lists)
2. Modal/container scoping (search within Easy Apply modal, not entire page)
3. Fuzzy matching with abbreviation expansion in JS (matching Python's _fuzzy_match_option)
4. Post-fill validation error detection
5. Conditional field re-scan after cascading dropdowns
6. Real browser fingerprint (zero CDP detection)
7. Passive behavior calibration from real user
8. Gemini Nano for free on-device field analysis
