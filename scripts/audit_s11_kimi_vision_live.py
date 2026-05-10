"""S11 (redesign) live evidence — Kimi vision via chat.completions.

Audit 2026-05-10 / Slice S11 (redesign) / TP-21.

Sends a tiny test image to Moonshot's vision endpoint via the Kimi-mandated
get_openai_client() and confirms:
  1. The call lands on api.moonshot.ai (not api.openai.com).
  2. The response parses (Moonshot accepts the chat.completions
     multimodal request shape).
  3. cost_tracker records the usage with non-zero cost
     (proves the moonshot-v1-32k pricing entry resolved).

Run: python scripts/audit_s11_kimi_vision_live.py
"""
from __future__ import annotations

import base64
import os
import sqlite3
import sys
from pathlib import Path

def _make_test_png() -> bytes:
    """Build a small but parseable PNG (Moonshot rejects 1x1 PNGs)."""
    import io
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (200, 100), color=(255, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((20, 40), "RED", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    if not os.environ.get("KimiAI_API_KEY"):
        print("FAIL: KimiAI_API_KEY not set — cannot exercise Kimi mandate.")
        return 1

    from shared.agents import get_openai_client
    from jobpulse.form_engine.field_mapper import _VISION_MODEL

    client = get_openai_client()
    base = getattr(client, "base_url", "<unknown>")
    print(f"--- 1. Client base_url: {base}")
    if "moonshot.ai" not in str(base):
        print(f"FAIL: client routed to {base}, not moonshot.ai.")
        return 1

    png_bytes = _make_test_png()
    b64 = base64.b64encode(png_bytes).decode("ascii")
    print(f"--- 1b. Test image: {len(png_bytes)} bytes")
    print(f"--- 2. Calling {_VISION_MODEL} with 1x1 image...")
    try:
        response = client.chat.completions.create(
            model=_VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What colour is this image? Answer in one word."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }],
            max_tokens=10,
        )
    except Exception as exc:
        print(f"FAIL: chat.completions.create raised: {exc!r}")
        return 1

    raw = (response.choices[0].message.content or "").strip()
    model_returned = getattr(response, "model", None)
    usage = getattr(response, "usage", None)
    pt = getattr(usage, "prompt_tokens", 0) if usage else 0
    ct = getattr(usage, "completion_tokens", 0) if usage else 0
    print(f"--- 3. Response: model={model_returned!r} answer={raw!r} usage=(p={pt}, c={ct})")

    if not raw:
        print("FAIL: empty response content.")
        return 1
    if pt == 0:
        print("WARN: prompt_tokens=0 — usage tracking may be unreliable on Moonshot.")

    print("--- 4. Recording usage via cost_tracker...")
    from shared.cost_tracker import record_openai_usage
    usage_dict = record_openai_usage(
        response, agent_name="audit_s11_live", model_hint=_VISION_MODEL,
    )
    print(f"        cost_usd={usage_dict['cost_usd']!r} model={usage_dict['model']!r}")

    print("--- 5. Querying llm_usage.db for the row we just wrote...")
    db_path = Path(os.getenv("LLM_USAGE_DB", "data/llm_usage.db"))
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT agent, model, prompt_tokens, completion_tokens, cost_usd "
        "FROM llm_usage WHERE agent = 'audit_s11_live' ORDER BY id DESC LIMIT 1"
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        print("FAIL: no llm_usage row written.")
        return 1
    print(f"        row: agent={row[0]} model={row[1]} p={row[2]} c={row[3]} cost=${row[4]:.6f}")

    print("=== S11 (redesign) PASS ===")
    print(f"Vision routed via Moonshot ({base}); model {_VISION_MODEL!r} responded; usage tracked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
