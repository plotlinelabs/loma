#!/usr/bin/env python3
"""First-party Loma skill tool for agent access.

Examples:
  python3 tools/loma_skills.py list
  python3 tools/loma_skills.py search --query onboarding
  python3 tools/loma_skills.py get --slug onboarding
  python3 tools/loma_skills.py file --slug onboarding --path SKILL.md
  python3 tools/loma_skills.py update-file --slug onboarding --path SKILL.md --content-file /tmp/SKILL.md --user-email u@co.com --auth-token TOKEN
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
from api.auth_helpers import ROLE_HIERARCHY  # noqa: E402
from config.app_config import OBSERVABILITY_DB_NAME  # noqa: E402
from tools._auth_token import verify_user_auth_token  # noqa: E402


def _json(data, status: int = 0) -> int:
    print(json.dumps(data, indent=2, default=str))
    return status


def _connect_db():
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        raise RuntimeError("OBSERVABILITY_MONGODB_URI is not set")
    client = AsyncIOMotorClient(uri)
    return client, client[OBSERVABILITY_DB_NAME]


async def _require_maintainer(db, user_email: str | None, auth_token: str | None) -> str:
    if not user_email or not auth_token:
        raise skill_service.SkillError("Write commands require --user-email and --auth-token", status=403)
    if not verify_user_auth_token(auth_token, user_email):
        raise skill_service.SkillError("Invalid or expired auth token", status=403)
    user = await db.users.find_one({"email": user_email})
    role = (user or {}).get("system_role", "chatter")
    if ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY["maintainer"]:
        raise skill_service.SkillError("Maintainer access required", status=403)
    return user_email


def _compact_skill(skill: dict) -> dict:
    return {
        "slug": skill.get("slug"),
        "name": skill.get("name"),
        "description": skill.get("description"),
        "tags": skill.get("tags") or [],
        "files": skill.get("files") or [],
        "assets": skill.get("assets") or [],
    }


async def _run(args) -> int:
    client, db = _connect_db()
    try:
        if args.command == "list":
            return _json({"skills": [_compact_skill(s) for s in await skill_service.list_skills(db)]})

        if args.command == "search":
            return _json({"skills": [_compact_skill(s) for s in await skill_service.search_skills(db, args.query)]})

        if args.command == "get":
            skill = await skill_service.get_skill(db, args.slug)
            return _json(_compact_skill(skill) | {"content": skill.get("content", "")})

        if args.command == "file":
            file_doc = await skill_service.get_skill_file(db, args.slug, args.path)
            if file_doc.get("kind") != "inline_text":
                return _json({"error": "Requested file is an asset. Use the asset command."}, 1)
            return _json({
                "slug": args.slug,
                "path": file_doc["path"],
                "content": file_doc.get("content", ""),
                "content_type": file_doc.get("content_type"),
            })

        if args.command == "asset":
            file_doc = await skill_service.get_skill_file(db, args.slug, args.path)
            if file_doc.get("kind") != "local_asset":
                return _json({"error": "Requested file is not an asset."}, 1)
            return _json({
                "slug": args.slug,
                "path": file_doc["path"],
                "content_type": file_doc.get("content_type"),
                "size_bytes": file_doc.get("size_bytes"),
                "asset_path": file_doc.get("asset_path"),
                "original_filename": file_doc.get("original_filename"),
            })

        if args.command == "create":
            actor = await _require_maintainer(db, args.user_email, args.auth_token)
            content = Path(args.skill_md).read_text(encoding="utf-8")
            result = await skill_service.upsert_skill(
                db,
                slug=args.slug,
                files=[skill_service.validate_text_file("SKILL.md", content)],
                actor=actor,
                source="agent",
                message="Created skill from agent",
            )
            return _json(_compact_skill(result))

        if args.command == "update-file":
            actor = await _require_maintainer(db, args.user_email, args.auth_token)
            content = Path(args.content_file).read_text(encoding="utf-8")
            result = await skill_service.update_skill_file(
                db,
                slug=args.slug,
                file_doc=skill_service.validate_text_file(args.path, content),
                actor=actor,
                source="agent",
            )
            return _json(_compact_skill(result))

        if args.command == "upload-asset":
            actor = await _require_maintainer(db, args.user_email, args.auth_token)
            asset_path = Path(args.file)
            result = await skill_service.update_skill_file(
                db,
                slug=args.slug,
                file_doc=skill_service.store_asset(
                    skill_service.slugify(args.slug),
                    args.path,
                    asset_path.read_bytes(),
                    asset_path.name,
                ),
                actor=actor,
                source="agent",
                message=f"Uploaded {args.path} from agent",
            )
            return _json(_compact_skill(result))

        if args.command == "delete-file":
            actor = await _require_maintainer(db, args.user_email, args.auth_token)
            result = await skill_service.delete_skill_file(
                db,
                slug=args.slug,
                path=args.path,
                actor=actor,
                source="agent",
            )
            return _json(_compact_skill(result))

        if args.command == "import-dir":
            actor = await _require_maintainer(db, args.user_email, args.auth_token)
            imported = []
            root = Path(args.source)
            for child in sorted(p for p in root.iterdir() if p.is_dir()):
                imported.append(_compact_skill(await skill_service.import_skill_directory(db, child, actor=actor)))
            return _json({"imported": imported})

        return _json({"error": f"Unknown command: {args.command}"}, 1)
    except skill_service.SkillError as exc:
        return _json({"error": str(exc), "status": exc.status}, 1)
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Loma DB-native skill tool")
    parser.add_argument("--user-email")
    parser.add_argument("--auth-token")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_write_auth(p):
        p.add_argument("--user-email")
        p.add_argument("--auth-token")

    sub.add_parser("list")
    search = sub.add_parser("search")
    search.add_argument("--query", required=True)
    get = sub.add_parser("get")
    get.add_argument("--slug", required=True)
    file_cmd = sub.add_parser("file")
    file_cmd.add_argument("--slug", required=True)
    file_cmd.add_argument("--path", required=True)
    asset = sub.add_parser("asset")
    asset.add_argument("--slug", required=True)
    asset.add_argument("--path", required=True)
    create = sub.add_parser("create")
    create.add_argument("--slug", required=True)
    create.add_argument("--skill-md", required=True)
    add_write_auth(create)
    update = sub.add_parser("update-file")
    update.add_argument("--slug", required=True)
    update.add_argument("--path", required=True)
    update.add_argument("--content-file", required=True)
    add_write_auth(update)
    upload = sub.add_parser("upload-asset")
    upload.add_argument("--slug", required=True)
    upload.add_argument("--path", required=True)
    upload.add_argument("--file", required=True)
    add_write_auth(upload)
    delete = sub.add_parser("delete-file")
    delete.add_argument("--slug", required=True)
    delete.add_argument("--path", required=True)
    add_write_auth(delete)
    import_dir = sub.add_parser("import-dir")
    import_dir.add_argument("--source", required=True)
    add_write_auth(import_dir)

    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
