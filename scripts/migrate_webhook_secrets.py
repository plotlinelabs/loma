"""One-time migration: encrypt plaintext webhook auth_secret values.

Finds all flows with webhook_config.auth_secret (plaintext) and replaces
it with webhook_config.auth_secret_encrypted (Fernet-encrypted).

Usage:
    python scripts/migrate_webhook_secrets.py [--dry-run]
"""

import asyncio
import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from api.oauth_helpers import encrypt_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def migrate(dry_run: bool = False):
    from observability.db import init_db, get_db

    await init_db()
    db = get_db()
    if db is None:
        logger.error("Database not available")
        return

    cursor = db.flows.find({
        "webhook_config.auth_secret": {"$exists": True, "$ne": ""},
    })

    migrated = 0
    skipped = 0
    async for flow in cursor:
        flow_id = flow["flow_id"]
        secret = flow["webhook_config"].get("auth_secret", "")

        if not secret:
            skipped += 1
            continue

        if dry_run:
            logger.info("[DRY RUN] Would encrypt secret for flow %s (%s)",
                        flow_id, flow.get("name", ""))
            migrated += 1
            continue

        try:
            encrypted = encrypt_token(secret)
            await db.flows.update_one(
                {"flow_id": flow_id},
                {
                    "$set": {"webhook_config.auth_secret_encrypted": encrypted},
                    "$unset": {"webhook_config.auth_secret": ""},
                },
            )
            logger.info("Encrypted secret for flow %s (%s)", flow_id, flow.get("name", ""))
            migrated += 1
        except Exception:
            logger.exception("Failed to encrypt secret for flow %s", flow_id)

    logger.info("Migration complete: %d encrypted, %d skipped", migrated, skipped)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logger.info("Running in DRY RUN mode")
    asyncio.run(migrate(dry_run=dry_run))
