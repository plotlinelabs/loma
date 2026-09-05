"""Identity and sharing checks for direct organization-integration CLIs.

Run before command dispatch, including when environment credentials exist.
No authorization cache: removal of a user, team, or share takes effect on the
next invocation. Imported functions are trusted backend code, not CLI entrypoints.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.access import allows_user
from tools._auth_token import verify_user_auth_token


class IntegrationAccessDenied(PermissionError):
    pass


def split_identity(argv: list[str]) -> tuple[str, str, list[str]]:
    values = {}
    clean = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        flag, sep, value = arg.partition('=')
        if flag in ('--user-email', '--auth-token'):
            if flag in values:
                raise IntegrationAccessDenied('Duplicate identity argument')
            if not sep:
                index += 1
                if index >= len(argv) or argv[index].startswith('--'):
                    raise IntegrationAccessDenied('Missing identity value')
                value = argv[index]
            if not value:
                raise IntegrationAccessDenied('Missing identity value')
            values[flag] = value
        else:
            clean.append(arg)
        index += 1
    if set(values) != {'--user-email', '--auth-token'}:
        raise IntegrationAccessDenied('Authenticated user credentials required')
    return values['--user-email'], values['--auth-token'], clean


def require_cli_access(provider: str, email: str, token: str) -> None:
    client = None
    try:
        if not verify_user_auth_token(token, email):
            raise IntegrationAccessDenied()
        uri = os.environ.get('OBSERVABILITY_MONGODB_URI', '').strip()
        if not uri:
            raise IntegrationAccessDenied()
        from pymongo import MongoClient
        from config.app_config import OBSERVABILITY_DB_NAME
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        db = client[OBSERVABILITY_DB_NAME]
        user = db.users.find_one({'email': email})
        if not user or user.get('status') != 'active':
            raise IntegrationAccessDenied()
        doc = db.integrations.find_one({'provider': provider})
        if not doc:
            raise IntegrationAccessDenied()
        teams = []
        if (doc.get('shared_with') or {}).get('teams'):
            teams = [team['team_id'] for team in db.teams.find(
                {'members': email}, {'team_id': 1},
            )]
        if not allows_user(doc, email, teams):
            raise IntegrationAccessDenied()
    except Exception as exc:
        raise IntegrationAccessDenied('Integration access denied or unavailable') from exc
    finally:
        if client is not None:
            client.close()


def authorize_cli(provider: str, *, preserve_identity: bool = False) -> None:
    try:
        email, token, clean = split_identity(sys.argv[1:])
        require_cli_access(provider, email, token)
        # Ashby additionally checks a provider-specific allowlist itself.
        identity = ['--user-email', email, '--auth-token', token] if preserve_identity else []
        sys.argv[:] = [sys.argv[0], *clean, *identity]
    except IntegrationAccessDenied:
        print(json.dumps({'error': 'Integration access denied. Supply valid --user-email and --auth-token and check the connection sharing settings.'}))
        raise SystemExit(1) from None
