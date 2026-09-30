"""First-run seeding of generic starter skills.

A freshly cloned Loma deployment starts with an empty `skills` collection, so the
agent has nothing useful to draw on. On startup we import a curated set of
generic, company-agnostic starter skills bundled under ``seed/skills/``.

Each bundled skill is imported only if no skill with its slug exists yet (in any
state, including disabled), so skills added to ``seed/skills/`` later still reach
existing deployments, while existing, edited or deleted skills are never
overwritten, updated or resurrected.
"""

from __future__ import annotations

import logging
from pathlib import Path

from api import skill_service
from config.app_config import LOMA_SEED_SKILLS

logger = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parent.parent / "seed" / "skills"


async def seed_default_skills(db) -> None:
    """Import every bundled starter skill whose slug does not exist yet.

    Idempotent and safe: a skill that already exists (edited by users, or disabled
    by deleting it) is never touched. Disable entirely with ``LOMA_SEED_SKILLS=false``.
    """
    if not LOMA_SEED_SKILLS:
        return
    if db is None:
        return

    if not SEED_DIR.is_dir():
        logger.warning("[SEED] Seed directory not found: %s", SEED_DIR)
        return

    try:
        existing = {doc["slug"] async for doc in db.skills.find({}, {"slug": 1}) if doc.get("slug")}
    except Exception:
        logger.exception("[SEED] Could not read skills collection; skipping seed")
        return

    candidates = []
    for child in sorted(p for p in SEED_DIR.iterdir() if p.is_dir()):
        try:
            slug = skill_service.slugify(child.name)
        except skill_service.SkillError:
            continue
        if slug not in existing:
            candidates.append(child)
    if not candidates:
        return

    await skill_service.ensure_skill_indexes(db)
    seeded: list[str] = []
    for child in candidates:
        try:
            result = await skill_service.import_skill_directory(db, child, actor="system")
            seeded.append(result.get("slug", child.name))
        except Exception:
            logger.exception("[SEED] Failed to seed starter skill from %s", child)

    if seeded:
        logger.info("[SEED] Seeded %d starter skills: %s", len(seeded), ", ".join(seeded))
    else:
        logger.warning("[SEED] No starter skills were seeded from %s", SEED_DIR)
