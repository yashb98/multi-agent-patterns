"""Interview Prep — STAR+Reflection story generation from JD skills and candidate projects.

Maps required JD skills to candidate projects and generates STAR+R story templates
for interview preparation. No LLM calls — all deterministic.

Public API:
  map_skills_to_stories(required_skills, projects) -> {skill: {project, description}}
  build_star_story(skill, project, description) -> dict
  generate_prep_report(company, role, required_skills, projects) -> dict
  format_prep_telegram(report) -> str
"""

from __future__ import annotations

from shared.logging_config import get_logger

logger = get_logger(__name__)


def map_skills_to_stories(
    required_skills: list[str],
    projects: list[dict],
) -> dict[str, dict]:
    """Map each required skill to the best matching project.

    For each required skill, finds the project whose skills list has the most
    overlap with ALL required skills (best context fit). Returns only skills
    that matched at least one project.

    Args:
        required_skills: Skills extracted from the JD.
        projects: List of dicts with 'name', 'description', 'skills' keys.

    Returns:
        Dict mapping skill -> {'project': name, 'description': description}.
    """
    mapped: dict[str, dict] = {}

    for skill in required_skills:
        skill_lower = skill.lower()
        best_project = None
        best_overlap = 0

        for project in projects:
            project_skills = [s.lower() for s in project.get("skills", [])]
            if skill_lower not in project_skills:
                continue

            overlap = sum(
                1 for req in required_skills if req.lower() in project_skills
            )
            if overlap > best_overlap:
                best_overlap = overlap
                best_project = project

        if best_project is not None:
            mapped[skill] = {
                "project": best_project["name"],
                "description": best_project.get("description", ""),
            }

    logger.debug("Mapped %d/%d skills to projects", len(mapped), len(required_skills))
    return mapped


def build_star_story(skill: str, project: str, description: str) -> dict:
    """Build a STAR+Reflection story template for a skill/project pair.

    Returns template strings — placeholders for the candidate to fill in.
    Not LLM-generated.

    Args:
        skill: The skill being demonstrated.
        project: The project name.
        description: A brief project description.

    Returns:
        Dict with keys: skill, project, situation, task, action, result, reflection.
    """
    return {
        "skill": skill,
        "project": project,
        "situation": (
            f"While working on {project}, {description.rstrip('.')}."
            if description
            else f"While working on {project}."
        ),
        "task": (
            f"I needed to apply {skill} to solve a core challenge in the project."
        ),
        "action": (
            f"I used {skill} to [describe specific steps taken, tools used, "
            f"decisions made]."
        ),
        "result": (
            "This led to [quantified outcome: e.g. X% improvement, Y hours saved, "
            "Z reduction in errors]."
        ),
        "reflection": (
            f"Looking back, I would [describe what you learned or would do differently] "
            f"to deepen my {skill} expertise further."
        ),
    }


def generate_prep_report(
    company: str,
    role: str,
    required_skills: list[str],
    projects: list[dict],
) -> dict:
    """Generate a full interview prep report for a company/role.

    Args:
        company: Target company name.
        role: Job role title.
        required_skills: Skills extracted from the JD.
        projects: Candidate's projects (each with 'name', 'description', 'skills').

    Returns:
        Dict with: company, role, skill_coverage, mapped_skills,
                   star_stories, unmapped_skills, gap_mitigation.
    """
    mapped = map_skills_to_stories(required_skills, projects)
    unmapped = [s for s in required_skills if s not in mapped]

    star_stories = [
        build_star_story(skill, info["project"], info["description"])
        for skill, info in mapped.items()
    ]

    total = len(required_skills)
    covered = len(mapped)
    skill_coverage = f"{covered}/{total}"

    gap_mitigation = [
        f"Prepare a learning plan or side project to demonstrate {skill}."
        for skill in unmapped
    ]

    logger.info(
        "Prep report for %s @ %s: %s skills covered, %d gaps",
        role,
        company,
        skill_coverage,
        len(unmapped),
    )

    return {
        "company": company,
        "role": role,
        "skill_coverage": skill_coverage,
        "mapped_skills": mapped,
        "star_stories": star_stories,
        "unmapped_skills": unmapped,
        "gap_mitigation": gap_mitigation,
    }


def format_prep_telegram(report: dict) -> str:
    """Format an interview prep report as a Telegram message.

    Args:
        report: Dict returned by generate_prep_report().

    Returns:
        Formatted string suitable for Telegram.
    """
    lines = [
        f"*Interview Prep: {report['role']} @ {report['company']}*",
        f"Skill Coverage: {report['skill_coverage']}",
        "",
    ]

    if report["star_stories"]:
        lines.append("*STAR Stories*")
        for story in report["star_stories"]:
            lines.append(f"\n*{story['skill']}* ({story['project']})")
            lines.append(f"S: {story['situation']}")
            lines.append(f"T: {story['task']}")
            lines.append(f"A: {story['action']}")
            lines.append(f"R: {story['result']}")
            lines.append(f"Reflection: {story['reflection']}")

    if report["unmapped_skills"]:
        lines.append("\n*Skill Gaps*")
        for skill in report["unmapped_skills"]:
            lines.append(f"- {skill}")

        lines.append("\n*Gap Mitigation*")
        for tip in report["gap_mitigation"]:
            lines.append(f"- {tip}")

    return "\n".join(lines)


def fetch_interview_questions(company: str, role: str, max_results: int = 5) -> list[dict]:
    """Fetch common interview questions via SearXNG. Returns empty list if unavailable."""
    try:
        from shared.searxng_client import search_smart
        results = search_smart(
            f"{company} {role} interview questions",
            context="general",
            max_results=max_results,
        )
        return [{"title": r.title, "url": r.url, "content": r.content[:300]} for r in results]
    except Exception as e:
        logger.debug("Interview question fetch failed: %s", e)
        return []
