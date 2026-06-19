"""Hourly usage check — alerts when Claude MAX subscription usage exceeds threshold."""

import json
import logging
import os
from datetime import datetime, timezone, timedelta

import aiohttp
from slack_sdk.web.async_client import AsyncWebClient

from observability.db import get_db

logger = logging.getLogger(__name__)

CREDENTIALS_PATH = os.path.expanduser("~/.claude/.credentials.json")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

# Alert when remaining usage drops below this percentage
ALERT_THRESHOLD = 0.75  # i.e., 75% used = 25% remaining
# Don't re-alert for the same bucket within this window
COOLDOWN_HOURS = 2

BUCKETS = [
    ("5h", "Session (5h)"),
    ("7d", "Weekly (All Models)"),
    ("7d_sonnet", "Weekly (Sonnet)"),
]


def _read_oauth_token() -> str | None:
    try:
        with open(CREDENTIALS_PATH) as f:
            creds = json.load(f)
        return creds.get("claudeAiOauth", {}).get("accessToken")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


async def _fetch_rate_limits() -> dict:
    """Make a minimal API call to get rate limit headers."""
    token = _read_oauth_token()
    if token is None:
        return {}

    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": token,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                result = {}
                prefix = "anthropic-ratelimit-unified-"
                for key, value in resp.headers.items():
                    if key.lower().startswith(prefix):
                        result[key.lower()[len(prefix):]] = value
                return result
    except Exception:
        logger.exception("[USAGE_CHECK] Failed to fetch rate limits")
        return {}


def _format_reset_time(unix_ts: int) -> str:
    """Format a reset timestamp as a human-readable relative time."""
    now = datetime.now(timezone.utc).timestamp()
    diff = unix_ts - now
    if diff <= 0:
        return "now"
    hours = int(diff // 3600)
    minutes = int((diff % 3600) // 60)
    if hours > 24:
        days = hours // 24
        return f"{days}d {hours % 24}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


async def run_usage_check():
    """Check usage and alert if any bucket exceeds the threshold."""
    db = get_db()
    headers = await _fetch_rate_limits()
    if not headers:
        logger.warning("[USAGE_CHECK] No rate limit data available")
        return

    now = datetime.now(timezone.utc)
    alerts_to_send = []

    for prefix, label in BUCKETS:
        utilization = float(headers.get(f"{prefix}-utilization", 0))
        status = headers.get(f"{prefix}-status", "allowed")
        reset = int(headers.get(f"{prefix}-reset", 0))

        if utilization < ALERT_THRESHOLD and status == "allowed":
            continue

        # Check cooldown
        if db is not None:
            last_alert = await db.usage_alerts.find_one(
                {"bucket": prefix},
                sort=[("alerted_at", -1)],
            )
            if last_alert is not None:
                cooldown_end = last_alert["alerted_at"] + timedelta(hours=COOLDOWN_HOURS)
                # Skip if within cooldown AND same reset window
                if now < cooldown_end and last_alert.get("reset_at") == reset:
                    continue

        remaining_pct = round((1 - utilization) * 100)
        used_pct = round(utilization * 100)
        reset_str = _format_reset_time(reset) if reset else "unknown"

        alerts_to_send.append({
            "bucket": prefix,
            "label": label,
            "utilization": utilization,
            "remaining_pct": remaining_pct,
            "used_pct": used_pct,
            "reset": reset,
            "reset_str": reset_str,
            "status": status,
        })

    if not alerts_to_send:
        logger.info("[USAGE_CHECK] All buckets within limits")
        return

    # Build alert message
    lines = [":warning: *Claude MAX Usage Alert*\n"]
    for alert in alerts_to_send:
        status_emoji = ":red_circle:" if alert["status"] != "allowed" else ":large_orange_circle:"
        lines.append(
            f"{status_emoji} *{alert['label']}*: {alert['used_pct']}% used "
            f"({alert['remaining_pct']}% remaining) · resets in {alert['reset_str']}"
        )

    if any(a["status"] != "allowed" for a in alerts_to_send):
        lines.append("\n:no_entry: *Rate limited* — Loma is paused until the limit resets.")

    message = "\n".join(lines)

    # Post to Slack
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel_id = os.environ.get("SLACK_ALERTS_CHANNEL_ID", "")

    if slack_token and channel_id:
        try:
            client = AsyncWebClient(token=slack_token)
            await client.chat_postMessage(channel=channel_id, text=message)
            logger.info("[USAGE_CHECK] Alert posted to %s", channel_id)
        except Exception:
            logger.exception("[USAGE_CHECK] Failed to post alert to Slack")
    else:
        logger.warning("[USAGE_CHECK] SLACK_BOT_TOKEN or SLACK_ALERTS_CHANNEL_ID not set, skipping Slack alert")
        logger.info("[USAGE_CHECK] Alert message:\n%s", message)

    # Record alerts in DB
    if db is not None:
        for alert in alerts_to_send:
            await db.usage_alerts.update_one(
                {"bucket": alert["bucket"], "reset_at": alert["reset"]},
                {"$set": {
                    "utilization": alert["utilization"],
                    "alerted_at": now,
                    "status": alert["status"],
                }},
                upsert=True,
            )
