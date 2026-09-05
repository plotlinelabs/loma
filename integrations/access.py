"""Shared, fail-closed integration authorization for trusted callers."""


def allows_user(integration: dict, email: str, team_ids=()) -> bool:
    if not email or integration.get('status') != 'active':
        return False
    # Legacy custom connectors are private too: never assume org ownership.
    if integration.get('scope') == 'personal' or integration.get('is_custom'):
        return integration.get('connected_by') == email
    shared = integration.get('shared_with') or {'mode': 'everyone'}
    if shared.get('mode') == 'everyone':
        return True
    if shared.get('mode') != 'specific':
        return False
    return email in (shared.get('users') or []) or bool(
        set(team_ids) & set(shared.get('teams') or [])
    )


async def can_access(db, integration: dict, email: str) -> bool:
    if db is None or not email:
        return False
    teams = []
    if (integration.get('shared_with') or {}).get('teams'):
        async for team in db.teams.find({'members': email}, {'team_id': 1}):
            teams.append(team['team_id'])
    return allows_user(integration, email, teams)


async def require_provider(db, provider: str, email: str) -> None:
    from broker.service import Denied
    try:
        doc = await db.integrations.find_one({'provider': provider}) if db is not None else None
        if not doc or not await can_access(db, doc, email):
            raise Denied()
    except Exception as exc:
        raise Denied() from exc
