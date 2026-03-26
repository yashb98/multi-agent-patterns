#!/usr/bin/env python3
"""Convert feature docs to PDF and send to Telegram."""

import re
import subprocess
import json
import os
import sys
from pathlib import Path
from fpdf import FPDF
from dotenv import load_dotenv

base = Path(__file__).parent.parent
load_dotenv(base / ".env")

token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")


def _clean_text(text):
    """Replace unicode chars that Helvetica can't render."""
    replacements = {
        "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u2022": "*",
        "\u2192": "->", "\u2190": "<-", "\u2194": "<->",
        "\u2713": "[x]", "\u2717": "[ ]", "\u00d7": "x",
        "\u2588": "#", "\u2593": "#", "\u2591": ".",
        "\u2550": "=", "\u2500": "-", "\u2502": "|",
        "\u250c": "+", "\u2510": "+", "\u2514": "+", "\u2518": "+",
        "\u251c": "+", "\u2524": "+", "\u252c": "+", "\u2534": "+",
        "\u253c": "+",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Remove any remaining non-latin1 chars
    return text.encode("latin-1", errors="replace").decode("latin-1")


def md_to_pdf(md_path, pdf_path):
    text = md_path.read_text()
    pdf = FPDF(orientation="L", format="A4")  # landscape for wide tables
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_left_margin(10)
    pdf.set_right_margin(10)

    for line in text.split("\n"):
        stripped = _clean_text(line.strip())
        if stripped.startswith("# "):
            pdf.set_font("Helvetica", "B", 16)
            pdf.cell(0, 10, stripped[2:], new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif stripped.startswith("## "):
            pdf.set_font("Helvetica", "B", 13)
            pdf.ln(3)
            pdf.cell(0, 8, stripped[3:], new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif stripped.startswith("### "):
            pdf.set_font("Helvetica", "B", 11)
            pdf.ln(2)
            pdf.cell(0, 7, stripped[4:], new_x="LMARGIN", new_y="NEXT")
        elif stripped.startswith("|"):
            pdf.set_font("Courier", "", 6)
            clean = stripped.replace("**", "").replace("*", "")
            if set(clean.replace("|", "").replace("-", "").replace(" ", "")) == set():
                continue
            # Truncate wide tables
            if len(clean) > 140:
                clean = clean[:140] + "..."
            pdf.cell(0, 4, clean, new_x="LMARGIN", new_y="NEXT")
        elif stripped.startswith("```"):
            pdf.set_font("Courier", "", 7)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            pdf.set_font("Helvetica", "", 9)
            clean = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)
            pdf.cell(0, 5, "  " + clean[:110], new_x="LMARGIN", new_y="NEXT")
        elif stripped:
            pdf.set_font("Helvetica", "", 9)
            clean = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)
            clean = re.sub(r"`(.+?)`", r"\1", clean)
            try:
                pdf.multi_cell(0, 5, clean)
            except Exception:
                # If line too wide, force wrap by truncating
                pdf.cell(0, 5, clean[:150], new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.ln(2)

    pdf.output(str(pdf_path))
    return pdf_path.stat().st_size


def send_to_telegram(pdf_path, caption):
    result = subprocess.run(
        ["curl", "-s", "-X", "POST",
         f"https://api.telegram.org/bot{token}/sendDocument",
         "-F", f"chat_id={chat_id}",
         "-F", f"document=@{pdf_path}",
         "-F", f"caption={caption}"],
        capture_output=True, text=True, timeout=15
    )
    resp = json.loads(result.stdout)
    return resp.get("ok", False)


docs = [
    ("docs/feature-auto-job-applier.md", "data/auto-job-applier.pdf",
     "Auto Job Applier Design Doc - Adzuna + Reed + RemoteOK, scoring, cover letters, Telegram approval."),
    ("docs/feature-arxiv-digest.md", "data/arxiv-digest.pdf",
     "arXiv Research Digest Design Doc - 2-stage ranking, GRPO summaries, knowledge graph, persona evolution."),
]

for md_file, pdf_file, caption in docs:
    md_path = base / md_file
    pdf_path = base / pdf_file
    try:
        size = md_to_pdf(md_path, pdf_path)
        print(f"{pdf_path.name}: {size} bytes")
        ok = send_to_telegram(str(pdf_path), caption)
        print(f"  Sent: {ok}")
    except Exception as e:
        print(f"  Error: {e}")
