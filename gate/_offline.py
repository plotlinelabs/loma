"""Shared helper for offline gate tooling (backtests / analysis scripts).

These scripts run OUTSIDE the app process, so the central
`observability.db.get_db()` global is never initialised (and `init_observability`
creates indexes, which a read-only backtest must not do). This gives a plain
read connection to the observability DB, loading env via python-dotenv (the same
dependency the app uses) rather than hand-parsing `.env`.

Not used by the production path — `gate/shadow.py` receives `db` from the
centrally-initialised `webhook_executor`.
"""

from __future__ import annotations

import os
from pathlib import Path


def offline_db():
    from dotenv import load_dotenv
    from pymongo import MongoClient

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        raise RuntimeError("OBSERVABILITY_MONGODB_URI not set (offline gate tooling)")
    return MongoClient(uri, serverSelectionTimeoutMS=20000).loma_observability
