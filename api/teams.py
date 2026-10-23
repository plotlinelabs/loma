"""Shared team helpers: id/list normalisation and the team field on skills and flows."""

import re

TEAM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
MAX_FOLDER_LEN = 60


class TeamError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def slugify_team_id(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug[:48].strip("-")


def clean_string_list(value, field: str, *, lower: bool = False) -> list[str]:
    """Trimmed, de-duplicated list of non-empty strings (order kept)."""
    if not isinstance(value, list):
        raise TeamError(f"'{field}' must be a list of strings")
    seen: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TeamError(f"'{field}' must be a list of strings")
        item = item.strip().lower() if lower else item.strip()
        if item and item not in seen:
            seen.append(item)
    return seen


def clean_folder(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TeamError("'folder' must be a string or null")
    value = value.strip()
    if len(value) > MAX_FOLDER_LEN:
        raise TeamError(f"'folder' must be at most {MAX_FOLDER_LEN} characters")
    return value or None


async def resolve_team_id(db, value) -> str | None:
    """None/'' clears the team; any other value must name an existing team."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise TeamError("'team_id' must be a string or null")
    team = await db.teams.find_one({"team_id": value.strip()})
    if team is None:
        raise TeamError("Team not found", status=404)
    return team["team_id"]
