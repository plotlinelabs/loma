#!/usr/bin/env python3
"""One-off: move all personal-scoped skills to workspace scope."""

import argparse
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
    from tools._auth_token import verify_user_auth_token
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-email', required=True)
    parser.add_argument('--auth-token', required=True)
    parser.add_argument('--apply', action='store_true', help='Apply changes; default is dry-run')
    args = parser.parse_args()
    if not verify_user_auth_token(args.auth_token, args.user_email):
        raise SystemExit("Authentication required")
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        print("OBSERVABILITY_MONGODB_URI not set")
        sys.exit(1)

    client = AsyncIOMotorClient(uri)
    db = client[OBSERVABILITY_DB_NAME]

    user = await db.users.find_one({'email': args.user_email})
    if not user or user.get('status') != 'active' or user.get('system_role') != 'admin':
        client.close()
        raise SystemExit("Active administrator required")

    skills = await skill_service.list_skills(db)
    personal = [s for s in skills if s.get("scope") == "personal"]

    if not personal:
        print("No personal-scoped skills found.")
        return

    if not args.apply:
        print(f"Dry run: {len(personal)} personal skills would move. Use --apply to proceed.")
        client.close()
        return
    print(f"Moving {len(personal)} personal skills to workspace scope:")
    for s in personal:
        slug = s["slug"]
        try:
            await skill_service.update_skill_scope(db, slug=slug, scope="workspace", actor=args.user_email)
            print(f"  ✓ {slug}")
        except Exception as e:
            print(f"  ✗ {slug}: {e}")

    client.close()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
