"""Multi-bot Telegram listener — polls all configured bots in parallel threads.

Each bot handles its own subset of intents:
  Main Bot:     Everything (fallback)
  Budget Bot:   Budget commands only
  Research Bot: Papers/arXiv commands only
  Alert Bot:    No polling (send-only)
"""

import time
import threading
from datetime import datetime
from shared.logging_config import get_logger
from jobpulse.config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DATA_DIR, LOGS_DIR,
    TELEGRAM_BUDGET_BOT_TOKEN, TELEGRAM_RESEARCH_BOT_TOKEN,
    TELEGRAM_JOBS_BOT_TOKEN, JOBPULSE_SWARM,
)
from jobpulse.telegram_bots import _get_updates, send_for_intent, BUDGET_INTENTS, RESEARCH_INTENTS, JOBS_INTENTS
from jobpulse.command_router import classify, Intent
from jobpulse.healthcheck import write_heartbeat

logger = get_logger(__name__)

USE_SWARM = JOBPULSE_SWARM
if USE_SWARM:
    from jobpulse.swarm_dispatcher import dispatch
else:
    from jobpulse.dispatcher import dispatch


def _log(msg: str):
    logger.info(msg)
    log_file = LOGS_DIR / "telegram-listener.log"
    with open(log_file, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] {msg}\n")


def _poll_bot(bot_name: str, token: str, allowed_intents: set = None,
              send_fn=None):
    """Poll a single bot in a loop. Handles only allowed intents."""
    if not token:
        logger.info("Skipping %s bot (no token)", bot_name)
        return

    last_id_file = DATA_DIR / f"telegram_{bot_name}_last_update_id.txt"

    def get_last_id() -> int:
        try:
            return int(last_id_file.read_text().strip())
        except (FileNotFoundError, ValueError):
            return 0

    def save_last_id(uid: int):
        last_id_file.write_text(str(uid))

    _log(f"{bot_name} bot listener started")
    last_id = get_last_id()
    consecutive_errors = 0

    while True:
        try:
            updates = _get_updates(token, offset=last_id + 1, long_poll=True)
            write_heartbeat()

            if not updates:
                consecutive_errors = 0
                continue

            # Save checkpoint IMMEDIATELY — prevents re-processing on crash/restart
            max_id = max(u.get("update_id", 0) for u in updates)
            if max_id > last_id:
                last_id = max_id
                save_last_id(max_id)

            for update in updates:
                uid = update.get("update_id", 0)

                msg = update.get("message", {})
                from_id = str(msg.get("from", {}).get("id", ""))
                text = msg.get("text", "").strip()

                if from_id != TELEGRAM_CHAT_ID:
                    continue

                # Handle voice messages (voice msgs have no text field)
                voice = msg.get("voice") or msg.get("audio")
                if voice and not text:
                    from jobpulse.voice_handler import transcribe_voice
                    text = transcribe_voice(voice["file_id"], bot_token=token)
                    if text:
                        _log(f"[{bot_name}] Voice: \"{text[:80]}\"")
                        send_fn(f"\U0001f3a4 Heard: \"{text}\"")
                    else:
                        send_fn("\U0001f3a4 Couldn't understand the voice message.")
                        continue

                if not text:
                    continue

                # Handle /start, /help, and /stop per bot
                text_lower = text.lower().strip()
                if text_lower in ("/start", "/help", "help", "help."):
                    from jobpulse.telegram_bots import get_help_for_bot
                    send_fn(get_help_for_bot(bot_name))
                    _log(f"[{bot_name}] Sent help")
                    continue

                # Check approval flow
                from jobpulse.approval import process_reply as check_approval
                approval_response = check_approval(text)
                if approval_response:
                    send_fn(approval_response)
                    _log(f"[{bot_name}] Approval: {approval_response[:80]}")
                    continue

                _log(f"[{bot_name}] Got: \"{text[:80]}\"")

                # Classify
                cmd = classify(text)
                _log(f"[{bot_name}] Intent: {cmd.intent.value}")

                # "stop" works from any bot — undo last action
                if allowed_intents and cmd.intent.value not in allowed_intents and cmd.intent.value != "stop":
                    # Wrong bot — tell user where to go
                    from jobpulse.telegram_bots import send_main
                    send_fn(f"This command goes to the main bot. Forwarding...")
                    # Forward to main bot dispatcher and send reply via main
                    reply = dispatch(cmd)
                    send_main(reply)
                    _log(f"[{bot_name}] Forwarded to main: {cmd.intent.value}")
                    continue

                # Send processing indicator with time estimate
                INTENT_ESTIMATES = {
                    "arxiv": "Fetching & ranking 200 papers... ~60s",
                    "weekly_report": "Building period report... ~10s",
                    "briefing": "Collecting from all agents... ~15s",
                    "gmail": "Scanning inbox + classifying... ~10s",
                    "calendar": "Fetching events... ~3s",
                    "github": "Fetching commits... ~5s",
                    "trending": "Fetching trending repos... ~3s",
                    "show_tasks": "Loading tasks from Notion... ~3s",
                    "create_tasks": "Creating tasks in Notion... ~5s",
                    "complete_task": "Marking done in Notion... ~3s",
                    "remove_task": "Removing from Notion... ~3s",
                    "weekly_plan": "Loading past week's tasks... ~5s",
                    "log_spend": "Classifying + logging... ~5s",
                    "log_income": "Logging income... ~3s",
                    "log_savings": "Logging savings... ~3s",
                    "log_hours": "Calculating pay + syncing... ~5s",
                    "show_budget": "Loading budget summary... ~3s",
                    "set_budget": "Updating budget... ~3s",
                    "undo_budget": "Removing transaction... ~5s",
                    "undo_hours": "Removing hours entry... ~5s",
                    "show_hours": "Loading timesheet... ~3s",
                    "recurring_budget": "Processing recurring rule... ~3s",
                    "show_jobs": "Loading pending jobs... ~3s",
                    "approve_jobs": "Submitting applications... ~30s",
                    "reject_job": "Skipping job... ~2s",
                    "job_stats": "Calculating stats... ~3s",
                    "search_config": "Updating search config... ~2s",
                    "pause_jobs": "Pausing autopilot... ~1s",
                    "resume_jobs": "Resuming autopilot... ~1s",
                    "job_detail": "Loading job details... ~3s",
                    "scan_jobs": "Scanning for new jobs... ~60s",
                    "export": "Backing up all data... ~10s",
                    "conversation": "Thinking... ~3s",
                    "remote_shell": "Running command... ~5s",
                    "git_ops": "Running git... ~3s",
                }
                estimate = INTENT_ESTIMATES.get(cmd.intent.value)
                if estimate:
                    send_fn(f"⏳ {estimate}")
                # Extra check for blog command
                if cmd.intent.value == "arxiv" and "blog" in cmd.raw.lower():
                    send_fn("⏳ Generating 2000-word blog post... ~90s\n(5 agents: reader → writer → fact checker → diagrams → editor)")

                # Show typing indicator before LLM/agent call
                from jobpulse.telegram_bots import send_chat_action_for_token
                send_chat_action_for_token(token)

                # Dispatch
                reply = dispatch(cmd)
                send_fn(reply)
                _log(f"[{bot_name}] Replied: {reply[:80]}...")

            consecutive_errors = 0
            write_heartbeat()

        except KeyboardInterrupt:
            _log(f"{bot_name} bot stopped")
            break
        except Exception as e:
            consecutive_errors += 1
            _log(f"[{bot_name}] Error ({consecutive_errors}): {e}")
            if consecutive_errors > 5:
                time.sleep(min(60, consecutive_errors * 5))
            else:
                time.sleep(2)


def start_all_bots():
    """Start all configured bot listeners in parallel threads."""
    from jobpulse.telegram_bots import send_main, send_budget, send_research, send_jobs

    # Check OAuth health on startup
    try:
        from jobpulse.oauth_monitor import run_health_check
        run_health_check()
    except Exception as e:
        logger.warning("OAuth health check on startup failed: %s", e)

    threads = []

    # Main bot — handles everything EXCEPT intents claimed by dedicated bots
    # This prevents double-processing (e.g., budget message handled by both Main + Budget bot)
    main_excluded_intents = set()
    if TELEGRAM_BUDGET_BOT_TOKEN:
        main_excluded_intents.update(BUDGET_INTENTS)
    if TELEGRAM_RESEARCH_BOT_TOKEN:
        main_excluded_intents.update(RESEARCH_INTENTS)
    if TELEGRAM_JOBS_BOT_TOKEN:
        main_excluded_intents.update(JOBS_INTENTS)

    main_allowed = None  # None = all intents
    if main_excluded_intents:
        # Build set of all intents minus the excluded ones
        all_intents = {i.value for i in Intent}
        main_allowed = all_intents - main_excluded_intents

    if TELEGRAM_BOT_TOKEN:
        t = threading.Thread(
            target=_poll_bot,
            args=("main", TELEGRAM_BOT_TOKEN, main_allowed, send_main),
            name="main-bot", daemon=True,
        )
        threads.append(("Main", t))

    # Budget bot — budget intents only
    if TELEGRAM_BUDGET_BOT_TOKEN:
        t = threading.Thread(
            target=_poll_bot,
            args=("budget", TELEGRAM_BUDGET_BOT_TOKEN, BUDGET_INTENTS, send_budget),
            name="budget-bot", daemon=True,
        )
        threads.append(("Budget", t))

    # Research bot — arxiv/papers intents only
    if TELEGRAM_RESEARCH_BOT_TOKEN:
        t = threading.Thread(
            target=_poll_bot,
            args=("research", TELEGRAM_RESEARCH_BOT_TOKEN, RESEARCH_INTENTS, send_research),
            name="research-bot", daemon=True,
        )
        threads.append(("Research", t))

    # Jobs bot — job autopilot intents only
    if TELEGRAM_JOBS_BOT_TOKEN:
        t = threading.Thread(
            target=_poll_bot,
            args=("jobs", TELEGRAM_JOBS_BOT_TOKEN, JOBS_INTENTS, send_jobs),
            name="jobs-bot", daemon=True,
        )
        threads.append(("Jobs", t))

    # Alert bot is send-only — no polling needed

    if not threads:
        logger.error("No Telegram bots configured.")
        return

    logger.info("Starting %d Telegram bot(s): %s", len(threads), ", ".join(n for n, _ in threads))

    for name, t in threads:
        t.start()
        logger.info("%s bot started", name)

    # Block on first thread
    try:
        threads[0][1].join()
    except KeyboardInterrupt:
        logger.info("Multi-bot listener stopped")
