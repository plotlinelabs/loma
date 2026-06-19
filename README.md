# Loma

**Loma is an AI agent factory for companies.** It gives a team a self-hosted Slack agent, a dashboard for chat and observability, Google OAuth user management, and an integration framework for connecting company tools.

This repository is the clean OSS codebase. Company-specific knowledge, prompts, playbooks, credentials, and deployment details should live in your database, environment variables, or private configuration exports - not in source code.

## What ships in v0.1

- Slack Socket Mode bot for app mentions and DMs
- Thread context and Slack file download support
- Dashboard chat and conversation history
- Google OAuth login through NextAuth
- First-user-becomes-admin provisioning
- User, team, and role management
- MongoDB-backed conversations, prompt settings, flows, users, and integrations
- Optional integration registry for tools such as GitHub, Linear, HubSpot, Google, Slack, Sentry, and databases
- Feature flags so optional providers do not block startup

## Architecture

```text
Slack workspace ── Socket Mode ── Python backend (:3000) ── MongoDB
                                      │
                                      ├── Agent runtime + optional MCP tools
                                      │
Dashboard (:3001) ── Google OAuth ────┘
```

## Prerequisites

- Python 3.10+
- Node.js 20+
- MongoDB database
- Slack workspace where you can create an app
- Google OAuth client for dashboard login
- Anthropic API key or another model provider supported by your runtime configuration

## Local backend setup

```bash
git clone https://github.com/plotlinelabs/loma.git
cd loma
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env
python app.py
```

The backend listens on `WEBHOOK_PORT`, default `3000`.

## Local dashboard setup

```bash
cd dashboard
npm install
cp .env.example .env
# edit .env
npm run dev
```

Open `http://localhost:3001`. The first Google-authenticated user is automatically provisioned as `admin`.

## Slack app setup

1. Create a Slack app at <https://api.slack.com/apps>.
2. Enable Socket Mode and create an app-level token with `connections:write`; set it as `SLACK_APP_TOKEN`.
3. Add bot token scopes:
   - `app_mentions:read`
   - `chat:write`
   - `channels:history`
   - `channels:read`
   - `groups:history`
   - `im:history`
   - `im:read`
   - `im:write`
   - `files:read`
   - `reactions:read`
   - `reactions:write`
   - `users:read`
   - `users:read.email`
4. Subscribe to bot events:
   - `app_mention`
   - `message.im`
5. Install the app to your workspace and set the bot token as `SLACK_BOT_TOKEN`.
6. Invite Loma to any channels where it should respond to mentions.

Channel-wide automations are disabled in this first OSS release. They will become dashboard-managed company workflows in a later release.

## Google OAuth setup

Create an OAuth client in Google Cloud Console. For local development, add:

```text
http://localhost:3001/api/auth/callback/google
```

Set these in `dashboard/.env`:

```text
AUTH_SECRET=...
AUTH_GOOGLE_ID=...
AUTH_GOOGLE_SECRET=...
AUTH_URL=http://localhost:3001
```

In production, set `AUTH_URL` to your dashboard URL and add the matching callback URL in Google Cloud Console.

## MongoDB setup

Create a MongoDB database and set:

```text
OBSERVABILITY_MONGODB_URI=mongodb+srv://user:pass@cluster.example.com/loma
OBSERVABILITY_DB_NAME=loma_observability
```

Loma creates indexes on startup.

## Required backend environment

See `.env.example` for a complete starter. Minimum useful setup:

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
ANTHROPIC_API_KEY=sk-ant-...
OBSERVABILITY_MONGODB_URI=mongodb+srv://...
WEBHOOK_PORT=3000
APP_NAME=Loma
PUBLIC_BASE_URL=http://localhost:3001
```

## Feature flags

Optional subsystems are off unless configured:

```text
LOMA_ENABLE_SCHEDULER=false
LOMA_ENABLE_WEBHOOKS=true
LOMA_ENABLE_METRICS=false
```

Missing optional provider credentials should not prevent the backend from booting.

## Production starter

A minimal Docker Compose setup is included:

```bash
docker compose up --build
```

For EC2/GCP, run the backend behind a process manager and the dashboard behind your reverse proxy. Use HTTPS for the dashboard URL configured in Google OAuth.

## Team onboarding

- First login becomes `admin`.
- Admins can manage users, teams, and roles in the dashboard.
- Roles are: `admin`, `maintainer`, `operator`, `analyst`, and `chatter`.

## Optional integrations

The integration registry is included, but each provider must be configured before use. Providers should fail with clear setup errors instead of blocking startup.

## Roadmap

Next major workstream: company knowledge, playbooks, and Slack workflows become configurable from the Loma dashboard and stored in MongoDB.

## Security

Never commit `.env`, credentials, private company prompts, customer data, or internal playbooks. See `SECURITY.md`.
