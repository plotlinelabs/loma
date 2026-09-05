"""Linear GraphQL client for engineering-health metrics (ISSUE-3422).

The Linear *MCP* does not expose issue `estimate`, so the metrics pipeline reads
Linear directly via GraphQL. This module provides:

  - fetch_release_events(since, until)  — "released to production" events for velocity (3423)
  - fetch_planned_roadmap(month_label)  — planned vs shipped roadmap for bucket-split (3425)

Domain rules (see eng-health-metrics-ground-rules.md):
  - The marker for velocity is the transition INTO the "Released to Production" state.
  - Class precedence is Roadmap > Adhoc > Bug — a ticket tagged Roadmap + Bug is a
    *planned* bug-fix and counts as Roadmap, NOT Bug. The Bug bucket = unplanned bugs only.
  - "Planned" roadmap = `Roadmap` + `<Mon YYYY>` labels across BOTH the Engineering and
    Product Backlog teams (PB items are moved/re-keyed into Engineering, so no double-count).
  - Sub-issues (issues with a parent) are excluded from counts to avoid double-counting points.
  - Estimation is Fibonacci; unestimated tickets contribute 0 points.

Requires LINEAR_API_KEY.

CLI:
  python3 tools/linear.py velocity     --month "May 2026"
  python3 tools/linear.py bucket-split --month "May 2026"
"""

import argparse
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import aiohttp

from metrics.utils import month_bounds

logger = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"
RELEASED_STATE = "Released to Production"
CLASS_LABELS = ("Roadmap", "Adhoc", "Bug")  # precedence order
_MONTH_LABEL_RE = re.compile(r"^[A-Z][a-z]{2,8}\s+\d{4}$")  # e.g. "May 2026"


_api_key_cache: str | None = None


def _load_linear_key_from_integrations() -> str:
    """Fall back to the Fernet-encrypted Linear key in loma_observability.integrations.

    Mirrors how the agent's Linear MCP credentials are loaded — single source
    of truth, no separate env-var setup for the metrics pipeline. Sync read
    via pymongo (one-shot at module-cache miss); returns "" on any failure so
    the caller falls through to the original ValueError.
    """
    enc_key = os.environ.get("OAUTH_ENCRYPTION_KEY", "").strip()
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not enc_key or not uri:
        return ""
    try:
        from cryptography.fernet import Fernet
        from pymongo import MongoClient
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        try:
            doc = client.loma_observability.integrations.find_one(
                {"provider": "linear", "status": "active"},
                {"api_key_encrypted": 1},
            )
        finally:
            client.close()
        if not doc or not doc.get("api_key_encrypted"):
            return ""
        return Fernet(enc_key.encode()).decrypt(doc["api_key_encrypted"].encode()).decode()
    except Exception as e:
        logger.warning("Linear integrations fallback failed (%s); falling through to env-var error",
                       type(e).__name__)
        return ""


def _get_api_key() -> str:
    """Return the Linear API key.

    Priority:
      1. LINEAR_API_KEY env var (legacy + test convenience).
      2. loma_observability.integrations doc where provider="linear",
         decrypted via OAUTH_ENCRYPTION_KEY (same source the agent's MCP uses).

    Cached at module level — first miss triggers a one-shot Mongo read,
    subsequent calls are free.
    """
    global _api_key_cache
    if _api_key_cache:
        return _api_key_cache
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        key = _load_linear_key_from_integrations()
    if not key:
        raise ValueError(
            "LINEAR_API_KEY env var not set, and no usable 'linear' integration "
            "doc found in loma_observability.integrations."
        )
    _api_key_cache = key
    return key


def _headers() -> dict[str, str]:
    # Linear personal API keys go in the Authorization header verbatim (no "Bearer").
    return {"Authorization": _get_api_key(), "Content-Type": "application/json"}


async def _gql(session: aiohttp.ClientSession, query: str, variables: dict | None = None) -> dict[str, Any]:
    body = {"query": query, "variables": variables or {}}
    async with session.post(
        LINEAR_API_URL, headers=_headers(), json=body,
        timeout=aiohttp.ClientTimeout(total=60),
    ) as resp:
        data = await resp.json()
        if "errors" in data:
            raise RuntimeError(f"Linear GraphQL error: {json.dumps(data['errors'])[:400]}")
        return data["data"]


async def _paginate(session, build_query, extract):
    """Run a paginated `issues(...)` query. build_query(after)->str ; extract(data)->connection."""
    out, after = [], None
    while True:
        data = await _gql(session, build_query(after))
        conn = extract(data)
        out.extend(conn["nodes"])
        if conn["pageInfo"]["hasNextPage"]:
            after = conn["pageInfo"]["endCursor"]
        else:
            return out


# --- helpers --------------------------------------------------------------

def classify(labels: list[str]) -> str | None:
    """Class with precedence Roadmap > Adhoc > Bug. None if no class label."""
    for c in CLASS_LABELS:
        if c in labels:
            return c
    return None


def basket_of(labels: list[str]) -> str | None:
    """The `Mon YYYY` basket label, if present."""
    for l in labels:
        if _MONTH_LABEL_RE.match(l):
            return l
    return None


RELEASED_DONE_STATE = "Done"
# Maximum age for the Done-bypass fallback. Tickets that languished in the
# backlog for months and then got closed in a state-hygiene sweep typically
# shipped much earlier — don't credit them to the close month. 60 days
# captures normal sprint cycles (the slowest legitimate ticket Vinay/Sanyam
# audited was a 30-day-old fix) and excludes the May 5 / May 19 / May 21
# bulk-close clusters of 2024-vintage tickets.
DONE_BYPASS_MAX_AGE_DAYS = 60


def _rtp_entry(history_nodes: list[dict], lo: str, hi: str) -> str | None:
    """Earliest 'entered Released to Production' timestamp within [lo, hi), else None."""
    hits = [
        h["createdAt"] for h in history_nodes
        if h.get("toState") and h["toState"]["name"] == RELEASED_STATE
        and lo <= h["createdAt"] < hi
    ]
    return min(hits) if hits else None


def _release_event_at(node: dict, lo: str, hi: str) -> str | None:
    """Return the release-event timestamp for an issue, or None.

    Primary signal: transition INTO `Released to Production` within [lo, hi).
    Fallback (Done-bypass): if there's NO `Released to Production` transition in
    the issue's full history AND the issue went straight to `Done` within
    [lo, hi), count the Done transition — provided the issue is younger than
    `DONE_BYPASS_MAX_AGE_DAYS` (otherwise it's almost certainly a hygiene
    close, not a real release).

    The two-guard policy is intentional. Per the May 2026 audit:
      Guard 1 — "no prior RtP" is the main signal that the ticket bypassed
        the formal release state entirely. Without it we'd double-count
        tickets that were already attributed to an earlier month.
      Guard 2 — the 60-day age cap filters bulk-close hygiene that doesn't
        represent shipping during the current month.
    """
    history = node.get("history", {}).get("nodes", []) or []
    rtp_ts = _rtp_entry(history, lo, hi)
    if rtp_ts:
        return rtp_ts

    # Has the ticket EVER entered Released to Production? If yes (just outside
    # the window), it was already attributed elsewhere — skip.
    has_any_rtp = any(
        (h.get("toState") or {}).get("name") == RELEASED_STATE
        for h in history
    )
    if has_any_rtp:
        return None

    # Find earliest Done transition in the window
    done_hits = [
        h["createdAt"] for h in history
        if (h.get("toState") or {}).get("name") == RELEASED_DONE_STATE
        and lo <= h["createdAt"] < hi
    ]
    if not done_hits:
        return None
    done_ts = min(done_hits)

    # Age guard: only credit if the ticket is < DONE_BYPASS_MAX_AGE_DAYS old
    # at close. This filters out the bulk-close hygiene clusters where tickets
    # 90+ days old are closed en masse without ever shipping in the cycle.
    created = node.get("createdAt")
    if created:
        try:
            from datetime import datetime
            c = datetime.fromisoformat(created.replace("Z", "+00:00"))
            d = datetime.fromisoformat(done_ts.replace("Z", "+00:00"))
            age_days = (d - c).total_seconds() / 86400
            if age_days > DONE_BYPASS_MAX_AGE_DAYS:
                return None
        except (ValueError, TypeError):
            pass  # if we can't parse, fall through and count it
    return done_ts


_ISSUE_FIELDS = """
  identifier estimate createdAt
  state { name } parent { id }
  assignee { name email }
  team { key name }
  labels { nodes { name } }
  history(first: 50) { nodes { createdAt toState { name } } }
"""


# --- public fetchers ------------------------------------------------------

async def fetch_release_events(since_iso: str, until_iso: str) -> list[dict]:
    """Release events: tickets that entered `Released to Production` within [since, until).

    One event per top-level issue, timestamped at the RtP transition. Used for velocity (3423).
    """
    def build(after):
        ac = f', after: "{after}"' if after else ""
        return f"""{{ issues(filter: {{
              state: {{ name: {{ in: ["{RELEASED_STATE}", "Done"] }} }},
              updatedAt: {{ gte: "{since_iso}" }}
            }}, first: 100{ac}) {{
              pageInfo {{ hasNextPage endCursor }}
              nodes {{ {_ISSUE_FIELDS} }}
            }} }}"""

    async with aiohttp.ClientSession() as s:
        nodes = await _paginate(s, build, lambda d: d["issues"])

    events = []
    for n in nodes:
        if n.get("parent"):  # exclude sub-issues
            continue
        released_at = _release_event_at(n, since_iso, until_iso)
        if not released_at:
            continue
        labels = [l["name"] for l in n["labels"]["nodes"]]
        events.append({
            "identifier": n["identifier"],
            "released_at": released_at,
            "points": n.get("estimate") or 0,
            "assignee": (n.get("assignee") or {}).get("name") or "unassigned",
            "team": (n.get("team") or {}).get("key"),
            "class": classify(labels),
            "basket": basket_of(labels),
            "title": None,
        })
    return events


async def fetch_planned_roadmap(month_label: str) -> list[dict]:
    """Planned roadmap items = `Roadmap` + `<month>` across Engineering + Product Backlog.

    Each item flagged shipped if it entered `Released to Production` within the month.
    Used for bucket-split / roadmap adherence (3425).
    """
    lo, hi = month_bounds(month_label)

    def build(after):
        ac = f', after: "{after}"' if after else ""
        return f"""{{ issues(filter: {{
              labels: {{ name: {{ eq: "Roadmap" }} }},
              and: [{{ labels: {{ name: {{ eq: "{month_label}" }} }} }}]
            }}, first: 100{ac}) {{
              pageInfo {{ hasNextPage endCursor }}
              nodes {{ {_ISSUE_FIELDS} }}
            }} }}"""

    async with aiohttp.ClientSession() as s:
        nodes = await _paginate(s, build, lambda d: d["issues"])

    items = []
    for n in nodes:
        if n.get("parent"):
            continue
        shipped_at = _release_event_at(n, lo, hi)
        items.append({
            "identifier": n["identifier"],
            "team": (n.get("team") or {}).get("key"),
            "state": n["state"]["name"],
            "points": n.get("estimate") or 0,
            "shipped": bool(shipped_at) or n["state"]["name"] in (RELEASED_STATE, "Done"),
            "shipped_at": shipped_at,
        })
    return items


# --- CLI ------------------------------------------------------------------

async def _cli():
    p = argparse.ArgumentParser(description="Linear metrics reader (ISSUE-3422)")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("velocity"); v.add_argument("--month", required=True)
    b = sub.add_parser("bucket-split"); b.add_argument("--month", required=True)
    args = p.parse_args()
    lo, hi = month_bounds(args.month)

    if args.cmd == "velocity":
        ev = await fetch_release_events(lo, hi)
        total = sum(e["points"] for e in ev)
        by_class: dict[str, int] = {}
        by_person: dict[str, int] = {}
        for e in ev:
            by_class[e["class"] or "(unlabeled)"] = by_class.get(e["class"] or "(unlabeled)", 0) + e["points"]
            by_person[e["assignee"]] = by_person.get(e["assignee"], 0) + e["points"]
        print(json.dumps({
            "month": args.month, "release_events": len(ev), "total_points": total,
            "points_by_class": by_class,
            "points_by_individual": dict(sorted(by_person.items(), key=lambda x: -x[1])),
        }, indent=2))

    elif args.cmd == "bucket-split":
        items = await fetch_planned_roadmap(args.month)
        planned_n, planned_pts = len(items), sum(i["points"] for i in items)
        shipped = [i for i in items if i["shipped"]]
        print(json.dumps({
            "month": args.month,
            "roadmap_planned_items": planned_n,
            "roadmap_planned_points": planned_pts,
            "roadmap_shipped_items": len(shipped),
            "roadmap_shipped_points": sum(i["points"] for i in shipped),
            "by_team": {
                t: sum(1 for i in items if i["team"] == t)
                for t in sorted({i["team"] for i in items})
            },
            "in_design_not_shipped": [i["identifier"] for i in items if not i["shipped"]],
        }, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_cli())
