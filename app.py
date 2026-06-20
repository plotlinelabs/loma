import asyncio
import logging
import os
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

# Load .env (bind-mounted at /app/.env in Docker) BEFORE importing modules that
# read env at import time (e.g. config.app_config), and override the container's
# env_file values. This makes dashboard edits to .env take effect when "Restart
# Service" re-execs this process: os.execv carries the stale environment forward,
# so we must re-read the file and override to pick up changes.
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from slack_app.handlers import register_handlers
from slack_app.ingestion import register_ingestion_middleware
from webhooks.linear import setup_linear_webhook_routes
from webhooks.grain import setup_grain_webhook_routes
from webhooks.pylon import setup_pylon_webhook_routes
from webhooks.hubspot import setup_hubspot_webhook_routes
from webhooks.github import setup_github_webhook_routes
from webhooks.incoming import setup_incoming_webhook_routes
from observability.db import init_observability
from api.routes import setup_api_routes
from api.auth_middleware import auth_middleware
from api.governance_routes import setup_governance_routes
from api.oauth_routes import setup_oauth_routes
from api.webhook_log_routes import setup_webhook_log_routes
from api.env_routes import setup_env_routes
from api.usage_routes import setup_usage_routes
from api.terminal_routes import setup_terminal_routes
from api.claude_auth_routes import setup_claude_auth_routes
from api.file_routes import setup_file_routes
from api.integration_routes import setup_integration_routes
from api.prompt_settings_routes import setup_prompt_settings_routes
from recovery import start_recovery_loop
from scheduler.engine import init_scheduler
from agent.client import load_config, merge_db_integrations
from agent.pool import init_pool
from agent.prompt import refresh_loma_skill_index_from_db, refresh_prompt_settings_from_db
from config.app_config import APP_NAME, LOMA_ENABLE_SCHEDULER, LOMA_ENABLE_WEBHOOKS, LOMA_ENABLE_SLACK

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = AsyncApp(token=os.environ["SLACK_BOT_TOKEN"])
register_handlers(app)
register_ingestion_middleware(app)


@web.middleware
async def log_404_middleware(request, handler):
    try:
        return await handler(request)
    except web.HTTPNotFound:
        logger.error("[WEBHOOK] 404 Not Found: %s %s", request.method, request.path)
        return web.json_response({"error": "Not Found", "path": request.path}, status=404)


async def main():
    # Initialize observability MongoDB
    await init_observability()
    await refresh_prompt_settings_from_db()
    await refresh_loma_skill_index_from_db()

    # Pre-warm Claude SDK client pool (background \u2014 doesn't block startup)
    agent_config = load_config()
    agent_config = await merge_db_integrations(agent_config)
    await init_pool(config=agent_config)

    # Start periodic recovery loop for interrupted conversations
    start_recovery_loop()

    # In dev mode, skip Slack and scheduled tasks
    is_dev = os.environ.get("ENV", "").upper() == "DEV"

    # Initialize task scheduler (loads active tasks from MongoDB)
    if not is_dev and LOMA_ENABLE_SCHEDULER:
        await init_scheduler()
    elif not is_dev:
        logger.info("Scheduler disabled (set LOMA_ENABLE_SCHEDULER=true to enable)")

    # Slack Socket Mode. Disabled in dev, when LOMA_ENABLE_SLACK is off, or when
    # no app token is set — the latter two let preview/ephemeral stacks run without
    # double-consuming the production Slack app's events.
    handler = None
    if not is_dev and LOMA_ENABLE_SLACK and os.environ.get("SLACK_APP_TOKEN"):
        handler = AsyncSocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    elif not is_dev:
        logger.info(
            "Slack Socket Mode disabled (LOMA_ENABLE_SLACK=false or SLACK_APP_TOKEN unset)"
        )

    # Webhook HTTP server
    webhook_app = web.Application(
        middlewares=[log_404_middleware, auth_middleware],
        client_max_size=110 * 1024 * 1024,  # Supports skill assets up to 100 MB.
    )
    if LOMA_ENABLE_WEBHOOKS:
        setup_linear_webhook_routes(webhook_app)
        setup_grain_webhook_routes(webhook_app)
        setup_pylon_webhook_routes(webhook_app)
        setup_hubspot_webhook_routes(webhook_app)
        setup_github_webhook_routes(webhook_app)
        setup_incoming_webhook_routes(webhook_app)
    setup_api_routes(webhook_app)
    setup_governance_routes(webhook_app)
    setup_oauth_routes(webhook_app)
    setup_webhook_log_routes(webhook_app)
    setup_env_routes(webhook_app)
    setup_usage_routes(webhook_app)
    setup_terminal_routes(webhook_app)
    setup_claude_auth_routes(webhook_app)
    setup_file_routes(webhook_app)
    setup_integration_routes(webhook_app)
    setup_prompt_settings_routes(webhook_app)
    runner = web.AppRunner(webhook_app)
    await runner.setup()
    port = int(os.environ.get("WEBHOOK_PORT", "3000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Webhook server running on port %d", port)

    logger.info("%s is running!", APP_NAME)
    if handler:
        await handler.start_async()
    else:
        logger.info("Slack Socket Mode not started; keeping HTTP server alive")
        # Keep the process alive for the HTTP server
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
