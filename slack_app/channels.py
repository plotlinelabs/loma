"""Optional Slack channel workflow registry.

The first OSS release keeps channel-specific automations disabled by default.
A later release will move these workflows into dashboard-managed database records.
"""

CHANNEL_CONFIGS: dict[str, dict] = {}


def get_channel_config(channel_id: str) -> dict | None:
    """Return config for a monitored channel, or None if not monitored."""
    return CHANNEL_CONFIGS.get(channel_id)
