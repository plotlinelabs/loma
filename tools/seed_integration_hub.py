#!/usr/bin/env python3
"""Seed repeatable Integration Hub pilot clients.

Usage:
  OBSERVABILITY_MONGODB_URI=... python3 tools/seed_integration_hub.py
"""

import asyncio
import os
import sys
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.app_config import OBSERVABILITY_DB_NAME  # noqa: E402
from integration_hub.service import AccountService  # noqa: E402
from integration_hub.repository import AccountRepository  # noqa: E402


PILOT_CLIENTS = (
    {"name": "Pilot Mobile Client", "platforms": ["android", "ios"], "environments": ["development"]},
    {"name": "Pilot Web Client", "platforms": ["web"], "environments": ["staging"]},
)


async def main():
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri.startswith("mongodb"):
        raise SystemExit("OBSERVABILITY_MONGODB_URI must be configured")
    client = AsyncIOMotorClient(uri)
    repository = AccountRepository(client[OBSERVABILITY_DB_NAME])
    service = AccountService(repository)
    for seed in PILOT_CLIENTS:
        existing = await repository.collection.find_one({"name": seed["name"]})
        if existing:
            print(f"skip: {seed['name']}")
            continue
        account = await service.create(seed, "pilot-seed@plotline.so")
        playbook = "web_sdk" if seed["platforms"] == ["web"] else "mobile_sdk"
        await service.create_project(
            account["account_id"],
            {"name": "Primary onboarding", "playbook": playbook},
            "pilot-seed@plotline.so",
        )
        print(f"created: {seed['name']}")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
