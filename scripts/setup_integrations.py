#!/usr/bin/env python3
"""
Setup Integrations — One-time setup for all JobPulse API connections.

Usage: python scripts/setup_integrations.py

Walks through:
1. Google OAuth2 (Gmail + Calendar) — opens browser for consent
2. Notion API — tests connection
3. GitHub — tests gh CLI auth
4. Telegram — sends test message
"""

import os
import sys
import json
import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv
load_dotenv(PROJECT_DIR / ".env")


def test_telegram():
    """Test Telegram bot connection."""
    print("\n── Telegram ──")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("❌ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in .env")
        return False

    payload = json.dumps({"chat_id": chat_id, "text": "🧪 JobPulse setup test — Telegram is working!"})
    result = subprocess.run(
        ["curl", "-s", "-X", "POST",
         f"https://api.telegram.org/bot{token}/sendMessage",
         "-H", "Content-Type: application/json", "-d", payload],
        capture_output=True, text=True, timeout=15
    )
    resp = json.loads(result.stdout)
    if resp.get("ok"):
        print("✅ Telegram connected — test message sent!")
        return True
    print(f"❌ Telegram error: {resp}")
    return False


def test_notion():
    """Test Notion API connection."""
    print("\n── Notion ──")
    api_key = os.getenv("NOTION_API_KEY")
    if not api_key:
        print("❌ NOTION_API_KEY not set in .env")
        return False

    result = subprocess.run(
        ["curl", "-s", "-X", "POST", "https://api.notion.com/v1/search",
         "-H", f"Authorization: Bearer {api_key}",
         "-H", "Content-Type: application/json",
         "-H", "Notion-Version: 2022-06-28",
         "-d", '{"query":"","page_size":5}'],
        capture_output=True, text=True, timeout=15
    )
    resp = json.loads(result.stdout)
    results = resp.get("results", [])
    if "status" in resp and resp["status"] >= 400:
        print(f"❌ Notion error: {resp.get('message', resp)}")
        return False

    print(f"✅ Notion connected — {len(results)} pages/databases accessible")

    tasks_db = os.getenv("NOTION_TASKS_DB_ID")
    research_db = os.getenv("NOTION_RESEARCH_DB_ID")
    if tasks_db:
        print(f"   Daily Tasks DB: {tasks_db}")
    else:
        print("   ⚠️  NOTION_TASKS_DB_ID not set — create via Notion API or UI")
    if research_db:
        print(f"   Research DB: {research_db}")
    else:
        print("   ⚠️  NOTION_RESEARCH_DB_ID not set")
    return True


def test_github():
    """Test GitHub CLI authentication."""
    print("\n── GitHub ──")
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=10
        )
        if "Logged in" in result.stdout or "Logged in" in result.stderr:
            username = os.getenv("GITHUB_USERNAME", "yashb98")
            print(f"✅ GitHub connected as {username}")

            # Quick test — fetch recent events
            events = subprocess.run(
                ["gh", "api", f"/users/{username}/events?per_page=1"],
                capture_output=True, text=True, timeout=10
            )
            if events.returncode == 0:
                print(f"   API access confirmed")
            return True
        print(f"❌ GitHub not authenticated: {result.stderr}")
        return False
    except FileNotFoundError:
        print("❌ gh CLI not installed. Install: brew install gh")
        return False


def setup_google_oauth():
    """Walk through Google OAuth2 setup for Gmail + Calendar."""
    print("\n── Google OAuth2 (Gmail + Calendar) ──")

    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET")
    token_path = os.getenv("GOOGLE_TOKEN_PATH", str(PROJECT_DIR / "data" / "google_token.json"))

    if os.path.exists(token_path):
        print(f"✅ Token file exists: {token_path}")
        # Test if it works
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request

            creds = Credentials.from_authorized_user_file(token_path,
                ["https://www.googleapis.com/auth/gmail.readonly",
                 "https://www.googleapis.com/auth/calendar.readonly"])

            if creds.valid:
                print("   Token is valid — Gmail + Calendar ready")
                return True
            elif creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_path, "w") as f:
                    f.write(creds.to_json())
                print("   Token refreshed successfully")
                return True
        except Exception as e:
            print(f"   Token invalid: {e}")

    if not client_id or not client_secret:
        print("""
To set up Google OAuth2:

1. Go to https://console.cloud.google.com/apis/credentials
2. Create project (or select existing)
3. Enable Gmail API and Google Calendar API:
   - APIs & Services → Library → search "Gmail API" → Enable
   - APIs & Services → Library → search "Calendar API" → Enable
4. Create OAuth 2.0 credentials:
   - Credentials → Create → OAuth Client ID → Desktop App
5. Add these to your .env:
   GOOGLE_CLIENT_ID=your-client-id
   GOOGLE_CLIENT_SECRET=your-client-secret
6. Re-run this script
""")
        return False

    # Run OAuth2 flow
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow

        client_config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }

        flow = InstalledAppFlow.from_client_config(
            client_config,
            scopes=[
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/calendar.readonly",
            ]
        )

        print("Opening browser for Google OAuth2 consent...")
        creds = flow.run_local_server(port=0)

        os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

        print(f"✅ Google OAuth2 complete — token saved to {token_path}")
        return True

    except ImportError:
        print("❌ Install: pip install google-auth-oauthlib google-api-python-client")
        return False
    except Exception as e:
        print(f"❌ OAuth2 error: {e}")
        return False


def main():
    print("=" * 50)
    print("  JobPulse — Integration Setup")
    print("=" * 50)

    results = {}
    results["telegram"] = test_telegram()
    results["notion"] = test_notion()
    results["github"] = test_github()
    results["google"] = setup_google_oauth()

    print("\n" + "=" * 50)
    print("  Summary")
    print("=" * 50)
    for name, ok in results.items():
        status = "✅" if ok else "❌"
        print(f"  {status} {name.title()}")

    all_ok = all(results.values())
    if all_ok:
        print("\n🎉 All integrations connected! JobPulse is ready.")
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"\n⚠️  {len(failed)} integration(s) need attention: {', '.join(failed)}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
