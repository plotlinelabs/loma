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
- OpenCode as the default agent runtime, with optional Claude Agent SDK fallback
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
- OpenCode API key for the default agent runtime
- Optional Anthropic API key or Claude dashboard accounts if you want Claude Agent SDK fallback

## Fresh EC2 Quickstart

Use this path to test a brand-new self-hosted install on Ubuntu 24.04 LTS or Ubuntu 26.04 LTS. Temporarily allow inbound TCP `22`, `3000`, and `3001` in the instance security group while testing.

1. SSH into the instance:

```bash
ssh -i your-key.pem ubuntu@<ec2-public-ip>
```

2. Install Docker Engine and Docker Compose plugin:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker ubuntu
```

Log out and SSH back in so Docker group permissions apply.

3. Clone Loma:

```bash
git clone https://github.com/plotlinelabs/loma.git
cd loma
```

4. Create the backend environment file:

```bash
cp .env.example .env
nano .env
```

Minimum values for an EC2 smoke test:

```text
PUBLIC_BASE_URL=http://<ec2-public-ip>:3001
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
OPENCODE_API_KEY=opencode-...
AGENT_DEFAULT_MODEL=opencode-go/deepseek-v4-flash
OBSERVABILITY_MONGODB_URI=mongodb+srv://...
OBSERVABILITY_DB_NAME=loma_observability
WEBHOOK_PORT=3000
```

5. Create the dashboard environment file:

```bash
cd dashboard
cp .env.example .env
nano .env
cd ..
```

Minimum values:

```text
AUTH_SECRET=<random-long-secret>
AUTH_GOOGLE_ID=...
AUTH_GOOGLE_SECRET=...
AUTH_URL=http://<ec2-public-ip>:3001
BACKEND_URL=http://loma-backend:3000
NEXT_PUBLIC_API_URL=http://<ec2-public-ip>:3000
```

6. Configure Google OAuth with this redirect URI:

```text
http://<ec2-public-ip>:3001/api/auth/callback/google
```

7. Configure Slack:

- Create a Slack app at <https://api.slack.com/apps>.
- Enable Socket Mode.
- Create an app-level token with `connections:write` and set it as `SLACK_APP_TOKEN`.
- Add the bot scopes listed in the Slack setup section below.
- Subscribe to `app_mention` and `message.im` bot events.
- Install the app and set the bot token as `SLACK_BOT_TOKEN`.

8. Start Loma:

```bash
docker compose up --build
```

9. Smoke test:

- Open `http://<ec2-public-ip>:3001`.
- Log in with Google; the first user should become `admin`.
- Send a message in dashboard chat.
- Mention the Slack bot in a channel where it has been invited.
- DM the Slack bot.
- Confirm conversation history appears in the dashboard.

Useful debugging commands:

```bash
docker compose ps
docker compose logs -f loma-backend
docker compose logs -f loma-dashboard
```

If OpenCode fails, check backend logs for missing `OPENCODE_API_KEY`, `opencode binary not found`, or `OpenCode server did not become ready`. The backend Docker image installs the `opencode` CLI automatically, so most first-run issues are missing credentials or blocked network access.

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

## OpenCode setup

Loma uses OpenCode by default. The backend Docker image installs the `opencode` CLI with OpenCode's official install script. Local non-Docker installs can use the same script or `npm install -g opencode-ai`.

Create an OpenCode API key, set `OPENCODE_API_KEY`, and keep the default model unless you want to override it:

```text
OPENCODE_API_KEY=opencode-...
AGENT_DEFAULT_MODEL=opencode-go/deepseek-v4-flash
```

OpenCode starts an app-managed local server on `127.0.0.1:4097` by default. Override with `OPENCODE_HOST`, `OPENCODE_PORT`, or `OPENCODE_SERVER_URL` if you manage OpenCode separately.

To use Claude Agent SDK instead, set `AGENT_DEFAULT_MODEL=anthropic/<model>` and configure `ANTHROPIC_API_KEY` or dashboard Claude accounts.

## Required backend environment

See `.env.example` for a complete starter. Minimum useful setup:

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
OPENCODE_API_KEY=opencode-...
AGENT_DEFAULT_MODEL=opencode-go/deepseek-v4-flash
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
