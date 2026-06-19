"""Sentry REST API client (ISSUE-3426).

Thin async wrapper around https://docs.sentry.io/api/. Used by
``metrics/sentry_perf.py`` to pull platform-level daily metrics and by ad-hoc
CLI calls.

Requires env:
  SENTRY_ACCESS_TOKEN   User auth token (sntryu_...) or internal-integration token
                        Needed scopes: org:read, project:read, event:read
                        (Also accepts the legacy name SENTRY_AUTH_TOKEN as a fallback
                        so local dev configs work either way.)
  SENTRY_ORG_SLUG       Optional. Defaults to ``loma-2y`` (the live org;
                        org ``loma`` is over member-limit and not accessible).

Notes:
  * ``events-stats`` requires NUMERIC project ids, not slugs.
  * ``sessions`` accepts either, but we pass numeric ids for consistency.
  * All time ranges are UTC and the API expects ``YYYY-MM-DDTHH:MM:SSZ``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

SENTRY_BASE_URL = "https://sentry.io/api/0"


def _token() -> str:
    # Production uses SENTRY_ACCESS_TOKEN; legacy / local dev may still use SENTRY_AUTH_TOKEN.
    t = os.environ.get("SENTRY_ACCESS_TOKEN", "").strip() or os.environ.get("SENTRY_AUTH_TOKEN", "").strip()
    if not t:
        raise ValueError("SENTRY_ACCESS_TOKEN not set (or legacy SENTRY_AUTH_TOKEN)")
    return t


def _org_slug() -> str:
    return os.environ.get("SENTRY_ORG_SLUG", "loma-2y").strip()


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}", "Accept": "application/json"}


async def _get(session: aiohttp.ClientSession, path: str, params: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    url = f"{SENTRY_BASE_URL}{path}"
    async with session.get(url, headers=_headers(), params=params, timeout=aiohttp.ClientTimeout(total=40)) as resp:
        text = await resp.text()
        if resp.status != 200:
            logger.warning("Sentry GET %s -> HTTP %d: %s", path, resp.status, text[:300])
            return {"error": text[:300], "status": resp.status}
        try:
            return json.loads(text)
        except Exception:
            return {"error": "non-json response", "raw": text[:300]}


async def list_projects() -> list[dict]:
    async with aiohttp.ClientSession() as session:
        data = await _get(session, f"/organizations/{_org_slug()}/projects/")
        if isinstance(data, list):
            return data
        return []


async def _stats_series(
    session: aiohttp.ClientSession,
    project_id: int,
    y_axis: str,
    start: str,
    end: str,
    *,
    query: str = "",
    environment: str = "production",
    interval: str = "1d",
) -> list[tuple[int, float]]:
    """One yAxis over (start, end). Returns [(epoch_seconds, value), ...]."""
    params = [
        ("project", str(project_id)),
        ("yAxis", y_axis),
        ("interval", interval),
        ("start", start),
        ("end", end),
        ("environment", environment),
        ("query", query),
    ]
    data = await _get(session, f"/organizations/{_org_slug()}/events-stats/", params)
    series = []
    for point in data.get("data", []) or []:
        ts = point[0]
        bucket = point[1] or [{}]
        v = bucket[0].get("count") if bucket else None
        if v is None:
            v = 0.0
        series.append((int(ts), float(v)))
    return series


async def _sessions(
    session: aiohttp.ClientSession,
    project_id: int,
    start: str,
    end: str,
    *,
    environment: str = "production",
    interval: str = "1d",
) -> dict[str, Any]:
    """Group-by session.status — totals for crash-free rate."""
    params = [
        ("project", str(project_id)),
        ("field", "sum(session)"),
        ("field", "count_unique(user)"),
        ("groupBy", "session.status"),
        ("interval", interval),
        ("start", start),
        ("end", end),
        ("environment", environment),
    ]
    return await _get(session, f"/organizations/{_org_slug()}/sessions/", params)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_slow_transactions(
    project_id: int,
    *,
    days: int = 7,
    limit: int = 15,
    environment: str = "production",
    sort_by: str = "p95",  # one of: p95, p75, p50, count, failure_rate
) -> list[dict]:
    """Top transactions by p95 latency over the trailing window.

    Returns rows shaped like:
      {
        "transaction": "/login",
        "count": 68,
        "p50_ms": 3354.0, "p75_ms": ..., "p95_ms": 8359.0,
        "failure_rate": 0.0,
      }
    """
    sort_field = {
        "p95": "-p95_transaction_duration",
        "p75": "-p75_transaction_duration",
        "p50": "-p50_transaction_duration",
        "count": "-count",
        "failure_rate": "-failure_rate",
    }.get(sort_by, "-p95_transaction_duration")
    params = [
        ("field", "transaction"),
        ("field", "count()"),
        ("field", "p50(transaction.duration)"),
        ("field", "p75(transaction.duration)"),
        ("field", "p95(transaction.duration)"),
        ("field", "failure_rate()"),
        ("sort", sort_field),
        ("per_page", str(limit)),
        ("query", "event.type:transaction"),
        ("project", str(project_id)),
        ("environment", environment),
        ("statsPeriod", f"{days}d"),
    ]
    async with aiohttp.ClientSession() as session:
        data = await _get(session, f"/organizations/{_org_slug()}/events/", params)
    rows = []
    for r in data.get("data", []) or []:
        rows.append({
            "transaction": r.get("transaction") or "(unknown)",
            "count": int(r.get("count()") or 0),
            "p50_ms": round(float(r.get("p50(transaction.duration)") or 0), 1),
            "p75_ms": round(float(r.get("p75(transaction.duration)") or 0), 1),
            "p95_ms": round(float(r.get("p95(transaction.duration)") or 0), 1),
            "failure_rate": round(float(r.get("failure_rate()") or 0), 4),
        })
    return rows


async def fetch_daily_metrics(
    project_id: int,
    days: int,
    *,
    environment: str = "production",
) -> list[dict]:
    """Pull all daily metric series for one project, return one dict per UTC day.

    Output rows have shape:
      {
        "day": "YYYY-MM-DD",
        "events_count": int,
        "error_events": int,
        "unhandled_events": int,
        "failure_rate": float,
        "txn_p50_ms": float, "txn_p75_ms": float, "txn_p95_ms": float,
        "lcp_p75_ms": float, "fcp_p75_ms": float, "cls_p75": float, "inp_p75_ms": float,
        "sessions_total": int, "sessions_healthy": int, "sessions_crashed": int,
        "sessions_errored": int, "sessions_abnormal": int,
        "users_total": int, "users_crashed": int,
        "crash_free_session_rate": float | None,
        "crash_free_user_rate": float | None,
      }
    """
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    iso_start, iso_end = _iso(start), _iso(end)
    txn_query = "event.type:transaction"

    # All Sentry reads are independent — fire 11 stat-series + N session calls
    # together and let aiohttp pipeline them. Cuts wall-clock from ~18×latency
    # to ~1×latency on the daily refresh.
    async with aiohttp.ClientSession() as session:
        series_specs = [
            ("count()",                       ""),
            ("count()",                       "event.type:error"),
            ("count()",                       "error.unhandled:true"),
            ("failure_rate()",                ""),
            ("p50(transaction.duration)",     txn_query),
            ("p75(transaction.duration)",     txn_query),
            ("p95(transaction.duration)",     txn_query),
            ("p75(measurements.lcp)",         txn_query),
            ("p75(measurements.fcp)",         txn_query),
            ("p75(measurements.cls)",         txn_query),
            ("p75(measurements.inp)",         txn_query),
        ]
        series_tasks = [
            _stats_series(session, project_id, y, iso_start, iso_end,
                          query=q, environment=environment)
            for y, q in series_specs
        ]
        day_starts = []
        d = start
        while d < end:
            day_starts.append(d)
            d = d + timedelta(days=1)
        session_tasks = [
            _sessions(session, project_id, _iso(d0), _iso(d0 + timedelta(days=1)),
                      environment=environment)
            for d0 in day_starts
        ]
        results = await asyncio.gather(*series_tasks, *session_tasks)
        (ev_total, ev_error, ev_unhandled, failure_rate,
         txn_p50, txn_p75, txn_p95,
         lcp, fcp, cls, inp) = results[: len(series_specs)]
        session_results = results[len(series_specs):]

    sessions_by_day: dict[str, dict] = {}
    for d0, sess in zip(day_starts, session_results):
        row = {"sessions_total": 0, "sessions_healthy": 0, "sessions_crashed": 0,
               "sessions_errored": 0, "sessions_abnormal": 0,
               "users_total": 0, "users_crashed": 0}
        for grp in sess.get("groups", []) or []:
            status = (grp.get("by") or {}).get("session.status", "unknown")
            tot = (grp.get("totals") or {}).get("sum(session)", 0) or 0
            usr = (grp.get("totals") or {}).get("count_unique(user)", 0) or 0
            row["sessions_total"] += tot
            row["users_total"] += usr
            if status == "healthy":
                row["sessions_healthy"] = tot
            elif status == "crashed":
                row["sessions_crashed"] = tot
                row["users_crashed"] = usr
            elif status == "errored":
                row["sessions_errored"] = tot
            elif status == "abnormal":
                row["sessions_abnormal"] = tot
        sessions_by_day[d0.strftime("%Y-%m-%d")] = row

    # Build day-indexed dict from event-stats series (all share the same daily ticks)
    by_day: dict[str, dict] = {}
    def _set(series: list[tuple[int, float]], field: str, as_int: bool = False) -> None:
        for ts, v in series:
            day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            cell = by_day.setdefault(day, {"day": day})
            cell[field] = int(v) if as_int else round(v, 4)

    _set(ev_total, "events_count", as_int=True)
    _set(ev_error, "error_events", as_int=True)
    _set(ev_unhandled, "unhandled_events", as_int=True)
    _set(failure_rate, "failure_rate")
    _set(txn_p50, "txn_p50_ms")
    _set(txn_p75, "txn_p75_ms")
    _set(txn_p95, "txn_p95_ms")
    _set(lcp, "lcp_p75_ms")
    _set(fcp, "fcp_p75_ms")
    _set(cls, "cls_p75")
    _set(inp, "inp_p75_ms")

    for day, row in by_day.items():
        sess = sessions_by_day.get(day, {})
        row.update(sess)
        total_sess = sess.get("sessions_total", 0)
        crashed_sess = sess.get("sessions_crashed", 0)
        row["crash_free_session_rate"] = round(1.0 - (crashed_sess / total_sess), 6) if total_sess else None
        total_usr = sess.get("users_total", 0)
        crashed_usr = sess.get("users_crashed", 0)
        row["crash_free_user_rate"] = round(1.0 - (crashed_usr / total_usr), 6) if total_usr else None

    return sorted(by_day.values(), key=lambda r: r["day"])


async def _cli():
    p = argparse.ArgumentParser(description="Sentry probe / fetch helper")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("projects", help="List projects in the configured org")
    fetch = sub.add_parser("daily", help="Fetch daily metrics for a project")
    fetch.add_argument("project_id", type=int)
    fetch.add_argument("--days", type=int, default=7)
    fetch.add_argument("--environment", default="production")
    slow = sub.add_parser("slow", help="Top slow transactions for a project")
    slow.add_argument("project_id", type=int)
    slow.add_argument("--days", type=int, default=7)
    slow.add_argument("--limit", type=int, default=15)
    slow.add_argument("--environment", default="production")
    args = p.parse_args()
    if args.cmd == "projects":
        projects = await list_projects()
        for pr in projects:
            print(f"{pr.get('id')}\t{pr.get('slug')}\tplatform={pr.get('platform')}")
        return
    if args.cmd == "daily":
        rows = await fetch_daily_metrics(args.project_id, args.days, environment=args.environment)
        for row in rows:
            print(json.dumps(row))
        return
    if args.cmd == "slow":
        rows = await fetch_slow_transactions(args.project_id, days=args.days, limit=args.limit, environment=args.environment)
        for row in rows:
            print(json.dumps(row))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(_cli())
