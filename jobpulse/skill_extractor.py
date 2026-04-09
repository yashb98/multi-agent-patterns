"""Rule-based skill extractor with LLM fallback.

Two-pass extraction from JD text:
1. Section detection + taxonomy matching against skill_synonyms.json
2. LLM fallback (GPT-4o-mini) when < 10 skills extracted
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from shared.logging_config import get_logger

# Soft skills to filter out — we focus on technical skills only
_SOFT_SKILLS = {
    "project management", "communication", "teamwork", "leadership",
    "problem solving", "time management", "adaptability", "collaboration",
    "analytical thinking", "critical thinking", "stakeholder management",
    "presentation skills", "mentoring", "coaching", "prioritization",
    "attention to detail", "self motivated", "fast learner",
    "team collaboration", "cross functional", "negotiation",
    "decision making", "conflict resolution", "emotional intelligence",
    "creativity", "interpersonal skills", "organizational skills",
    "work ethic", "flexibility", "multitasking", "strategic thinking",
}

logger = get_logger(__name__)

SYNONYMS_PATH: str = str(Path(__file__).parent.parent / "data" / "skill_synonyms.json")

# Section header patterns
_REQUIRED_HEADERS = re.compile(
    r"^#+\s*(?:Requirements?|Essential|Must\s+Have|Qualifications?|What\s+You'?ll\s+Need)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_PREFERRED_HEADERS = re.compile(
    r"^#+\s*(?:Nice\s+to\s+Have|Preferred|Bonus|Desirable)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_ANY_HEADER = re.compile(r"^#+\s+", re.MULTILINE)

# Industry keywords
_INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "fintech": ["fintech", "financial technology", "banking", "payments", "trading platform"],
    "healthtech": ["healthtech", "health tech", "healthcare", "medical", "clinical", "biotech"],
    "gaming": ["gaming", "game development", "game engine", "gamedev", "esports"],
    "edtech": ["edtech", "education technology", "e-learning", "elearning"],
    "ecommerce": ["ecommerce", "e-commerce", "retail tech", "marketplace"],
    "cybersecurity": ["cybersecurity", "cyber security", "infosec", "security"],
    "ai/ml": ["artificial intelligence", "deep learning", "neural network", "nlp", "computer vision"],
    "devops": ["devops", "site reliability", "sre", "platform engineering"],
    "cloud": ["cloud computing", "cloud native", "cloud infrastructure"],
    "blockchain": ["blockchain", "web3", "cryptocurrency", "crypto", "defi"],
    "saas": ["saas", "software as a service"],
    "data engineering": ["data engineering", "data pipeline", "etl", "data warehouse"],
}


def _normalize(text: str) -> str:
    """Lowercase, strip, replace hyphens/underscores with spaces."""
    return text.lower().strip().replace("-", " ").replace("_", " ")


def _load_synonyms() -> dict[str, list[str]]:
    """Load skill synonyms from JSON file."""
    try:
        with open(SYNONYMS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("Failed to load synonyms from %s: %s", SYNONYMS_PATH, e)
        return {}


def detect_jd_sections(jd_text: str) -> dict[str, str]:
    """Detect required/preferred sections in JD text.

    Returns dict with keys from {"required", "preferred", "unsectioned"}.
    """
    required_matches = list(_REQUIRED_HEADERS.finditer(jd_text))
    preferred_matches = list(_PREFERRED_HEADERS.finditer(jd_text))

    if not required_matches and not preferred_matches:
        return {"unsectioned": jd_text}

    # Collect all section boundaries (header start positions)
    all_headers = list(_ANY_HEADER.finditer(jd_text))
    header_positions = sorted({m.start() for m in all_headers})

    def _get_section_text(match: re.Match) -> str:  # type: ignore[type-arg]
        """Extract text from a header match to the next header or end of text."""
        start = match.end()
        # Find next header after this one
        next_pos = len(jd_text)
        for pos in header_positions:
            if pos > match.start():
                next_pos = pos
                break
        return jd_text[start:next_pos].strip()

    sections: dict[str, str] = {}

    if required_matches:
        sections["required"] = _get_section_text(required_matches[0])
    if preferred_matches:
        sections["preferred"] = _get_section_text(preferred_matches[0])

    return sections


def extract_skills_rule_based(jd_text: str) -> dict:
    """Extract skills using taxonomy matching against skill_synonyms.json.

    Returns:
        dict with required_skills, preferred_skills, industry, sub_context, source.
    """
    synonyms = _load_synonyms()
    sections = detect_jd_sections(jd_text)

    # Build lookup: normalized synonym/canonical -> canonical name
    lookup: dict[str, str] = {}
    for canonical, syns in synonyms.items():
        norm_canonical = _normalize(canonical)
        lookup[norm_canonical] = canonical
        for syn in syns:
            lookup[_normalize(syn)] = canonical

    def _match_skills(text: str) -> list[str]:
        """Find skills in text using word boundary or substring matching."""
        norm_text = _normalize(text)
        found: set[str] = set()
        for term, canonical in lookup.items():
            if " " in term:
                # Multi-word: substring match
                if term in norm_text:
                    found.add(canonical)
            else:
                # Single word: word boundary match
                if re.search(r"\b" + re.escape(term) + r"\b", norm_text):
                    found.add(canonical)
        return sorted(found)

    required_skills: list[str] = []
    preferred_skills: list[str] = []

    if "required" in sections:
        required_skills = _match_skills(sections["required"])
    if "preferred" in sections:
        preferred_skills = _match_skills(sections["preferred"])
    if "unsectioned" in sections:
        # All skills go to required when no sections detected
        required_skills = _match_skills(sections["unsectioned"])

    # Deprioritize soft skills — technical skills first, soft skills at the end
    required_tech = [s for s in required_skills if _normalize(s) not in _SOFT_SKILLS]
    required_soft = [s for s in required_skills if _normalize(s) in _SOFT_SKILLS]
    required_skills = required_tech + required_soft

    preferred_tech = [s for s in preferred_skills if _normalize(s) not in _SOFT_SKILLS]
    preferred_soft = [s for s in preferred_skills if _normalize(s) in _SOFT_SKILLS]
    preferred_skills = preferred_tech + preferred_soft

    # Industry detection
    industry = _detect_industry(jd_text)

    return {
        "required_skills": required_skills,
        "preferred_skills": preferred_skills,
        "industry": industry,
        "sub_context": "",
        "source": "rule_based",
    }


def _detect_industry(jd_text: str) -> str:
    """Detect industry from keywords in JD text."""
    norm = _normalize(jd_text)
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        for kw in keywords:
            if kw in norm:
                return industry
    return "general"


def extract_skills_hybrid(jd_text: str) -> dict:
    """Extract skills with rule-based first, LLM fallback if < 10 skills found.

    Returns same dict as extract_skills_rule_based but source may be "llm_fallback".
    """
    result = extract_skills_rule_based(jd_text)
    total = len(result["required_skills"]) + len(result["preferred_skills"])

    if total >= 10:
        logger.info("Rule-based extracted %d skills, skipping LLM", total)
        return result

    logger.info("Rule-based extracted only %d skills, falling back to LLM", total)
    return _extract_skills_llm(jd_text)


def _extract_skills_llm(jd_text: str) -> dict:
    """LLM fallback using GPT-4o-mini for vague JDs.

    Truncates JD to 4000 chars. Temperature 0.0. JSON response format.
    """
    from shared.agents import get_openai_client

    client = get_openai_client()

    truncated = jd_text[:4000]

    system_prompt = (
        "You are a job description parser. Extract skills from the following JD. "
        "Return a JSON object with these keys:\n"
        '- "required_skills": list of strings (hard skills explicitly required)\n'
        '- "preferred_skills": list of strings (nice-to-have skills)\n'
        '- "industry": string (e.g. fintech, healthtech, gaming, general)\n'
        '- "sub_context": string (brief context about the role)\n'
        "Be thorough. Include both technical and soft skills mentioned."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": truncated},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)

        return {
            "required_skills": parsed.get("required_skills", []),
            "preferred_skills": parsed.get("preferred_skills", []),
            "industry": parsed.get("industry", "general"),
            "sub_context": parsed.get("sub_context", ""),
            "source": "llm_fallback",
        }
    except Exception as e:
        logger.error("LLM skill extraction failed: %s", e)
        return {
            "required_skills": [],
            "preferred_skills": [],
            "industry": "general",
            "sub_context": "",
            "source": "llm_fallback",
        }
