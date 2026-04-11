---
description: "How to debug the 24/7 JobPulse daemon — logs, restart, common failures."
context: fork
---

# Debug Daemon Skill

## Starting/Stopping
```
python -m jobpulse.runner daemon          # Start single bot
python -m jobpulse.runner multi-bot       # Start all 5 bots
python -m jobpulse.runner stop            # Stop all daemons
```

## Daemon Management (cross-platform)
```
./scripts/install_daemon.sh install    # Auto-detects macOS (launchctl) vs Linux (systemd)
./scripts/install_daemon.sh status     # Check if running
./scripts/install_daemon.sh restart    # Restart
./scripts/install_daemon.sh logs       # Tail logs
./scripts/install_daemon.sh uninstall  # Remove
```

## Log Locations
- Linux: `journalctl --user -u jobpulse -f`
- macOS: `tail -f logs/daemon-stdout.log`
- Agent process trails: http://localhost:8000/processes.html
- Error log: http://localhost:8000/health.html → errors section
- Budget transactions: data/budget.db
- Verified facts cache: data/verified_facts.db

## Common Failures

1. **Bot not responding**: Check if daemon is running (`status` on Telegram). If dead, restart with `multi-bot`.
2. **Duplicate messages**: Two bots handling same message. Check that main bot excludes dedicated bot intents.
3. **429 from arXiv**: HTTPS + User-Agent + 3-attempt retry with 5/10/15s backoff.
4. **Gmail auth expired**: Re-run `python scripts/setup_integrations.py` to refresh OAuth tokens.
5. **Budget double-logging**: Dedup guard should prevent. If not, check if both main + budget bot are polling same chat.

## Health Check
```
python -m jobpulse.runner webhook         # Start API server
# Swagger: http://localhost:8080/docs
# Health: http://localhost:8000/health.html
```
