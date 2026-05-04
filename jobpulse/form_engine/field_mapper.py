"""Field mapper — deterministic and LLM-based field→value resolution."""
from __future__ import annotations

import base64
import json
import re
from typing import Any, TYPE_CHECKING

from shared.agents import get_openai_client, get_model_name
from shared.logging_config import get_logger
from shared.pii import assert_prompt_has_wrapped_pii

from jobpulse.form_engine.field_resolver import (
    _FIELD_LABEL_TO_PROFILE_KEY,
    _build_option_aliases,
    _ensure_label_db,
    _fuzzy_label_to_profile_key,
    _persist_label_mapping,
    _profile_prompt_json,
    _screening_prompt_background,
    _screening_prompt_profile,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


def save_gotcha(page_url: str, label: str, problem: str, solution: str) -> None:
    """Auto-save a form-filling gotcha for a domain."""
    try:
        from urllib.parse import urlparse
        from jobpulse.form_engine.gotchas import GotchasDB

        if not page_url:
            return
        domain = urlparse(page_url).netloc.lower().removeprefix("www.")
        db = GotchasDB()
        db.store(domain, label, problem, solution, engine="playwright")
    except Exception as exc:
        logger.debug("Could not save gotcha: %s", exc)


def is_screening_like_field(field: dict[str, Any]) -> bool:
    return (
        field.get("type") in {"select", "combobox", "radio", "checkbox"}
        or "?" in field.get("label", "")
    )


def learn_field_mapping(mapping: dict[str, str], profile: dict) -> None:
    """Learn new label→profile_key associations from LLM results."""
    from jobpulse.applicator import PROFILE
    profile_flat = {**PROFILE, **profile}

    value_to_key: dict[str, str] = {}
    for k, v in profile_flat.items():
        if v and isinstance(v, str):
            value_to_key[v.strip().lower()] = k

    new_count = 0
    for label, value in mapping.items():
        label_lower = label.lower()
        if label_lower in _FIELD_LABEL_TO_PROFILE_KEY:
            continue
        val_lower = str(value).strip().lower()
        profile_key = value_to_key.get(val_lower)
        if profile_key:
            _FIELD_LABEL_TO_PROFILE_KEY[label_lower] = profile_key
            _persist_label_mapping(label_lower, profile_key)
            new_count += 1

    if new_count:
        logger.info("Learned %d new field label mappings (persisted to SQLite)", new_count)


def try_cached_mapping(
    page_url: str, fields: list[dict], profile: dict,
    custom_answers: dict, known_domain: bool,
    domain_field_mappings: dict[str, str] | None = None,
) -> dict | None:
    """Try to resolve field mapping from cached label→profile_key templates."""
    _ensure_label_db()
    try:
        from jobpulse.form_experience_db import FormExperienceDB
        if not page_url:
            return None
        db = FormExperienceDB()
        exp = db.lookup(page_url)
        if not exp or not exp.get("field_types"):
            if not known_domain:
                return None

        from jobpulse.applicator import PROFILE
        profile_flat = {**PROFILE, **profile}
        label_key_map = _FIELD_LABEL_TO_PROFILE_KEY

        mapping: dict[str, str] = {}
        unmapped: list[str] = []
        for f in fields:
            if f["type"] == "file" or f.get("value"):
                continue
            label = f["label"]
            key = (domain_field_mappings or {}).get(label)
            if not key:
                key = label_key_map.get(label.lower())
            if not key:
                key = _fuzzy_label_to_profile_key(label.lower())
            if key and key in profile_flat and profile_flat[key]:
                value = profile_flat[key]
                if key == "location":
                    _jctx = custom_answers.get("_job_context")
                    if isinstance(_jctx, dict):
                        job_loc = _jctx.get("location", "")
                        if isinstance(job_loc, str) and job_loc.strip():
                            value = job_loc.strip()
                mapping[label] = value
            elif label.lower() in custom_answers:
                mapping[label] = custom_answers[label.lower()]
            else:
                unmapped.append(label)

        if unmapped and not known_domain:
            return None
        if mapping:
            logger.info(
                "Field mapping: %d fields resolved from cache, %d unmapped (0 LLM calls)",
                len(mapping), len(unmapped),
            )
        return mapping if mapping else None
    except Exception:
        return None


def clean_mapping(mapping: dict[str, Any]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for label, value in mapping.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            cleaned[label] = text
    return cleaned


# Diversity-related keywords → generic custom_answers key
_DIVERSITY_KEYWORDS: dict[tuple[str, ...], str] = {
    ("gender", "gender identity", "sex", "sex at birth"): "gender",
    ("sexual orientation", "orientation"): "sexual_orientation",
    ("ethnicity", "race", "racial", "ethnic background"): "ethnicity",
    ("disability", "disabilities"): "disability",
    ("veteran", "veterans", "military"): "veteran",
}


def _fuzzy_custom_answer(label_lower: str, custom_answers: dict) -> str | None:
    """Try to match a field label to a custom_answers key fuzzily.

    Same correctness rule as `fuzzy_label_to_profile_key`: substring / keyword
    matching does NOT fire on sentence-shaped questions. Without this, a
    custom_answers entry like {"country": "United Kingdom"} would substring-
    match into any long question containing the word "country" (e.g.
    "Are you eligible to work in the country..." → returns "United Kingdom"
    as the answer to a Yes/No).
    """
    from jobpulse.form_engine.field_resolver import _is_sentence_question
    import re as _re

    tokens_list = _re.sub(r"[^a-z0-9]+", " ", label_lower).split()
    if _is_sentence_question(label_lower, tokens_list):
        return None

    # Exact match already tried by caller — do substring / keyword matching here
    for key, value in custom_answers.items():
        if key.startswith("_"):
            continue
        key_lower = key.lower()
        if key_lower in label_lower or label_lower in key_lower:
            if isinstance(value, str) and value.strip():
                return value.strip()

    # Diversity keyword fallback
    for keywords, generic_key in _DIVERSITY_KEYWORDS.items():
        if any(kw in label_lower for kw in keywords):
            val = custom_answers.get(generic_key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            # Also try capitalized / spaced variants
            for alt in (generic_key.replace("_", " "), generic_key.replace("_", "")):
                val = custom_answers.get(alt)
                if isinstance(val, str) and val.strip():
                    return val.strip()

    # Embedding similarity fallback.
    # Skip for very short labels (< 5 chars): the embedding model can't
    # reliably compare single short words and produces false positives like
    # "stream" → "name" (score 0.83). The substring + diversity tiers above
    # already cover the legitimate short-label cases.
    if len(label_lower.strip()) < 5:
        return None
    try:
        from shared.semantic_utils import best_semantic_match
        candidate_keys = [k for k in custom_answers if not k.startswith("_") and isinstance(custom_answers[k], str) and custom_answers[k].strip()]
        # Apply the same length floor to candidate keys.
        candidate_keys = [k for k in candidate_keys if len(k.strip()) >= 5]
        if candidate_keys:
            match, score = best_semantic_match(label_lower, candidate_keys, min_score=0.70)
            if match is not None:
                return custom_answers[match].strip()
    except Exception:
        pass

    return None


def _resolve_with_options(value: str, field: dict) -> str:
    """If a field has options, use semantic matching to pick the exact option text."""
    options = field.get("options")
    if not options or field["type"] in ("text", "textarea"):
        return value

    from jobpulse.form_engine.semantic_matcher import semantic_option_match

    try:
        numeric = float(value.replace(",", "").replace("£", "").replace("$", "").replace("€", ""))
    except (ValueError, AttributeError):
        numeric = None

    # Country-suffix preference: option lists like Greenhouse's location
    # autocomplete return ambiguous matches ("Dundee, Florida" vs
    # "Dundee, Dundee City, United Kingdom"). When the user's country is
    # known via ProfileStore, pass it as a tiebreaker so the option in the
    # right country wins. Falls back gracefully if ProfileStore unavailable.
    prefer: tuple[str, ...] = ()
    try:
        from shared.profile_store import get_profile_store
        store = get_profile_store()
        country = (store.sensitive("country") or "").strip()
        if not country:
            location = (store.identity().location or "").strip()
            if "," in location:
                country = location.rsplit(",", 1)[-1].strip()
            elif location:
                country = location
        if country:
            prefer = (country,)
    except Exception:
        prefer = ()

    matched = semantic_option_match(
        value, options,
        field_label=field.get("label", ""),
        numeric_value=numeric,
        prefer_substrings=prefer,
    )
    return matched if matched is not None else value


def seed_mapping(
    fields: list[dict], profile: dict, custom_answers: dict,
    *, strategy=None,
) -> tuple[dict[str, str], list[dict]]:
    """Resolve any field that has a deterministic profile/custom answer."""
    _ensure_label_db()
    from jobpulse.applicator import PROFILE

    profile_flat = {**PROFILE, **profile}
    extra_mappings: dict[str, str] = strategy.extra_label_mappings() if strategy is not None else {}
    mapping: dict[str, str] = {}
    unresolved: list[dict] = []

    _placeholder_values = {
        "select one", "select an option", "select", "-- select --",
        "-none-", "loading", "choose", "please select",
    }
    for field in fields:
        cur_val = field.get("value", "")
        is_placeholder = isinstance(cur_val, str) and cur_val.strip().lower() in _placeholder_values
        if field["type"] == "file" or (cur_val and not is_placeholder):
            continue

        label = field["label"]
        normalized_label = strategy.normalize_label(label) if strategy is not None else label
        label_lower = normalized_label.lower()

        custom_value = custom_answers.get(label_lower)
        if isinstance(custom_value, str) and custom_value.strip():
            mapping[label] = _resolve_with_options(custom_value.strip(), field)
            continue

        # Fuzzy custom_answers match (handles diversity fields, paraphrased labels)
        fuzzy_custom = _fuzzy_custom_answer(label_lower, custom_answers)
        if fuzzy_custom is not None:
            mapping[label] = _resolve_with_options(fuzzy_custom, field)
            continue

        profile_key = extra_mappings.get(label_lower) or _FIELD_LABEL_TO_PROFILE_KEY.get(label_lower)
        if not profile_key:
            profile_key = _fuzzy_label_to_profile_key(label_lower)
        if not profile_key and label_lower in profile_flat:
            profile_key = label_lower
        profile_value = profile_flat.get(profile_key, "") if profile_key else ""
        if profile_key == "location":
            _jctx = custom_answers.get("_job_context")
            if isinstance(_jctx, dict):
                job_loc = _jctx.get("location", "")
                if isinstance(job_loc, str) and job_loc.strip():
                    profile_value = job_loc.strip()
        if isinstance(profile_value, str) and profile_value.strip():
            mapping[label] = _resolve_with_options(profile_value.strip(), field)
            if profile_key and label_lower not in _FIELD_LABEL_TO_PROFILE_KEY:
                _FIELD_LABEL_TO_PROFILE_KEY[label_lower] = profile_key
                _persist_label_mapping(label_lower, profile_key)
            continue

        unresolved.append(field)

    return mapping, unresolved


async def map_fields(
    page_url: str, fields: list[dict], profile: dict,
    custom_answers: dict, platform: str,
    known_domain: bool, correction_warning: str,
    domain_field_mappings: dict[str, str] | None = None,
    cached_screening: dict[str, str] | None = None,
) -> tuple[dict, int]:
    """Map profile data to form field labels. Returns (mapping, llm_calls)."""
    llm_calls = 0

    cached = try_cached_mapping(
        page_url, fields, profile, custom_answers, known_domain,
        domain_field_mappings=domain_field_mappings,
    )
    if cached is not None:
        return clean_mapping(cached), 0

    mapping, unresolved = seed_mapping(fields, profile, custom_answers)
    if not unresolved:
        return mapping, 0

    llm_fields = [
        field for field in unresolved
        if field["type"] not in {"select", "combobox", "radio", "checkbox"}
        and "?" not in field["label"]
    ]
    if not llm_fields:
        return mapping, 0

    llm_calls += 1
    field_descriptions = []
    for f in llm_fields:
        desc = f"- {f['label']} ({f['type']})"
        if f.get("options"):
            desc += f" options: {f['options'][:10]}"
        if f.get("value"):
            desc += f" [already filled: {f['value']}]"
        if f.get("required"):
            desc += " *required"
        field_descriptions.append(desc)

    if not field_descriptions:
        return {}, 0

    prompt = (
        f'Map profile data to form fields. Return JSON {{"label": "value"}}.\n'
        f"CRITICAL: JSON keys MUST be the EXACT label text from the Fields list below. "
        f"Do NOT rename, normalize, or invent keys. Only include fields that appear in the list.\n"
        f"Skip fields marked [already filled]. Skip file upload fields.\n\n"
        f"Fields:\n{chr(10).join(field_descriptions)}\n\n"
        f"Profile: {_profile_prompt_json(profile)}\n"
        f"Platform: {platform}\n"
        f"Known answers: {json.dumps({k: v for k, v in custom_answers.items() if not k.startswith('_')})}"
        f"{correction_warning}"
    )
    assert_prompt_has_wrapped_pii(prompt, profile, "applicant.profile")

    try:
        from shared.agents import cognitive_llm_call
        raw = cognitive_llm_call(
            task=prompt,
            domain="form_field_mapping",
            stakes="medium",
        )
        if raw and raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        llm_mapping = clean_mapping(json.loads(raw)) if raw else {}
    except json.JSONDecodeError as e:
        logger.error("LLM returned invalid JSON for field mapping: %s", e)
        return mapping, llm_calls
    except Exception as e:
        logger.error("LLM field mapping call failed: %s", e)
        return mapping, llm_calls

    learn_field_mapping(llm_mapping, profile)
    mapping.update(llm_mapping)

    return mapping, llm_calls


async def map_fields_with_confidence(
    page_url: str, fields: list[dict], profile: dict,
    custom_answers: dict, platform: str,
    known_domain: bool, correction_warning: str,
    domain_field_mappings: dict[str, str] | None = None,
    cached_screening: dict[str, str] | None = None,
) -> tuple[list, int]:
    """Like map_fields() but returns confidence-scored FieldMappings.

    Returns (list[FieldMapping], llm_calls).
    Low-confidence fields are escalated via Best-of-N consensus (System 2).
    """
    from jobpulse.form_engine.confidence_scorer import ConfidenceScorer, FieldMapping

    scorer = ConfidenceScorer()
    llm_calls = 0

    cached = try_cached_mapping(
        page_url, fields, profile, custom_answers, known_domain,
        domain_field_mappings=domain_field_mappings,
    )
    if cached is not None:
        return scorer.score_mappings(cached, source="cached", fields=fields), 0

    mapping, unresolved = seed_mapping(fields, profile, custom_answers)
    scored = scorer.score_mappings(mapping, source="deterministic", fields=fields)

    if not unresolved:
        return scored, 0

    llm_mapping, llm_call_count = await map_fields(
        page_url, fields, profile, custom_answers, platform,
        known_domain, correction_warning,
        domain_field_mappings=domain_field_mappings,
        cached_screening=cached_screening,
    )
    llm_calls += llm_call_count

    llm_only = {k: v for k, v in llm_mapping.items() if k not in mapping}
    llm_scored = scorer.score_mappings(llm_only, source="llm", fields=fields)
    scored.extend(llm_scored)

    low_conf = [fm for fm in scored if not fm.is_confident]
    if low_conf:
        logger.info(
            "AUQ: %d/%d fields below confidence threshold, escalating to System 2",
            len(low_conf), len(scored),
        )
        consensus = scorer.escalate_low_confidence(
            low_confidence_mappings=low_conf,
            fields=fields,
            profile=profile,
            custom_answers=custom_answers,
            platform=platform,
        )
        llm_calls += 1
        for fm in scored:
            if fm.label in consensus:
                fm.value = consensus[fm.label]
                fm.confidence = 0.92
                fm.source = "consensus"

        _emit_escalation_signal(low_conf, platform, page_url)

    return scored, llm_calls


def _emit_escalation_signal(
    low_conf_fields: list, platform: str, page_url: str,
) -> None:
    try:
        from shared.optimization import get_optimization_engine
        get_optimization_engine().emit(
            signal_type="adaptation",
            source_loop="auq_escalation",
            domain=platform,
            agent_name="field_mapper",
            payload={
                "param": "confidence_escalation",
                "field_count": len(low_conf_fields),
                "fields": [fm.label for fm in low_conf_fields],
                "page_url": page_url,
            },
            session_id=f"auq_{platform}",
        )
    except Exception as exc:
        logger.debug("AUQ escalation signal failed: %s", exc)


async def screen_questions(
    unresolved_fields: list[dict], job_context: dict[str, Any] | None,
    profile_store: Any, correction_warning: str,
) -> tuple[dict, int]:
    """Answer screening questions using the v2 ScreeningPipeline (semantic cache + intent + option alignment).

    Falls back to batch LLM if the pipeline returns empty answers for any field.
    Returns (answers, llm_calls).
    """
    from jobpulse.config import APPLICANT_PROFILE
    from jobpulse.screening_pipeline import ScreeningPipeline

    pipeline = ScreeningPipeline(profile=APPLICANT_PROFILE)
    answers: dict[str, str] = {}
    llm_calls = 0

    for field in unresolved_fields:
        label = field["label"]
        # Build field descriptor for option alignment
        field_desc: dict[str, Any] = {
            "type": field.get("type", ""),
            "options": field.get("options") or [],
        }

        result = pipeline.answer(
            question=label,
            field=field_desc,
            job_context=job_context,
        )

        answer = result.get("answer", "")
        source = result.get("source", "unknown")
        confidence = result.get("confidence", 0.0)

        if answer:
            answers[label] = answer
            logger.debug(
                "screen_questions: '%s' → '%s' (source=%s, confidence=%.2f)",
                label, answer, source, confidence,
            )
        else:
            logger.debug("screen_questions: pipeline returned no answer for '%s'", label)

    # Fallback: batch LLM for any fields the pipeline couldn't resolve
    unresolved_after_pipeline = [
        f for f in unresolved_fields if f["label"] not in answers
    ]
    if unresolved_after_pipeline:
        logger.info(
            "ScreeningPipeline resolved %d/%d fields; falling back to LLM for %d",
            len(answers), len(unresolved_fields), len(unresolved_after_pipeline),
        )
        llm_answers, llm_calls = _screen_questions_llm_batch(
            unresolved_after_pipeline, job_context, profile_store, correction_warning
        )
        answers.update(llm_answers)
    else:
        logger.info(
            "ScreeningPipeline resolved all %d screening fields (0 LLM calls)",
            len(unresolved_fields),
        )

    return answers, llm_calls


def _screen_questions_llm_batch(
    unresolved_fields: list[dict], job_context: dict[str, Any] | None,
    profile_store: Any, correction_warning: str,
) -> tuple[dict, int]:
    """Legacy batch LLM fallback for screening questions."""
    questions = []
    for f in unresolved_fields:
        opts = f.get("options")
        if opts and isinstance(opts, list) and len(opts) > 0:
            opts_str = json.dumps(opts[:15])
        else:
            opts_str = "(free text)"
        questions.append(f'- LABEL: "{f["label"]}" | OPTIONS: {opts_str}')

    prompt_profile = _screening_prompt_profile(profile_store)
    applicant_bg = _screening_prompt_background(prompt_profile, profile_store)
    prompt = (
        f"Answer these screening questions for a job application.\n"
        f"Context: {job_context or 'Not provided'}\n"
        f"Applicant: {applicant_bg}\n\n"
        f"Fields:\n{chr(10).join(questions)}\n\n"
        f"CRITICAL: JSON keys MUST be the EXACT text inside the LABEL quotes above — "
        f"do NOT include the word OPTIONS or any surrounding text. "
        f"Choose ONLY from the given options when a list is provided.\n"
        f'Return JSON {{"exact label text": "answer"}}.'
        f"{correction_warning}"
    )
    assert_prompt_has_wrapped_pii(prompt, prompt_profile, "applicant")

    try:
        from shared.agents import cognitive_llm_call
        raw = cognitive_llm_call(
            task=prompt,
            domain="screening_answers",
            stakes="high",
        )
        if raw and raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        answers = clean_mapping(json.loads(raw)) if raw else {}
    except json.JSONDecodeError as e:
        logger.error("LLM returned invalid JSON for screening answers: %s", e)
        return {}, 1
    except Exception as e:
        logger.error("LLM screening answer call failed: %s", e)
        return {}, 1

    try:
        from jobpulse.job_db import JobDB
        db = JobDB()
        for q, a in answers.items():
            db.cache_answer(q, str(a))
        logger.info("Cached %d screening answers from LLM", len(answers))
    except Exception as exc:
        logger.debug("Could not cache screening answers: %s", exc)

    return answers, 1


async def recover_failed_fields_with_llm(
    page_url: str,
    failed_fields: list[dict[str, Any]],
    profile: dict[str, Any],
    custom_answers: dict[str, Any],
    platform: str,
    heuristics_context: str = "",
) -> tuple[dict[str, str], int]:
    """Ask the LLM for alternate values after a DOM fill did not verify. Returns (recovered, llm_calls)."""
    if not failed_fields:
        return {}, 0

    from jobpulse.applicator import PROFILE

    profile_full = {**PROFILE, **profile}
    field_lines: list[str] = []
    for item in failed_fields:
        field = item["field"]
        result = item["result"]
        attempted = item["attempted_value"]
        actual = result.get("actual_value") or "<empty>"
        desc = (
            f"- {field['label']} ({field['type']}) attempted: {attempted!r}; "
            f"actual on page after fill: {actual!r}"
        )
        options = result.get("options_seen") or field.get("options") or []
        if options:
            desc += f"; visible options: {options[:15]}"
        field_lines.append(desc)

    prompt = (
        "A job application field fill did not stick in the DOM. "
        "Suggest alternate values only for fields you can improve.\n"
        f"Platform: {platform}\n"
        f"Job context: {custom_answers.get('_job_context') or 'Not provided'}\n"
        f"Applicant profile: {_profile_prompt_json(profile_full)}\n\n"
        f"Failed fields:\n{chr(10).join(field_lines)}\n\n"
        "Rules:\n"
        "- Return JSON only.\n"
        "- JSON keys must be the exact field labels above.\n"
        "- If options are listed, choose only from those options.\n"
        "- Prefer a different value from the failed attempt when that will help the widget stick.\n"
        "- Omit fields where the failure is browser/widget behavior rather than the value itself.\n"
    )
    if heuristics_context:
        prompt += f"\nLearned heuristics from prior applications:\n{heuristics_context}\n"
    assert_prompt_has_wrapped_pii(prompt, profile_full, "applicant.profile")

    recovered: dict[str, str] = {}
    try:
        from shared.agents import cognitive_llm_call
        raw = cognitive_llm_call(
            task=prompt,
            domain="form_recovery",
            stakes="medium",
        )
        if raw and raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        recovered = clean_mapping(json.loads(raw)) if raw else {}
    except json.JSONDecodeError as exc:
        logger.error("LLM returned invalid JSON for fill recovery: %s", exc)
        return {}, 1
    except Exception as exc:
        logger.error("LLM recovery call failed: %s", exc)
        return {}, 1

    if not recovered:
        return {}, 1

    try:
        from jobpulse.job_db import JobDB

        db = JobDB()
        failed_by_label = {item["field"]["label"]: item for item in failed_fields}
        for label, value in recovered.items():
            item = failed_by_label.get(label)
            if item and is_screening_like_field(item["field"]):
                db.cache_answer(label, value)
            if item:
                attempted = item["attempted_value"]
                actual = item["result"].get("actual_value") or "<empty>"
                save_gotcha(
                    page_url, label,
                    "dom_fill_unverified",
                    f"LLM recovery suggested '{value}' after '{attempted}' verified as '{actual}'",
                )
    except Exception as exc:
        logger.debug("Could not persist LLM recovery learning: %s", exc)

    return recovered, 1




async def _screenshot_form_area(page: "Page") -> bytes:
    """Screenshot the form container if locatable, otherwise the viewport."""
    for selector in ("form", "[role='form']", "#application", ".application-form"):
        try:
            loc = page.locator(selector).first
            if await loc.count() and await loc.is_visible():
                return await loc.screenshot(type="png")
        except Exception:
            continue
    return await page.screenshot(type="png")


async def recover_failed_fields_with_vision(
    page: "Page",
    failed_fields: list[dict[str, Any]],
    profile: dict[str, Any],
    custom_answers: dict[str, Any],
    platform: str,
) -> tuple[dict[str, str], int]:
    """Vision fallback: screenshot the form and ask a vision model. Returns (recovered, llm_calls)."""
    if not failed_fields:
        return {}, 0

    try:
        screenshot_png = await _screenshot_form_area(page)
    except Exception as exc:
        logger.warning("Vision recovery: could not capture screenshot: %s", exc)
        return {}, 0

    from jobpulse.applicator import PROFILE

    profile_full = {**PROFILE, **profile}
    field_lines = []
    for item in failed_fields:
        field = item["field"]
        attempted = item["attempted_value"]
        actual = item["result"].get("actual_value") or "<empty>"
        desc = f"- {field['label']} ({field['type']}) attempted: {attempted!r}, actual: {actual!r}"
        options = item["result"].get("options_seen") or field.get("options") or []
        if options:
            desc += f"; options: {options[:10]}"
        field_lines.append(desc)

    b64_image = base64.b64encode(screenshot_png).decode("ascii")
    job_ctx = custom_answers.get("_job_context") or "Not provided"
    prompt = (
        "Look at this job application form screenshot. "
        "Some fields were not filled correctly. "
        "For each failed field below, identify the field in the screenshot and suggest the correct value.\n\n"
        f"Failed fields:\n{chr(10).join(field_lines)}\n\n"
        f"Applicant: {_profile_prompt_json(profile_full)}\n"
        f"Job context: {job_ctx}\n"
        f"Platform: {platform}\n\n"
        "Rules:\n"
        '- Return JSON only: {{"label": "value"}}.\n'
        "- Keys must be the exact field labels above.\n"
        "- If you can see dropdown options in the screenshot, choose from visible options.\n"
        "- Omit fields you cannot identify in the screenshot.\n"
    )
    assert_prompt_has_wrapped_pii(prompt, profile_full, "applicant.profile")

    client = get_openai_client()
    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{b64_image}",
                    },
                ],
            }],
        )
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="vision_recovery", model_hint="gpt-4.1-mini")
        except Exception:
            pass
        raw = response.output_text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        recovered = clean_mapping(json.loads(raw))
        logger.info("Vision recovery mapped %d fields", len(recovered))
    except json.JSONDecodeError as exc:
        logger.error("Vision recovery returned invalid JSON: %s", exc)
        return {}, 1
    except Exception as exc:
        logger.error("Vision recovery call failed: %s", exc)
        return {}, 1

    return recovered, 1


async def vision_map_unlabeled_fields(
    page: "Page",
    fields: list[dict],
    profile: dict[str, Any],
    custom_answers: dict[str, Any],
    platform: str,
) -> tuple[dict[str, str], int]:
    """Vision fallback for fields with empty/missing labels. Returns (mapping, llm_calls)."""
    unlabeled = [f for f in fields if not f.get("label", "").strip() and f["type"] != "file"]
    if not unlabeled:
        return {}, 0

    try:
        screenshot_png = await _screenshot_form_area(page)
    except Exception as exc:
        logger.warning("Vision unlabeled scan: could not capture screenshot: %s", exc)
        return {}, 0

    from jobpulse.applicator import PROFILE

    profile_full = {**PROFILE, **profile}
    b64_image = base64.b64encode(screenshot_png).decode("ascii")

    field_descs = []
    for i, f in enumerate(unlabeled):
        desc = f"- Field #{i+1} (type: {f['type']})"
        if f.get("value"):
            desc += f" [current value: {f['value']}]"
        if f.get("options"):
            desc += f" options: {f['options'][:10]}"
        field_descs.append(desc)

    job_ctx = custom_answers.get("_job_context") or "Not provided"
    prompt = (
        "Look at this job application form screenshot. "
        f"There are {len(unlabeled)} form fields with no accessible label (shadow DOM). "
        "Identify each field by its visual position and surrounding text in the screenshot.\n\n"
        f"Unlabeled fields:\n{chr(10).join(field_descs)}\n\n"
        f"Applicant profile: {_profile_prompt_json(profile_full)}\n"
        f"Job context: {job_ctx}\n"
        f"Platform: {platform}\n\n"
        "For each field you can identify, return the answer.\n"
        'Return JSON: {{"Field #1": "value", "Field #2": "value"}}.\n'
        "Only include fields you can confidently identify."
    )
    assert_prompt_has_wrapped_pii(prompt, profile_full, "applicant.profile")

    client = get_openai_client()
    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{b64_image}",
                    },
                ],
            }],
        )
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="vision_unlabeled", model_hint="gpt-4.1-mini")
        except Exception:
            pass
        raw = response.output_text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        vision_map = json.loads(raw)
        logger.info("Vision identified %d unlabeled fields", len(vision_map))
    except json.JSONDecodeError as exc:
        logger.error("Vision unlabeled mapping returned invalid JSON: %s", exc)
        return {}, 1
    except Exception as exc:
        logger.error("Vision unlabeled mapping failed: %s", exc)
        return {}, 1

    mapping: dict[str, str] = {}
    for key, value in vision_map.items():
        m = re.match(r"Field #(\d+)", key)
        if not m:
            continue
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(unlabeled):
            label = unlabeled[idx].get("label") or f"_unlabeled_{idx}"
            mapping[label] = str(value).strip()

    return mapping, 1


async def review_form(page: "Page") -> tuple[dict, int]:
    """Screenshot-based pre-submit review. Returns (review_result, llm_calls)."""
    screenshot_bytes = await page.screenshot(type="png")
    b64 = base64.b64encode(screenshot_bytes).decode()

    prompt = (
        "Review this filled application form. Any empty required fields, "
        'wrong values, or mismatches? Return {"pass": true} or '
        '{"pass": false, "issues": [...]}'
    )

    client = get_openai_client()
    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{b64}",
                    },
                ],
            }],
        )
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="field_mapper", model_hint="gpt-4.1-mini")
        except Exception:
            pass

        raw = response.output_text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw), 1
    except json.JSONDecodeError as exc:
        logger.error("Review form returned invalid JSON: %s", exc)
        return {"pass": True, "issues": ["review parse failed"]}, 1
    except Exception as exc:
        logger.error("Review form LLM call failed: %s", exc)
        return {"pass": True, "issues": ["review unavailable"]}, 1
