<div align="center">

# Loma

**Self-hosted AI agents for your whole team.**

One agent — in Slack and a dashboard — that knows your tools, runs on open models *or* your pooled Claude subscriptions, automates work on schedules and webhooks, and shares a team-wide skill library that gets better over time.

[Website](https://www.lomahq.com) · [Docs](https://www.lomahq.com/docs) · [Quickstart](#quickstart) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![CI](https://github.com/plotlinelabs/loma/actions/workflows/ci.yml/badge.svg)](https://github.com/plotlinelabs/loma/actions/workflows/ci.yml)

</div>

---

## Why Loma

Most teams pay for AI per seat and per token, wire up the same context by hand in every tool, and lose every useful prompt the moment the chat ends. Loma is the opposite: one agent your whole company shares, on infrastructure you own.

- 🪙 **Pool your team's Claude Code subscriptions.** Connect existing Claude accounts into a round-robin pool so everyone's agent usage draws from subscriptions you already pay for — subsidizing token cost instead of buying per-seat API access.
- 🧠 **Run on open models via OpenCode.** The default runtime is [OpenCode](https://opencode.ai), so you get open-source models like DeepSeek V4 and GLM with no per-token bill — and you can switch models per conversation.
- ⚡ **Automate with webhook & scheduled flows.** Turn any agent task into a routine: run it on a cron schedule, or fire it from a webhook out of your CI/CD, support tool, or any system in your org.
- 📚 **Set up skills once, the whole team uses them.** Write a playbook once, store it in the database, and every teammate *and* every automation uses it instantly — versioned, and able to improve itself from feedback.

Plus: reachable from **Slack and a web dashboard**, connects to **your existing stack** (databases, issue trackers, CRM, docs, observability), logs **every run, token, and cost**, and is fully **self-hostable** with env-driven config you can edit from the dashboard.

## What you can do

Loma runs the same agent across ad-hoc questions and standing automations. A few patterns teams use it for:

| Trigger | What Loma does |
| --- | --- |
| **Ask, anytime** (dashboard or Slack) | Answers questions live against your systems — "what's the ID of X?", "who requested this?", validate a config, debug an issue — without you opening five tabs. |
| **A support ticket arrives** (webhook) | Investigates the ticket against your data and docs, then drafts a customer reply or posts an internal note. |
| **A bug or feature request lands in Slack** | Triages it, files or sizes a ticket, and links the relevant context. |
| **Every morning** (schedule) | Posts the reports your team reads before standup — on-call & bug summaries, support digests, experiment/A-B results, adoption dashboards — pulled from across your tools. |
| **A PR merges or a deploy ships** (webhook) | Posts changelogs and release notes, sends deploy notifications, opens docs-update PRs. |
| **On a recurring sweep** (schedule) | Sizes open tickets, runs token/health checks, drives crash-fix or cleanup passes. |
| **Continuously** (schedule) | Mines PR feedback, support tickets, and call transcripts to update skills — so the agent keeps getting better. |

## How it works

```text
   Slack  ─────────────┐                        ┌── Agent runtime ── OpenCode (open models)
   (DM / mention)      │                        │                 └─ Claude account pool
                       ▼                        │
                Backend (:3000) ────────────────┼── Skills (DB) + your connected tools (MCP)
                       ▲          MongoDB        │
   Dashboard (:3001) ──┘   (conversations,       └── Flows: schedules + webhooks
   (chat, flows,           skills, flows,
    skills, config)        users, usage)
```

- **Backend** — Python (aiohttp + Slack Bolt). Runs the agent, serves the API, receives webhooks.
- **Dashboard** — Next.js. Chat, conversation history, skills, flows, integrations, config, users/roles, usage.
- **Storage** — MongoDB for everything stateful; skill assets on local disk (`LOMA_SKILL_ASSET_DIR`).
- **Runtime** — OpenCode by default; optionally pool Claude Code accounts. Switchable per conversation.

## Quickstart

The fastest way to try Loma is Docker Compose. You need a MongoDB connection string, a Slack app (Socket Mode), and an OpenCode API key.

```bash
git clone https://github.com/plotlinelabs/loma.git
cd loma

cp .env.example .env                 # backend config
cp dashboard/.env.example dashboard/.env   # dashboard config
# edit both — see the env keys in the docs

docker compose up --build
```

Then open `http://localhost:3001`, create the first admin with your `LOMA_SETUP_TOKEN`, and send a message in chat.

**Full setup** — fresh EC2/GCP install, Slack app scopes, auth, MongoDB, OpenCode, and the complete environment reference — is in the **[documentation](https://www.lomahq.com/docs)**.

## Configuration & integrations

- **Config** is env-driven and editable from the dashboard's Environment page — no rebuilds to change keys.
- **Optional by default:** missing provider credentials never block startup, and feature flags (`LOMA_ENABLE_SCHEDULER`, `LOMA_ENABLE_WEBHOOKS`, `LOMA_ENABLE_METRICS`) gate optional subsystems.
- **Integrations** connect from the dashboard: databases (MongoDB, ClickHouse, BigQuery, Athena), issue trackers (Linear, GitHub), support (Pylon), CRM (HubSpot, Apollo), knowledge (Notion, GitBook, Google Workspace), and observability (Sentry, PostHog), among others.

See the [integrations guide](https://www.lomahq.com/docs) for per-provider setup.

## Documentation

Full guides live at **[lomahq.com/docs](https://www.lomahq.com/docs)**:

- Getting started — Quickstart, fresh EC2/GCP install, local development
- Configuration — environment reference, feature flags, in-dashboard config
- Agent runtime — OpenCode, Claude account pooling, model selection
- Skills — authoring, importing, assets, versioning
- Flows & automations — scheduled routines and webhook triggers
- Integrations — connecting your tools
- Slack app, authentication, deployment & networking, security

## Project layout

```text
app.py            Backend entrypoint (aiohttp + Slack Bolt)
agent/            Agent runtime: OpenCode + Claude account pool
api/              HTTP API (chat, skills, flows, env, governance, ...)
scheduler/        Scheduled & webhook flow execution
webhooks/         Inbound webhook handlers
integrations/     Connectable-tool registry
tools/            CLI tools the agent can call (skills + integrations)
observability/    Conversation/usage logging to MongoDB
slack_app/        Slack event handling
dashboard/        Next.js dashboard
```

## Contributing

Issues and PRs are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). This repository is the clean OSS codebase — company-specific knowledge, prompts, playbooks, and credentials belong in your database and environment, never in source.

## Security

Never commit `.env`, credentials, private prompts, or customer data. To report a vulnerability, see [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE).
