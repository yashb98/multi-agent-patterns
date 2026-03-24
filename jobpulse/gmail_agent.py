"""Gmail agent — classifies recruiter emails using LLM, tracks in SQLite, sends Telegram alerts."""

import json
import base64
from datetime import datetime
from openai import OpenAI
from jobpulse.config import OPENAI_API_KEY, GOOGLE_TOKEN_PATH
from jobpulse import db
from jobpulse import telegram_agent
from jobpulse import event_logger
from jobpulse import auto_extract

# Categories
SELECTED = "SELECTED_NEXT_ROUND"
INTERVIEW = "INTERVIEW_SCHEDULING"
REJECTED = "REJECTED"
OTHER = "OTHER"

CATEGORY_EMOJI = {
    SELECTED: "✅ SELECTED",
    INTERVIEW: "📅 INTERVIEW",
    REJECTED: "❌ REJECTED",
}


def _get_gmail_service():
    """Build Gmail API service using stored OAuth2 token."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        import os

        creds = None
        if os.path.exists(GOOGLE_TOKEN_PATH):
            creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_PATH,
                ["https://www.googleapis.com/auth/gmail.readonly"])

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(GOOGLE_TOKEN_PATH, "w") as f:
                    f.write(creds.to_json())
            else:
                print("[Gmail] No valid credentials. Run: python scripts/setup_integrations.py")
                return None

        return build("gmail", "v1", credentials=creds)
    except ImportError:
        print("[Gmail] Install: pip install google-auth-oauthlib google-api-python-client")
        return None
    except Exception as e:
        print(f"[Gmail] Auth error: {e}")
        return None


def _classify_email(subject: str, body_snippet: str) -> str:
    """Use LLM to classify an email into one of 4 categories."""
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""Classify this email into EXACTLY ONE category:

SELECTED_NEXT_ROUND — congratulations, selected, moving forward, pleased to inform, progressed, next stage, shortlisted
INTERVIEW_SCHEDULING — availability, schedule an interview, book a slot, calendar link, time slots, when are you free
REJECTED — unfortunately, regret to inform, not selected, decided not to proceed, other candidates, not moving forward
OTHER — newsletters, promotions, social media, receipts, anything NOT about job applications

Email subject: {subject}
Email body (first 500 chars): {body_snippet[:500]}

Respond with ONLY the category name. Nothing else."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
            temperature=0,
        )
        category = response.choices[0].message.content.strip().upper()
        # Normalize
        if "SELECTED" in category:
            return SELECTED
        elif "INTERVIEW" in category or "SCHEDULING" in category:
            return INTERVIEW
        elif "REJECTED" in category:
            return REJECTED
        return OTHER
    except Exception as e:
        print(f"[Gmail] LLM classification error: {e}")
        return OTHER


def _extract_body(payload: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        # Recurse into nested parts
        if part.get("parts"):
            result = _extract_body(part)
            if result:
                return result
    return ""


def check_emails() -> list[dict]:
    """Main entry: fetch new emails, classify, store, alert. Returns classified recruiter emails."""
    service = _get_gmail_service()
    if not service:
        return []

    last_check = db.get_last_check_ts()
    now = datetime.now().isoformat()
    new_recruiter_emails = []

    try:
        # Fetch messages since last check
        query = f"after:{last_check[:10]} in:inbox"
        results = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
        messages = results.get("messages", [])

        print(f"[Gmail] Found {len(messages)} messages since {last_check[:10]}")

        for msg_meta in messages:
            msg_id = msg_meta["id"]

            # Skip if already processed
            if db.is_email_processed(msg_id):
                continue

            # Fetch full message
            msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}

            subject = headers.get("subject", "(no subject)")
            sender = headers.get("from", "unknown")
            date_str = headers.get("date", now)
            body = _extract_body(msg.get("payload", {}))[:500]

            # Classify
            category = _classify_email(subject, body)

            # Store in SQLite (all categories, for dedup)
            db.store_email(msg_id, sender, subject, category, body[:200], date_str)

            # Alert for recruiter categories only
            if category != OTHER:
                emoji_label = CATEGORY_EMOJI.get(category, category)
                sender_short = sender.split("<")[0].strip() if "<" in sender else sender
                new_recruiter_emails.append({
                    "id": msg_id, "sender": sender_short,
                    "subject": subject, "category": category
                })

                # Instant Telegram alert
                alert = f"📧 RECRUITER UPDATE\n\n{emoji_label}: {sender_short}\n\"{subject}\""
                if category == SELECTED:
                    alert += "\n\n🎉 Congratulations!"
                elif category == INTERVIEW:
                    alert += "\n\n🚨 Action needed — reply to schedule!"
                elif category == REJECTED:
                    alert += "\n\nOnward to the next one 💪"

                telegram_agent.send_message(alert)
                print(f"[Gmail] {emoji_label}: {sender_short} — {subject}")

                # Log to simulation events
                event_logger.log_event(
                    event_type="email_classified",
                    agent_name="gmail_agent",
                    action=f"classified_{category.lower()}",
                    content=f"{emoji_label}: {sender_short} — {subject}",
                    metadata={"subject": subject, "sender": sender, "category": category, "email_id": msg_id},
                )

                # Auto-extract knowledge (company names, roles, etc.)
                try:
                    auto_extract.extract_from_email(sender, subject, category, body[:500])
                except Exception:
                    pass  # extraction is best-effort, don't block email processing

    except Exception as e:
        print(f"[Gmail] Error fetching emails: {e}")

    # Update last check timestamp
    db.update_last_check_ts(now)

    if not new_recruiter_emails:
        print("[Gmail] No new recruiter emails")

    return new_recruiter_emails


def get_yesterday_recruiter_emails() -> list[dict]:
    """Get yesterday's recruiter emails from SQLite for morning digest."""
    from datetime import timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    return db.get_emails_since(yesterday, [SELECTED, INTERVIEW, REJECTED])
