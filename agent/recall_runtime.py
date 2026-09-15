"""Per-turn recall lifecycle, shared by interactive and headless task execution."""
import asyncio
import logging
import os
from pathlib import Path
import sys
from api.recall_session import issuer_call, issuer_url

logger = logging.getLogger(__name__)


async def refresh_history(user_id):
    from observability.db import get_db
    from scripts.recall_reconcile import reconcile_owner
    from api.recall_index import ensure_indexes
    db = get_db()
    if db is None:
        return
    try:
        await ensure_indexes(db)
        async with asyncio.timeout(20):
            await reconcile_owner(db, user_id)
    except Exception:
        # Search coverage stays partial/delayed; never abort an ordinary chat.
        logger.warning('Recall reconciliation incomplete')


def runtime_config(session):
    return {'type': 'stdio', 'command': sys.executable,
        'args': [str(Path(__file__).resolve().parents[1] / 'mcp-servers/loma-recall/server.py')],
        'env': {'LOMA_RECALL_BACKEND_URL': os.environ.get('LOMA_RECALL_BACKEND_URL',
                    'http://127.0.0.1:' + os.environ.get('WEBHOOK_PORT', '3000')),
                'LOMA_RECALL_ISSUER_URL': issuer_url(),
                'LOMA_RECALL_GRANT': session['grant'],
                'LOMA_RECALL_CAPABILITY': session['capability'],
                'LOMA_RECALL_EXPIRES_AT': str(session['expires_at'])}}


async def revoke(session):
    try:
        await issuer_call({'action': 'revoke', 'grant': session['grant']})
    except Exception:
        # Bounded two-hour issuer expiry remains a backstop for process death.
        logger.warning('Recall cleanup unavailable; grant will expire')
