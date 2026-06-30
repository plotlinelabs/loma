#!/usr/bin/env python3
"""One-off: move all personal-scoped skills to workspace scope."""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from motor.motor_asyncio import AsyncIOMotorClient
from config.app_config import OBSERVABILITY_DB_NAME
from api import skill_service


async def main():
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        print("OBSERVABILITY_MONGODB_URI not set")
        sys.exit(1)

    client = AsyncIOMotorClient(uri)
    db = client[OBSERVABILITY_DB_NAME]

    skills = await skill_service.list_skills(db)
    personal = [s for s in skills if s.get("scope") == "personal"]

    if not personal:
        print("No personal-scoped skills found.")
        return

    print(f"Moving {len(personal)} personal skills to workspace scope:")
    for s in personal:
        slug = s["slug"]
        try:
            await skill_service.update_skill_scope(db, slug=slug, scope="workspace", actor="migration")
            print(f"  ✓ {slug}")
        except Exception as e:
            print(f"  ✗ {slug}: {e}")

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
