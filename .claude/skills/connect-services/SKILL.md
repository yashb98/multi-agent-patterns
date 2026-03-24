---
name: connect-services
argument-hint: "[service name: gmail, telegram, discord, linkedin, calendar, notion, github, arxiv, websearch]"
description: Step-by-step guide to connect an external service to this project
disable-model-invocation: true
---

Connect the service: $ARGUMENTS

Follow the matching guide below.

## Gmail

1. Go to https://console.cloud.google.com/apis/credentials
2. Create a new project (or select existing)
3. Enable the **Gmail API**: APIs & Services → Library → search "Gmail API" → Enable
4. Create OAuth 2.0 credentials: Credentials → Create → OAuth Client ID → Desktop App
5. Download the `credentials.json` file
6. Run:
```bash
claude mcp add gmail-server -- npx -y @anthropic-ai/gmail-mcp-server /path/to/credentials.json
```
7. Add to `.env`:
```
GMAIL_CREDENTIALS_PATH=/path/to/credentials.json
```
8. First run will open browser for OAuth consent. Approve it.

## Google Calendar

1. Same Google Cloud project as Gmail
2. Enable **Google Calendar API**: APIs & Services → Library → search "Calendar" → Enable
3. Run:
```bash
claude mcp add google-calendar -- npx -y @anthropic-ai/google-calendar-mcp-server /path/to/credentials.json
```
4. Uses same credentials.json as Gmail.

## Telegram

1. Open Telegram, search for **@BotFather**
2. Send `/newbot`, follow prompts, get your bot token
3. Run:
```bash
export TELEGRAM_BOT_TOKEN="your-token-here"
claude mcp add telegram-server -- npx -y @anthropic-ai/telegram-mcp-server
```
4. Add to `.env`:
```
TELEGRAM_BOT_TOKEN=your-token-here
```

## Discord

1. Go to https://discord.com/developers/applications
2. Click **New Application** → name it → create
3. Go to **Bot** tab → click **Reset Token** → copy the token
4. Go to **OAuth2** → URL Generator → select `bot` scope → select permissions (Send Messages, Read Message History, Embed Links)
5. Open the generated URL to invite the bot to your server
6. Run:
```bash
export DISCORD_BOT_TOKEN="your-token-here"
claude mcp add discord-server -- npx -y @anthropic-ai/discord-mcp-server
```
7. Add to `.env`:
```
DISCORD_BOT_TOKEN=your-token-here
```

## Notion

1. Go to https://www.notion.so/my-integrations
2. Click **New Integration** → name it → select workspace → create
3. Copy the **Internal Integration Token**
4. In Notion: open any page/database you want accessible → click `...` → **Connections** → add your integration
5. Run:
```bash
export NOTION_API_KEY="your-integration-token"
claude mcp add notion-server -- npx -y @anthropic-ai/notion-mcp-server
```
6. Add to `.env`:
```
NOTION_API_KEY=your-integration-token
```

## LinkedIn

1. Go to https://www.linkedin.com/developers/apps → Create App
2. Under **Products**, request access to **Share on LinkedIn** and **Sign In with LinkedIn using OpenID Connect**
3. Go to **Auth** tab → note Client ID and Client Secret
4. Complete OAuth 2.0 flow to get an access token:
```bash
# Open this URL in browser (replace CLIENT_ID):
# https://www.linkedin.com/oauth/v2/authorization?response_type=code&client_id=CLIENT_ID&redirect_uri=http://localhost:3000/callback&scope=openid%20profile%20email%20w_member_social
# After approval, exchange the code for a token:
curl -X POST https://www.linkedin.com/oauth/v2/accessToken \
  -d "grant_type=authorization_code&code=AUTH_CODE&redirect_uri=http://localhost:3000/callback&client_id=CLIENT_ID&client_secret=CLIENT_SECRET"
```
5. Add to `.env`:
```
LINKEDIN_ACCESS_TOKEN=your-access-token
```
6. Note: LinkedIn tokens expire in **60 days**. Set a reminder to refresh.

## GitHub

Already connected! `gh` CLI is authenticated as `yashb98`.
To verify: `gh auth status`

## WebSearch

Already available in Claude Code. No setup needed.
Use via `WebFetch` tool or `WebSearch` tool directly.

## arXiv

Use the `/arxiv-top5` skill to fetch top AI papers on demand.
No API key needed — arXiv is open access.
