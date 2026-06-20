"""Centralized runtime defaults for self-hosted Loma deployments."""

from __future__ import annotations

import os


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


APP_NAME = os.environ.get("APP_NAME", "Loma")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:3001")
OBSERVABILITY_DB_NAME = os.environ.get("OBSERVABILITY_DB_NAME", "loma_observability")
GITHUB_DEFAULT_ORG = os.environ.get("GITHUB_DEFAULT_ORG", "")
LOMA_SKILL_ASSET_DIR = os.environ.get("LOMA_SKILL_ASSET_DIR", "/var/lib/loma/skill-assets")

LOMA_ENABLE_SCHEDULER = env_flag("LOMA_ENABLE_SCHEDULER", default=False)
LOMA_ENABLE_WEBHOOKS = env_flag("LOMA_ENABLE_WEBHOOKS", default=True)
LOMA_ENABLE_METRICS = env_flag("LOMA_ENABLE_METRICS", default=False)
# Slack Socket Mode consumer. Defaults to on so existing deployments are
# unchanged; preview/ephemeral stacks set this to false (or omit SLACK_APP_TOKEN)
# so they don't double-consume the production Slack app's events.
LOMA_ENABLE_SLACK = env_flag("LOMA_ENABLE_SLACK", default=True)
