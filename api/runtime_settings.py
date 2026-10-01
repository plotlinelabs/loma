"""Admin-managed runtime preferences. Never reads or writes an environment file.

Existing environment overrides take precedence for deployment compatibility.
Missing admin configuration keeps bounded work off and Ashby access denied.
"""
import os


def effective(config=None):
    config = config or {}
    def flag(name, field, default):
        value = os.environ.get(name)
        return value.strip().lower() == "true" if value is not None else config.get(field, default) is True
    users = os.environ.get("ASHBY_ALLOWED_USERS")
    allowed = [email.strip().lower() for email in users.split(",") if email.strip()] if users is not None else config.get("ashby_allowed_users", [])
    return {
        "bounded_work_enabled": flag("LOMA_BOUNDED_WORK_ENABLED", "bounded_work_enabled", False),
        "ashby_allowed_users": allowed,
        "overrides": [name for name in ("LOMA_BOUNDED_WORK_ENABLED", "ASHBY_ALLOWED_USERS") if name in os.environ],
    }


async def read(db):
    if db is None:
        raise RuntimeError("Runtime settings database unavailable")
    return effective(await db.gateway_config.find_one({"_id": "runtime-settings"}))
