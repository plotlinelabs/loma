"""First-run seeding of generic starter skills.

A freshly cloned Loma deployment starts with an empty `skills` collection, so the
agent has nothing useful to draw on. On startup we import a curated set of
generic, company-agnostic starter skills bundled under ``seed/skills/`` — but
ONLY when the collection is completely empty, so existing deployments are never
touched and skills are never overwritten or duplicated.
"""

from __future__ import annotations

import logging
from pathlib import Path

from api import skill_service
from config.app_config import LOMA_SEED_SKILLS

logger = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parent.parent / "seed" / "skills"


async def seed_default_skills(db) -> None:
    """Import bundled starter skills if (and only if) no skills exist yet.

    Idempotent and safe: keyed on the whole ``skills`` collection being empty, so
    it never runs on a populated deployment (e.g. an upgrade) and can't collide
    with user-authored skills. Disable entirely with ``LOMA_SEED_SKILLS=false``.
    """
    if not LOMA_SEED_SKILLS:
        return
    if db is None:
        return

    try:
        existing = await db.skills.count_documents({})
    except Exception:
        logger.exception("[SEED] Could not read skills collection; skipping seed")
        return
    if existing > 0:
        return

    if not SEED_DIR.is_dir():
        logger.warning("[SEED] Seed directory not found: %s", SEED_DIR)
        return

    await skill_service.ensure_skill_indexes(db)
    seeded: list[str] = []
    for child in sorted(p for p in SEED_DIR.iterdir() if p.is_dir()):
        try:
            result = await skill_service.import_skill_directory(db, child, actor="system")
            seeded.append(result.get("slug", child.name))
        except Exception:
            logger.exception("[SEED] Failed to seed starter skill from %s", child)

    if seeded:
        logger.info("[SEED] Seeded %d starter skills: %s", len(seeded), ", ".join(seeded))
    else:
        logger.warning("[SEED] No starter skills were seeded from %s", SEED_DIR)
