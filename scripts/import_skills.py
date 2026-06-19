#!/usr/bin/env python3
"""Import file-based skills into MongoDB/local asset storage.

Usage:
  python3 scripts/import_skills.py --source /path/to/.claude/skills --actor admin@example.com
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import skill_service  # noqa: E402
from config.app_config import OBSERVABILITY_DB_NAME  # noqa: E402


async def run(source: Path, actor: str) -> dict:
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        raise RuntimeError("OBSERVABILITY_MONGODB_URI is not set")
    client = AsyncIOMotorClient(uri)
    db = client[OBSERVABILITY_DB_NAME]
    try:
        await skill_service.ensure_skill_indexes(db)
        imported: list[str] = []
        failed: list[dict] = []
        for child in sorted(p for p in source.iterdir() if p.is_dir()):
            try:
                item = await skill_service.import_skill_directory(db, child, actor=actor)
                imported.append(item.get("slug"))
            except Exception as exc:  # keep going so one bad skill can't abort the run
                failed.append({"slug": child.name, "error": str(exc)})
        return {"imported": imported, "failed": failed, "count": len(imported)}
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Loma skills from a directory")
    parser.add_argument("--source", required=True, help="Directory containing skill subdirectories")
    parser.add_argument("--actor", default="import", help="Actor email/name to record in version history")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(Path(args.source), args.actor))
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
