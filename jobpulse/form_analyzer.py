"""Full-page LLM form analyzer — analyzes entire form pages autonomously.

Instead of matching fields one-by-one with regex patterns, this sends the
complete form snapshot (all fields, labels, options, page context) to the LLM
in a single call. The LLM sees the full picture and decides the best answer
for every field simultaneously.

Profile data (about the applicant) is hardcoded. Everything else —
understanding what the form is asking, mapping fields to answers,
handling dropdowns/radio/checkboxes — is autonomous.
"""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from jobpulse.config import OPENAI_API_KEY
from shared.logging_config import get_logger

from jobpulse.applicator import PROFILE, WORK_AUTH
from jobpulse.ext_models import Action, FieldInfo, PageSnapshot
from jobpulse.screening_answers import ROLE_SALARY, SKILL_EXPERIENCE

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Profile context (hardcoded about the user — everything else is autonomous)
# ---------------------------------------------------------------------------

_PROFILE_CONTEXT = f"""## Applicant Profile
- Name: {PROFILE['first_name']} {PROFILE['last_name']}
- Email: {PROFILE['email']}
- Phone: {PROFILE['phone']}
- Location: {PROFILE['location']}
- Education: {PROFILE['education']}
- LinkedIn: {PROFILE['linkedin']}
- GitHub: {PROFILE['github']}
- Portfolio: {PROFILE.get('portfolio', '')}

## Work Authorization (UK)
- Visa: {WORK_AUTH['visa_status']}
- Right to work in UK: Yes (Graduate Visa from May 2026, valid 2 years)
- Requires sponsorship: No
- Notice period: Immediately
- Current employer: Co-op (Team Leader)
- Current salary: £22,000

## Demographics (for equality monitoring forms)
- Gender: Male | Pronouns: He/Him
- Ethnicity: Asian or Asian British - Indian
- Nationality: Indian
- Religion: Hindu
- Disability: No
- Veteran: No
- Age range: 25-29
- Marital status: Single
- Driving licence: Yes

## Skills & Experience
- Python: 3 years | SQL: 3 years
- Machine Learning, Deep Learning, NLP, LLMs, Gen AI: 2 years
- TensorFlow, PyTorch, scikit-learn, pandas, numpy: 2 years
- Docker, Git, Linux, AWS, CI/CD: 2 years
- FastAPI, Flask, REST APIs: 2 years
- Spark, Airflow, ETL pipelines: 2 years
- Tableau, Power BI: 2 years
- React, JavaScript, TypeScript: 2 years
- Team management: 3 years (managed team of 8)

## Salary Expectations (by role)
- Data Scientist / ML Engineer / AI Engineer: £32,000
- Data Analyst: £28,000
- Data Engineer / Software Engineer: £30,000

## Standard Answers
- Willing to relocate within UK: Yes
- Willing to work remote/hybrid/on-site: Yes
- Background check: Yes, willing to undergo
- Security clearance: None currently held
- Languages: English (Native), Hindi (Native)
- Full-time preferred
- Available to start: Immediately
"""


def _build_fields_description(fields: list[FieldInfo]) -> str:
    """Build a structured description of all form fields for the LLM."""
    parts: list[str] = []
    for i, f in enumerate(fields):
        desc = f"Field {i + 1}:"
        desc += f"\n  selector: {f.selector}"
        desc += f"\n  type: {f.input_type}"
        desc += f"\n  label: {f.label!r}"
        if f.required:
            desc += "\n  required: YES"
        if f.current_value:
            desc += f"\n  current_value: {f.current_value!r} (ALREADY FILLED — skip unless wrong)"
        if f.options:
            desc += f"\n  options: {f.options}"
        if f.help_text:
            desc += f"\n  help_text: {f.help_text!r}"
        if f.group_label:
            desc += f"\n  group_label: {f.group_label!r}"
        if f.fieldset_legend:
            desc += f"\n  fieldset_legend: {f.fieldset_legend!r}"
        if f.error_text:
            desc += f"\n  error: {f.error_text!r}"
        parts.append(desc)
    return "\n\n".join(parts) if parts else "(no fields detected)"


def analyze_form_page(
    snapshot: PageSnapshot,
    *,
    job_context: dict[str, Any] | None = None,
    platform: str = "unknown",
) -> list[Action]:
    """Analyze an entire form page and return fill actions for all fields.

    Makes a single Claude API call with the full page context.
    Returns a list of Action objects ready for the orchestrator.
    """
    fields_to_fill = [f for f in snapshot.fields if not f.current_value]
    if not fields_to_fill:
        logger.info("FormAnalyzer: all fields already filled, no actions needed")
        return []

    all_fields_desc = _build_fields_description(snapshot.fields)
    logger.info("FormAnalyzer: analyzing %d fields on %s", len(snapshot.fields), snapshot.url[:80])
    for f in snapshot.fields:
        logger.info("  Field: label=%r type=%s selector=%s options=%s value=%r",
                     f.label[:60] if f.label else "", f.input_type,
                     f.selector[:40], f.options[:3] if f.options else [],
                     f.current_value[:30] if f.current_value else "")

    job_info = ""
    if job_context:
        job_info = (
            f"\n## Job Context\n"
            f"- Title: {job_context.get('job_title', 'unknown')}\n"
            f"- Company: {job_context.get('company', 'unknown')}\n"
            f"- Location: {job_context.get('location', 'unknown')}\n"
        )

    page_context = ""
    if snapshot.page_text_preview:
        page_context = f"\n## Page Context\n{snapshot.page_text_preview[:1000]}\n"
    if snapshot.progress:
        page_context += f"\nForm progress: step {snapshot.progress[0]} of {snapshot.progress[1]}\n"

    prompt = f"""You are an autonomous job application agent. You must FIRST analyze what this page is asking, THEN decide what to fill.

## CRITICAL: Analyze Before Acting
Before filling ANY field, understand:
1. What is this page? (Job listing page? Application form? Search page? Login?)
2. What is each field ACTUALLY asking? Read the label, help text, placeholder, and surrounding context.
3. Is this field a SEARCH/FILTER field (do NOT fill) or an APPLICATION field (fill with my data)?
4. For fields with vague labels (empty label, generic text), look at the field's context: group_label, fieldset_legend, help_text, parent_text.

## Rules
- DO NOT fill search bars, filter fields, or navigation elements
- DO NOT fill fields that are part of the job listing page (e.g. "Search jobs", "Location filter")
- ONLY fill actual application form fields (screening questions, personal info, etc.)
- If a field's purpose is unclear and it's NOT clearly an application question, SKIP IT (return empty array)
- For select/dropdown fields, pick the EXACT option text from the options list
- For radio groups, pick the EXACT option text from options
- For checkboxes, answer "true" or "false"
- For date fields, use YYYY-MM-DD format
- For number fields, provide just the number (no commas or symbols)
- If a field asks about a skill not in my profile, default to "2" years
- For open-ended questions (why this company, tell about yourself), write 2-3 professional sentences
- Match dropdown options EXACTLY as listed

{_PROFILE_CONTEXT}
{job_info}
{page_context}

## Form Fields on This Page
{all_fields_desc}

## Page URL: {snapshot.url}
## Page Title: {snapshot.title}

Respond with ONLY a JSON array. Each element:
{{"selector": "<CSS selector>", "value": "<answer>", "type": "<action_type>"}}

Action types: fill (text/textarea/email/tel/number), select (dropdown), fill_radio_group (radio), check (checkbox), fill_date (date), fill_autocomplete (typeahead), fill_custom_select (custom dropdown)

If NO fields should be filled (e.g. this is a search page or all fields are navigation), return: []

Return ONLY the JSON array, no markdown, no explanation."""

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=2000,
            temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        logger.info("FormAnalyzer: LLM response (%d chars)", len(raw))

        # Parse JSON — handle markdown-wrapped responses
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        fill_plan: list[dict[str, str]] = json.loads(raw)

        actions: list[Action] = []
        for entry in fill_plan:
            selector = entry.get("selector", "")
            value = entry.get("value", "")
            action_type = entry.get("type", "fill")

            if not selector or not value:
                continue

            # Validate action type
            valid_types = {
                "fill", "select", "fill_radio_group", "check",
                "fill_date", "fill_autocomplete", "fill_custom_select",
                "fill_tag_input", "upload",
            }
            if action_type not in valid_types:
                action_type = "fill"

            actions.append(Action(type=action_type, selector=selector, value=value))
            logger.info("  → %s [%s] = %s", selector[:40], action_type, str(value)[:60])

        logger.info("FormAnalyzer: %d actions planned for %d fields", len(actions), len(fields_to_fill))
        return actions

    except json.JSONDecodeError as exc:
        logger.error("FormAnalyzer: failed to parse LLM response as JSON: %s", exc)
        logger.error("Raw response: %s", raw[:500] if 'raw' in dir() else 'N/A')
        return []
    except Exception as exc:
        logger.error("FormAnalyzer: LLM call failed: %s", exc)
        return []
