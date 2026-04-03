"""Vision tier — screenshot analysis via GPT-4o-mini for stuck form fields.

Used as Tier 5 when pattern match, semantic cache, Gemini Nano, and LLM
all fail to produce a confident answer. Typically triggered ~5% of applications.
"""

from __future__ import annotations

import base64

from openai import OpenAI
from shared.logging_config import get_logger

from jobpulse.config import OPENAI_API_KEY

logger = get_logger(__name__)


def _build_vision_prompt(question: str, input_type: str) -> str:
    """Build the vision analysis prompt."""
    return (
        "You are filling out a job application form. "
        f'The current field asks: "{question}" (input type: {input_type}). '
        "Look at the screenshot of the form and determine the best answer. "
        "The applicant is Yash Bishnoi, MSc Computer Science, ML Engineer, "
        "based in Dundee UK with Graduate Visa. "
        "Return ONLY the answer value — no explanation, no quotes, no formatting."
    )


async def analyze_field_screenshot(
    question: str,
    screenshot_png: bytes,
    input_type: str,
) -> str | None:
    """Send a screenshot to GPT-4o-mini and extract the answer.

    Args:
        question: The field label/question text.
        screenshot_png: Raw PNG bytes of the page screenshot.
        input_type: HTML input type (text, select, radio, etc.).

    Returns:
        The answer string, or None if analysis fails.
    """
    if not OPENAI_API_KEY:
        logger.debug("Vision tier skipped — no OPENAI_API_KEY")
        return None

    try:
        b64_image = base64.b64encode(screenshot_png).decode("ascii")
        client = OpenAI(api_key=OPENAI_API_KEY)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _build_vision_prompt(question, input_type)},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64_image}"},
                        },
                    ],
                }
            ],
            max_tokens=100,
            temperature=0.2,
        )

        answer = response.choices[0].message.content.strip()
        logger.debug("Vision tier answer for '%s': '%s'", question[:60], answer[:80])
        return answer if answer else None

    except Exception as exc:
        logger.warning("Vision tier failed: %s", exc)
        return None
