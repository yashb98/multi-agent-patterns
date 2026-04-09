#!/usr/bin/env python3
"""Convert feature docs to PDF and send to Telegram."""

import subprocess
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

base = Path(__file__).parent.parent
sys.path.insert(0, str(base))
load_dotenv(base / ".env")

from shared.telegram_client import telegram_url

token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")


def send_to_telegram(pdf_path, caption):
    result = subprocess.run(
        ["curl", "-s", "-X", "POST",
         telegram_url(token, "sendDocument"),
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
