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
from jobpulse.email_preclassifier import preclassify, PreClassification
from shared.logging_config import get_logger

logger = get_logger(__name__)

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
                logger.warning("No valid credentials. Run: python scripts/setup_integrations.py")
                return None

        return build("gmail", "v1", credentials=creds)
    except ImportError:
        logger.warning("Install: pip install google-auth-oauthlib google-api-python-client")
        return None
    except Exception as e:
        logger.error("Auth error: %s", e)
        return None


def _classify_email(subject: str, body_snippet: str) -> str:
    """Use LLM to classify an email into one of 4 categories. Uses evolved persona if available."""
    client = OpenAI(api_key=OPENAI_API_KEY)

    # Inject evolved persona learnings
    extra_context = ""
    try:
        from jobpulse.persona_evolution import get_evolved_prompt
        evolved = get_evolved_prompt("gmail_agent")
        if evolved:
            extra_context = f"\n\nLearned patterns:\n{evolved}\n"
    except Exception as e:
        logger.debug("Persona evolution unavailable: %s", e)

    prompt = f"""Classify this email into EXACTLY ONE category:{extra_context}

SELECTED_NEXT_ROUND — congratulations, selected, moving forward, pleased to inform, progressed, next stage, shortlisted
INTERVIEW_SCHEDULING — availability, schedule an interview, book a slot, calendar link, time slots, when are you free
REJECTED — unfortunately, regret to inform, not selected, decided not to proceed, other candidates, not moving forward
OTHER — newsletters, promotions, social media, receipts, anything NOT about job applications

Email subject: {subject}
Email body (first 500 chars): {body_snippet[:500]}

Respond with ONLY the category name. Nothing else."""

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
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
        logger.error("LLM classification error: %s", e)
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


def check_emails(trigger: str = "scheduled_check") -> list[dict]:
    """Main entry: fetch new emails, classify, store, alert. Returns classified recruiter emails."""
    from jobpulse.process_logger import ProcessTrail
    trail = ProcessTrail("gmail_agent", trigger)

    # Step 1: Connect to Gmail
    with trail.step("api_call", "Connect to Gmail API") as s:
        service = _get_gmail_service()
        if not service:
            s["output"] = "No valid credentials"
            trail.finalize("Failed: no Gmail credentials")
            return []
        s["output"] = "Connected successfully"

    last_check = db.get_last_check_ts()
    now = datetime.now().isoformat()
    new_recruiter_emails = []

    try:
        # Step 2: Fetch inbox
        with trail.step("api_call", "Fetch inbox since last check",
                         step_input=f"Since: {last_check[:10]}") as s:
            query = f"after:{last_check[:10]} in:inbox"
            results = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
            messages = results.get("messages", [])
            s["output"] = f"Found {len(messages)} messages"
            s["metadata"] = {"email_count": len(messages)}

        logger.info("Found %d messages since %s", len(messages), last_check[:10])

        for i, msg_meta in enumerate(messages):
            msg_id = msg_meta["id"]

            # Skip if already processed
            if db.is_email_processed(msg_id):
                continue

            # Step: Read email
            with trail.step("api_call", f"Read email #{i+1}",
                             step_input=f"Email ID: {msg_id}") as s:
                msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
                headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
                subject = headers.get("subject", "(no subject)")
                sender = headers.get("from", "unknown")
                date_str = headers.get("date", now)
                body = _extract_body(msg.get("payload", {}))[:500]
                s["output"] = f"From: {sender}\nSubject: {subject}"

            # Step: Pre-classify with rules
            with trail.step("decision", f"Pre-classify email #{i+1}",
                             step_input=f"Sender: {sender}\nSubject: {subject}") as s:
                pre = preclassify(sender, subject, body)
                s["output"] = f"Pre-class: {pre.category or 'PASS-THROUGH'} (conf={pre.confidence:.2f})"
                s["decision"] = pre.evidence.get("reasoning") or "No rule matched — sending to LLM"
                s["metadata"] = {
                    "rule_name": pre.evidence.get("rule_name"),
                    "confidence": pre.confidence,
                    "likely_recruiter": pre.likely_recruiter,
                    "skip_llm": pre.skip_llm,
                }

            # Use pre-classifier result or fall back to LLM
            if pre.skip_llm and pre.category:
                category = pre.category
                logger.info("Pre-classified %s as %s (skip LLM, conf=%.2f)",
                           subject[:50], category, pre.confidence)
                # Audit check: sometimes verify rule decisions with LLM
                from jobpulse.email_preclassifier import should_audit, record_audit
                if should_audit(pre):
                    with trail.step("llm_call", f"Audit email #{i+1}",
                                     step_input=f"Auditing rule: {pre.evidence.get('rule_name')}") as s:
                        llm_category = _classify_email(subject, body)
                        is_correct = record_audit(msg_id, pre, llm_category)
                        s["output"] = f"Audit: rule={category}, LLM={llm_category}, correct={is_correct}"
                        s["decision"] = "Audit passed" if is_correct else f"MISMATCH: using LLM result {llm_category}"
                        if not is_correct:
                            category = llm_category  # Trust LLM over rule on mismatch
            else:
                # Step: Classify with LLM (no rule match or low confidence)
                with trail.step("llm_call", f"Classify email #{i+1}",
                                 step_input=f"Subject: {subject}\nBody: {body[:200]}") as s:
                    category = _classify_email(subject, body)
                    s["output"] = f"LLM classification: {category}"
                    s["decision"] = f"LLM classified as {category}"
                    if pre.likely_recruiter:
                        s["metadata"] = {"category": category, "sender": sender, "recruiter_hint": True}
                    else:
                        s["metadata"] = {"category": category, "sender": sender}

            # Learning phase: extract patterns from pre-classified emails
            from jobpulse.email_preclassifier import is_learning_phase, extract_patterns_from_email, increment_processed
            if is_learning_phase() and pre.skip_llm:
                with trail.step("llm_call", f"Learning: analyze email #{i+1} patterns",
                                 step_input=f"Category: {category}") as s:
                    try:
                        patterns = extract_patterns_from_email(sender, subject, body, category)
                        s["output"] = f"Patterns: {json.dumps(patterns.get('key_signals', []))}"
                        s["metadata"] = patterns
                    except Exception as e:
                        s["output"] = f"Pattern extraction skipped: {e}"
            increment_processed()

            # Step: Store
            with trail.step("api_call", f"Store email #{i+1} in SQLite") as s:
                db.store_email(msg_id, sender, subject, category, body[:200], date_str)
                s["output"] = "Stored successfully"

            # Alert for recruiter categories only
            if category != OTHER:
                emoji_label = CATEGORY_EMOJI.get(category, category)
                sender_short = sender.split("<")[0].strip() if "<" in sender else sender
                new_recruiter_emails.append({
                    "id": msg_id, "sender": sender_short,
                    "subject": subject, "category": category
                })

                # Step: Telegram alert
                with trail.step("api_call", f"Send Telegram alert for {category}",
                                 step_input=f"{sender_short}: {subject}") as s:
                    alert = f"📧 RECRUITER UPDATE\n\n{emoji_label}: {sender_short}\n\"{subject}\""
                    if category == SELECTED:
                        alert += "\n\n🎉 Congratulations!"
                    elif category == INTERVIEW:
                        alert += "\n\n🚨 Action needed — reply to schedule!"
                    elif category == REJECTED:
                        alert += "\n\nOnward to the next one 💪"
                    # Send to alert bot (dedicated alerts chat)
                    from jobpulse.telegram_bots import send_alert
                    send_alert(alert)
                    s["output"] = f"Alert sent for {category}"

                logger.info("%s: %s — %s", emoji_label, sender_short, subject)

                # Log to simulation events
                event_logger.log_event(
                    event_type="email_classified",
                    agent_name="gmail_agent",
                    action=f"classified_{category.lower()}",
                    content=f"{emoji_label}: {sender_short} — {subject}",
                    metadata={"subject": subject, "sender": sender, "category": category, "email_id": msg_id},
                )

                # Step: Extract knowledge
                with trail.step("extraction", f"Extract knowledge from email #{i+1}",
                                 step_input=f"{sender} — {subject}") as s:
                    try:
                        auto_extract.extract_from_email(sender, subject, category, body[:500])
                        s["output"] = f"Extracted knowledge for {sender_short}"
                    except Exception:
                        s["output"] = "Extraction skipped (best-effort)"

                # Request user review for pre-classified recruiter emails
                if pre.skip_llm and pre.flagged_for_review:
                    from jobpulse.email_review import request_review
                    review_msg = request_review(
                        msg_id, sender_short, subject, category,
                        pre.confidence, pre.evidence.get("rule_name", "unknown")
                    )
                    send_alert(review_msg)

            # For pre-classified OTHER emails that are flagged, still request review
            if category == OTHER and pre.skip_llm and pre.flagged_for_review:
                sender_short = sender.split("<")[0].strip() if "<" in sender else sender
                from jobpulse.email_review import request_review
                from jobpulse.telegram_bots import send_alert
                review_msg = request_review(
                    msg_id, sender_short, subject, category,
                    pre.confidence, pre.evidence.get("rule_name", "unknown")
                )
                send_alert(review_msg)

    except Exception as e:
        trail.log_step("error", "Fetch error", None, str(e), None,
                       {"error": str(e)}, "error")
        logger.error("Error fetching emails: %s", e)

    # Update last check timestamp
    db.update_last_check_ts(now)

    if not new_recruiter_emails:
        logger.info("No new recruiter emails")

    # Check for pre-classifier graduation
    from jobpulse.email_preclassifier import check_graduation
    graduated = check_graduation()
    trail_suffix = ""
    if graduated:
        state = db.get_preclassifier_state()
        trail_suffix = f" Pre-classifier graduated ({state['total_correct']}/{state['total_audited']} audits correct)."

    trail.finalize(f"Processed {len(messages)} emails. "
                   f"Recruiter: {len(new_recruiter_emails)}. Alerts sent: {len(new_recruiter_emails)}.{trail_suffix}")
    return new_recruiter_emails


def get_yesterday_recruiter_emails() -> list[dict]:
    """Get yesterday's recruiter emails from SQLite for morning digest."""
    from datetime import timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    return db.get_emails_since(yesterday, [SELECTED, INTERVIEW, REJECTED])
