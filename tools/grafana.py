"""Grafana alerting and metrics API client.

Provides CLI commands for the Loma agent:
  1. grafana.py alerts list [--state firing|resolved|all]  — List alert instances
  2. grafana.py alerts rules                                — List alert rules (Infra Alerts)
  3. grafana.py alerts silence <consumer-group> [--duration 30m] [--comment C] — Create silence
  4. grafana.py alerts history <consumer-group> [--range 7d] — Firing history with windows
  5. grafana.py query lag <consumer-group> [--range 30m] [--time T] — Query lag trend over time
  6. grafana.py query state <consumer-group>                 — Query consumer group state (0-5)
  7. grafana.py query members <consumer-group>               — Query active member count
  8. grafana.py query all-lag                                — Snapshot of all consumer group lags
  9. grafana.py query synthetics [<instance>] [--range 30]   — Synthetic check success rates & latencies
 10. grafana.py query synthetic-logs <instance> [--range 30] — Failure logs from Loki (errors, HTTP codes)
 11. grafana.py oncall current [--schedule tech-loma]   — Who is on call right now (IRM/OnCall API)
 12. grafana.py oncall next [--schedule tech-loma]       — Who is on call next
 13. grafana.py oncall schedules                             — List OnCall schedules with their API ids

The alerts/query commands require GRAFANA_URL and GRAFANA_API_KEY (Grafana stack, Bearer auth).
The oncall commands require GRAFANA_ONCALL_URL and GRAFANA_ONCALL_TOKEN (IRM/OnCall API, raw token).

Usage (called by the agent via Bash):
  python3 tools/grafana.py alerts list --state firing
  python3 tools/grafana.py alerts history loma-event-track-kafka-consumer --range 7d
  python3 tools/grafana.py query lag loma-push-kafka-consumer-cron --range 30
  python3 tools/grafana.py query lag loma-push-kafka-consumer-cron --range 60 --time 2026-03-04T14:00:00Z
  python3 tools/grafana.py query state loma-push-kafka-consumer-cron
  python3 tools/grafana.py query all-lag
  python3 tools/grafana.py alerts silence loma-cohort-kafka-consumer-cron --duration 30 --comment "transient lag"
  python3 tools/grafana.py query synthetics happy-flow-studies --range 30
  python3 tools/grafana.py query synthetics
"""

import asyncio
import json
import os
import sys
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger(__name__)

PROMETHEUS_DATASOURCE_UID = "grafanacloud-prom"


def _get_url() -> str:
    url = os.environ.get("GRAFANA_URL", "")
    if not url:
        raise ValueError(
            "GRAFANA_URL environment variable is not set. "
            "Please configure it before using Grafana tools."
        )
    return url.rstrip("/")


def _get_api_key() -> str:
    key = os.environ.get("GRAFANA_API_KEY", "")
    if not key:
        raise ValueError(
            "GRAFANA_API_KEY environment variable is not set. "
            "Please configure it before using Grafana tools."
        )
    return key


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# Grafana IRM / OnCall API (separate host + token from the Grafana stack)
# ---------------------------------------------------------------------------
#
# IRM/OnCall is a fully separate system from the Grafana stack:
#   - It has its own host (e.g. https://oncall-prod-us-central-0.grafana.net/oncall)
#   - It authenticates with a RAW token, NOT "Bearer <token>" (common gotcha)
# These helpers are intentionally isolated from _get_url/_get_api_key/_headers so
# that the existing alerts/query commands (which use GRAFANA_URL + GRAFANA_API_KEY
# with Bearer auth) are completely unaffected.


def _get_oncall_url() -> str:
    url = os.environ.get("GRAFANA_ONCALL_URL", "")
    if not url:
        raise ValueError(
            "GRAFANA_ONCALL_URL environment variable is not set. "
            "Set it to the OnCall API URL (e.g. "
            "https://oncall-prod-us-central-0.grafana.net/oncall)."
        )
    return url.rstrip("/")


def _get_oncall_token() -> str:
    token = os.environ.get("GRAFANA_ONCALL_TOKEN", "")
    if not token:
        raise ValueError(
            "GRAFANA_ONCALL_TOKEN environment variable is not set. "
            "Mint a service-account token on the lomatech Grafana org "
            "with IRM/OnCall read access and set it here."
        )
    return token


def _oncall_headers() -> Dict[str, str]:
    # OnCall API expects the raw token in Authorization, NOT "Bearer <token>".
    return {
        "Authorization": _get_oncall_token(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _oncall_api_get(
    path: str, params: Optional[Dict[str, str]] = None
) -> Union[Dict[str, Any], List[Any]]:
    """GET helper for the OnCall API. Returns parsed JSON or {"error": "..."}."""
    url = f"{_get_oncall_url()}{path}"
    if params:
        url += "?" + urlencode(params)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=_oncall_headers(), timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 401:
                    return {"error": "OnCall API token is invalid or expired."}
                if resp.status == 403:
                    return {
                        "error": "OnCall API token lacks permission. "
                        "Some endpoints require a user-specific OnCall API token "
                        "instead of a service-account token."
                    }
                if resp.status == 404:
                    return {"error": f"Not found: {path}"}
                if resp.status == 429:
                    return {"error": "OnCall API rate limit reached. Try again shortly."}
                if resp.status >= 400:
                    text = await resp.text()
                    return {"error": f"OnCall API error (HTTP {resp.status}): {text[:500]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to OnCall API: {e}"}


async def _api_get(path: str, params: Optional[Dict[str, str]] = None) -> Union[Dict[str, Any], List[Any]]:
    """Shared GET helper. Returns parsed JSON or {"error": "..."}."""
    url = f"{_get_url()}{path}"
    if params:
        url += "?" + urlencode(params)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 401:
                    return {"error": "Grafana API key is invalid or expired."}
                if resp.status == 403:
                    return {"error": "Grafana API key lacks permission for this operation."}
                if resp.status == 404:
                    return {"error": f"Not found: {path}"}
                if resp.status == 429:
                    return {"error": "Grafana rate limit reached. Try again shortly."}
                if resp.status >= 400:
                    text = await resp.text()
                    return {"error": f"Grafana API error (HTTP {resp.status}): {text[:500]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to Grafana API: {e}"}


async def _api_post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Shared POST helper. Returns parsed JSON or {"error": "..."}."""
    url = f"{_get_url()}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=_headers(), json=body, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 401:
                    return {"error": "Grafana API key is invalid or expired."}
                if resp.status == 403:
                    return {"error": "Grafana API key lacks permission for this operation."}
                if resp.status == 429:
                    return {"error": "Grafana rate limit reached. Try again shortly."}
                if resp.status not in (200, 201, 202):
                    text = await resp.text()
                    return {"error": f"Grafana API error (HTTP {resp.status}): {text[:500]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to Grafana API: {e}"}


async def _api_delete(path: str) -> Dict[str, Any]:
    """Shared DELETE helper. Returns parsed JSON or {"error": "..."}."""
    url = f"{_get_url()}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 401:
                    return {"error": "Grafana API key is invalid or expired."}
                if resp.status >= 400:
                    text = await resp.text()
                    return {"error": f"Grafana API error (HTTP {resp.status}): {text[:500]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to Grafana API: {e}"}


# ---------------------------------------------------------------------------
# Prometheus query helpers
# ---------------------------------------------------------------------------


async def _prom_query(query: str) -> Dict[str, Any]:
    """Run an instant Prometheus query via Grafana datasource proxy."""
    now = int(datetime.now(timezone.utc).timestamp())
    path = f"/api/datasources/proxy/uid/{PROMETHEUS_DATASOURCE_UID}/api/v1/query"
    params = {"query": query, "time": str(now)}
    return await _api_get(path, params)


async def _prom_query_range(
    query: str,
    range_minutes: int = 30,
    step: int = 60,
    at_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run a range Prometheus query via Grafana datasource proxy.

    Args:
        at_time: If provided, center the query window around this timestamp
                 instead of now. The window spans [at_time - range, at_time + range].
    """
    if at_time:
        center = int(at_time.timestamp())
        start = center - (range_minutes * 60)
        end = center + (range_minutes * 60)
    else:
        end = int(datetime.now(timezone.utc).timestamp())
        start = end - (range_minutes * 60)
    path = f"/api/datasources/proxy/uid/{PROMETHEUS_DATASOURCE_UID}/api/v1/query_range"
    params = {
        "query": query,
        "start": str(start),
        "end": str(end),
        "step": str(step),
    }
    return await _api_get(path, params)


# ---------------------------------------------------------------------------
# Public async functions
# ---------------------------------------------------------------------------


async def list_alerts(state: str = "all") -> Union[Dict[str, Any], List[Any]]:
    """List alert instances from Grafana Alertmanager.

    Args:
        state: "firing", "resolved", or "all"
    """
    params = {}
    if state == "firing":
        params["active"] = "true"
        params["silenced"] = "false"
        params["inhibited"] = "false"
    elif state == "resolved":
        params["active"] = "false"

    result = await _api_get("/api/alertmanager/grafana/api/v2/alerts", params)

    if isinstance(result, dict) and "error" in result:
        return result

    alerts = result if isinstance(result, list) else []

    summaries = []
    for a in alerts:
        labels = a.get("labels", {})
        annotations = a.get("annotations", {})
        status = a.get("status", {})
        alert_info = {
            "alertname": labels.get("alertname", ""),
            "state": status.get("state", ""),
            "consumer_group": labels.get("consumer_group", labels.get("consumer_name", "")),
            "severity": labels.get("severity", ""),
            "folder": labels.get("grafana_folder", ""),
            "starts_at": a.get("startsAt", ""),
            "ends_at": a.get("endsAt", ""),
            "labels": labels,
        }
        # Include annotations if present (summary, dashboard_url, description, etc.)
        if annotations:
            alert_info["annotations"] = annotations
        summaries.append(alert_info)

    return {"count": len(summaries), "alerts": summaries}


async def list_alert_rules() -> Dict[str, Any]:
    """List alert rules from Infra Alerts folder."""
    result = await _api_get(
        "/api/prometheus/grafana/api/v1/rules",
        {"limit_alerts": "3", "group_limit": "40"},
    )

    if isinstance(result, dict) and "error" in result:
        return result

    groups = result.get("data", {}).get("groups", []) if isinstance(result, dict) else []

    rules = []
    for g in groups:
        folder = g.get("file", "")
        for r in g.get("rules", []):
            rule_info = {
                "name": r.get("name", ""),
                "state": r.get("state", ""),
                "folder": folder,
                "severity": r.get("labels", {}).get("severity", ""),
                "type": r.get("labels", {}).get("type", ""),
            }
            # Include active alert instances if any
            active_alerts = [
                a for a in r.get("alerts", [])
                if a.get("state") in ("firing", "pending", "Firing", "Pending")
            ]
            if active_alerts:
                rule_info["active_alerts"] = [
                    {
                        "state": a.get("state"),
                        "consumer": a.get("labels", {}).get(
                            "consumer_group",
                            a.get("labels", {}).get("consumer_name", ""),
                        ),
                        "value": a.get("value", ""),
                    }
                    for a in active_alerts
                ]
            rules.append(rule_info)

    return {"count": len(rules), "rules": rules}


async def create_silence(
    consumer_group: str,
    duration_minutes: int = 30,
    comment: str = "Auto-triage: transient lag",
) -> Dict[str, Any]:
    """Create a silence for a specific consumer group's alerts.

    Silences all alerts matching the consumer_group label.
    """
    now = datetime.now(timezone.utc)
    ends_at = now + timedelta(minutes=duration_minutes)

    body = {
        "matchers": [
            {
                "name": "consumer_group",
                "value": consumer_group,
                "isRegex": False,
                "isEqual": True,
            }
        ],
        "startsAt": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "endsAt": ends_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "createdBy": "loma-agent",
        "comment": comment,
    }

    return await _api_post("/api/alertmanager/grafana/api/v2/silences", body)


async def alert_history(
    consumer_group: str,
    range_days: int = 7,
) -> Dict[str, Any]:
    """Query the firing history for a consumer group's alerts.

    Uses the GRAFANA_ALERTS metric to find when alerts fired and resolved.
    Returns firing windows with start/end times and durations.
    """
    # GRAFANA_ALERTS uses consumer_group for lag alerts and consumer_name for state/member alerts
    query = (
        f'GRAFANA_ALERTS{{consumer_group="{consumer_group}"}}'
        f' or GRAFANA_ALERTS{{consumer_name="{consumer_group}"}}'
    )
    range_minutes = range_days * 24 * 60
    # Use 5-minute steps for reasonable resolution
    step = 300
    result = await _prom_query_range(query, range_minutes, step=step)

    if isinstance(result, dict) and "error" in result:
        return result

    series = result.get("data", {}).get("result", []) if isinstance(result, dict) else []

    if not series:
        return {
            "consumer_group": consumer_group,
            "range_days": range_days,
            "status": "no_data",
            "message": f"No alert history found for '{consumer_group}' in the last {range_days} days",
        }

    alerts = []
    for s in series:
        metric = s.get("metric", {})
        alertname = metric.get("alertname", "unknown")
        alertstate = metric.get("alertstate", "unknown")
        values = s.get("values", [])

        # Find firing windows (transitions)
        windows = []  # type: List[Dict[str, Any]]
        window_start = None  # type: Optional[float]
        prev_val = 0.0

        for v in values:
            ts, val = v[0], float(v[1])
            if val > 0 and prev_val == 0:
                window_start = ts
            elif val == 0 and prev_val > 0 and window_start is not None:
                duration_min = int((ts - window_start) / 60)
                windows.append({
                    "started": datetime.fromtimestamp(window_start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "resolved": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duration_minutes": duration_min,
                })
                window_start = None
            prev_val = val

        # Handle still-firing window
        if window_start is not None:
            duration_min = int((values[-1][0] - window_start) / 60)
            windows.append({
                "started": datetime.fromtimestamp(window_start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "resolved": None,
                "status": "still_firing",
                "duration_minutes": duration_min,
            })

        if windows:
            alerts.append({
                "alertname": alertname,
                "alertstate": alertstate,
                "rule_uid": metric.get("grafana_rule_uid", ""),
                "firing_windows": windows,
                "total_windows": len(windows),
            })

    return {
        "consumer_group": consumer_group,
        "range_days": range_days,
        "alerts": alerts,
        "total_alert_types": len(alerts),
    }


async def query_lag(
    consumer_group: str,
    range_minutes: int = 30,
    at_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Query kafka_consumer_lag trend for a consumer group over the given range.

    Args:
        at_time: If provided, query lag around this historical timestamp
                 instead of the current time. Window: [at_time - range, at_time + range].

    Returns timestamped lag values for trend analysis.
    """
    query = f'kafka_consumer_lag{{consumer_group="{consumer_group}"}}'
    result = await _prom_query_range(query, range_minutes, at_time=at_time)

    if isinstance(result, dict) and "error" in result:
        return result

    series = result.get("data", {}).get("result", []) if isinstance(result, dict) else []

    time_context = "current"
    if at_time:
        time_context = at_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    if not series:
        return {
            "consumer_group": consumer_group,
            "range_minutes": range_minutes,
            "time_context": time_context,
            "status": "no_data",
            "message": f"No lag data found for consumer group '{consumer_group}'",
        }

    # Aggregate across environments (take max lag per timestamp)
    all_values = []
    for s in series:
        env = s.get("metric", {}).get("service_environment", "unknown")
        for ts, val in s.get("values", []):
            all_values.append((ts, int(val), env))

    # Deduplicate by timestamp, keeping max lag
    ts_map: Dict[float, Tuple[int, str]] = {}
    for ts, val, env in all_values:
        if ts not in ts_map or val > ts_map[ts][0]:
            ts_map[ts] = (val, env)

    sorted_points = sorted(ts_map.items())
    lags = [v[0] for _, v in sorted_points]

    if not lags:
        return {
            "consumer_group": consumer_group,
            "range_minutes": range_minutes,
            "status": "no_data",
        }

    first_lag = lags[0]
    last_lag = lags[-1]
    max_lag = max(lags)
    min_lag = min(lags)
    avg_lag = sum(lags) // len(lags)

    # Determine trend
    if len(lags) >= 3:
        recent_third = lags[-(len(lags) // 3):]
        early_third = lags[:len(lags) // 3]
        recent_avg = sum(recent_third) / len(recent_third)
        early_avg = sum(early_third) / len(early_third)

        if recent_avg < early_avg * 0.7:
            trend = "decreasing"
        elif recent_avg > early_avg * 1.3:
            trend = "increasing"
        else:
            trend = "stable"
    else:
        trend = "insufficient_data"

    return {
        "consumer_group": consumer_group,
        "range_minutes": range_minutes,
        "time_context": time_context,
        "datapoints": len(lags),
        "current_lag": last_lag,
        "first_lag": first_lag,
        "max_lag": max_lag,
        "min_lag": min_lag,
        "avg_lag": avg_lag,
        "trend": trend,
        "trend_detail": f"first={first_lag:,} → current={last_lag:,} (max={max_lag:,}, avg={avg_lag:,})",
        "values": [
            {"timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M"), "lag": v[0]}
            for ts, v in sorted_points[-10:]  # Last 10 datapoints for quick view
        ],
    }


async def query_state(consumer_group: str) -> Dict[str, Any]:
    """Query the current state of a Kafka consumer group.

    States: 0=Unknown, 1=Empty, 2=Dead, 3=PreparingRebalance, 4=CompletingRebalance, 5=Stable
    """
    STATE_NAMES = {
        0: "Unknown",
        1: "Empty",
        2: "Dead",
        3: "PreparingRebalance",
        4: "CompletingRebalance",
        5: "Stable",
    }

    query = f'kafka_consumer_state{{consumer_name="{consumer_group}"}}'
    result = await _prom_query(query)

    if isinstance(result, dict) and "error" in result:
        return result

    series = result.get("data", {}).get("result", []) if isinstance(result, dict) else []

    if not series:
        return {
            "consumer_group": consumer_group,
            "status": "no_data",
            "message": f"No state data found for '{consumer_group}'",
        }

    # Take the latest value (max across environments)
    state_val = max(int(s.get("value", [0, "0"])[1]) for s in series)

    return {
        "consumer_group": consumer_group,
        "state_code": state_val,
        "state_name": STATE_NAMES.get(state_val, f"Unknown({state_val})"),
        "is_healthy": state_val == 5,
    }


async def query_members(consumer_group: str) -> Dict[str, Any]:
    """Query the active member count of a Kafka consumer group."""
    query = f'kafka_consumer_member_count{{consumer_name="{consumer_group}"}}'
    result = await _prom_query(query)

    if isinstance(result, dict) and "error" in result:
        return result

    series = result.get("data", {}).get("result", []) if isinstance(result, dict) else []

    if not series:
        return {
            "consumer_group": consumer_group,
            "status": "no_data",
            "message": f"No member count data found for '{consumer_group}'",
        }

    # Take max across environments
    member_count = max(int(s.get("value", [0, "0"])[1]) for s in series)

    return {
        "consumer_group": consumer_group,
        "member_count": member_count,
        "has_members": member_count > 0,
    }


async def query_all_lag() -> Dict[str, Any]:
    """Get a snapshot of lag for ALL consumer groups, sorted by lag descending."""
    result = await _prom_query("kafka_consumer_lag")

    if isinstance(result, dict) and "error" in result:
        return result

    series = result.get("data", {}).get("result", []) if isinstance(result, dict) else []

    # Deduplicate by consumer_group, keeping max lag
    groups: Dict[str, int] = {}
    for s in series:
        cg = s.get("metric", {}).get("consumer_group", "unknown")
        lag = int(s.get("value", [0, "0"])[1])
        if cg not in groups or lag > groups[cg]:
            groups[cg] = lag

    sorted_groups = sorted(groups.items(), key=lambda x: -x[1])

    return {
        "count": len(sorted_groups),
        "consumer_groups": [
            {"consumer_group": cg, "lag": lag}
            for cg, lag in sorted_groups
        ],
    }


# ---------------------------------------------------------------------------
# Synthetic monitoring
# ---------------------------------------------------------------------------


async def query_synthetics(instance: Optional[str] = None, range_minutes: int = 30) -> Dict[str, Any]:
    """Query synthetic check success rates and latencies.

    If instance is provided, returns detailed metrics for that check.
    If instance is None, returns a summary of all checks.
    """
    if instance:
        return await _query_synthetic_detail(instance, range_minutes)
    return await _query_synthetic_summary()


async def _query_synthetic_summary() -> Dict[str, Any]:
    """Get current status of all synthetic checks."""
    # Get check info (lists all registered checks)
    info_result = await _prom_query('sm_check_info')
    if isinstance(info_result, dict) and "error" in info_result:
        return info_result

    info_series = info_result.get("data", {}).get("result", []) if isinstance(info_result, dict) else []

    # Get latest probe_success for each check
    success_result = await _prom_query(
        'avg by (instance, job) (probe_success)'
    )
    success_series = success_result.get("data", {}).get("result", []) if isinstance(success_result, dict) else []

    # Build success map: instance -> avg success rate
    success_map: Dict[str, float] = {}
    for s in success_series:
        inst = s.get("metric", {}).get("instance", "")
        val = float(s.get("value", [0, "0"])[1])
        success_map[inst] = val

    # Get latest probe duration
    duration_result = await _prom_query(
        'avg by (instance, job) (probe_duration_seconds)'
    )
    duration_series = duration_result.get("data", {}).get("result", []) if isinstance(duration_result, dict) else []

    duration_map: Dict[str, float] = {}
    for s in duration_series:
        inst = s.get("metric", {}).get("instance", "")
        val = float(s.get("value", [0, "0"])[1])
        duration_map[inst] = val

    checks = []
    for s in info_series:
        metric = s.get("metric", {})
        inst = metric.get("instance", "")
        job = metric.get("job", "")
        check_type = metric.get("check_name", "")
        frequency = metric.get("frequency", "")

        success_rate = success_map.get(inst)
        duration = duration_map.get(inst)

        check_info = {
            "instance": inst,
            "job": job,
            "check_type": check_type,
            "frequency_ms": frequency,
            "success_rate": round(success_rate, 4) if success_rate is not None else None,
            "status": "healthy" if success_rate is not None and success_rate >= 1.0 else
                      "degraded" if success_rate is not None and success_rate >= 0.5 else
                      "failing" if success_rate is not None else "unknown",
            "latency_seconds": round(duration, 3) if duration is not None else None,
        }
        checks.append(check_info)

    # Sort: failing first, then degraded, then healthy
    status_order = {"failing": 0, "degraded": 1, "unknown": 2, "healthy": 3}
    checks.sort(key=lambda c: status_order.get(c["status"], 4))

    return {
        "count": len(checks),
        "checks": checks,
    }


async def _query_synthetic_detail(instance: str, range_minutes: int = 30) -> Dict[str, Any]:
    """Get detailed metrics for a specific synthetic check over time."""
    # Success rate over time
    success_query = f'probe_success{{instance="{instance}"}}'
    success_result = await _prom_query_range(success_query, range_minutes, step=60)

    if isinstance(success_result, dict) and "error" in success_result:
        return success_result

    success_series = success_result.get("data", {}).get("result", []) if isinstance(success_result, dict) else []

    # Duration over time
    duration_query = f'probe_duration_seconds{{instance="{instance}"}}'
    duration_result = await _prom_query_range(duration_query, range_minutes, step=60)
    duration_series = duration_result.get("data", {}).get("result", []) if isinstance(duration_result, dict) else []

    # HTTP status code (for HTTP checks)
    http_status_query = f'probe_http_status_code{{instance="{instance}"}}'
    http_result = await _prom_query_range(http_status_query, range_minutes, step=60)
    http_series = http_result.get("data", {}).get("result", []) if isinstance(http_result, dict) else []

    if not success_series:
        return {
            "instance": instance,
            "range_minutes": range_minutes,
            "status": "no_data",
            "message": f"No synthetic check data found for instance '{instance}'",
        }

    # Aggregate success values across probes
    success_values = []
    for s in success_series:
        probe = s.get("metric", {}).get("probe", "unknown")
        for ts, val in s.get("values", []):
            success_values.append((ts, float(val), probe))

    # Per-timestamp: average success across probes
    ts_success: Dict[float, List[float]] = {}
    for ts, val, _ in success_values:
        ts_success.setdefault(ts, []).append(val)

    sorted_ts = sorted(ts_success.keys())
    avg_success = [sum(ts_success[ts]) / len(ts_success[ts]) for ts in sorted_ts]

    total_checks = len(avg_success)
    failed_checks = sum(1 for v in avg_success if v < 1.0)
    success_rate = sum(avg_success) / len(avg_success) if avg_success else 0

    # Duration stats
    all_durations = []
    for s in duration_series:
        for _, val in s.get("values", []):
            all_durations.append(float(val))

    # HTTP status codes (unique non-200 codes)
    error_codes = set()
    for s in http_series:
        for _, val in s.get("values", []):
            code = int(float(val))
            if code != 200 and code != 0:
                error_codes.add(code)

    # Identify probes involved
    probes = list({s.get("metric", {}).get("probe", "unknown") for s in success_series})

    # Get job name from first series
    job = success_series[0].get("metric", {}).get("job", instance) if success_series else instance

    result: Dict[str, Any] = {
        "instance": instance,
        "job": job,
        "range_minutes": range_minutes,
        "probes": probes,
        "total_executions": total_checks,
        "failed_executions": failed_checks,
        "success_rate": round(success_rate, 4),
        "status": "healthy" if success_rate >= 1.0 else
                  "degraded" if success_rate >= 0.8 else "failing",
    }

    if all_durations:
        result["latency"] = {
            "current_seconds": round(all_durations[-1], 3),
            "avg_seconds": round(sum(all_durations) / len(all_durations), 3),
            "max_seconds": round(max(all_durations), 3),
            "min_seconds": round(min(all_durations), 3),
        }

    if error_codes:
        result["http_error_codes"] = sorted(error_codes)

    # Recent failures timeline (last 5 failures)
    recent_failures = []
    for ts in sorted_ts:
        avg = sum(ts_success[ts]) / len(ts_success[ts])
        if avg < 1.0:
            recent_failures.append({
                "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M"),
                "success_rate": round(avg, 2),
            })
    if recent_failures:
        result["recent_failures"] = recent_failures[-5:]

    return result


LOKI_DATASOURCE_UID = "grafanacloud-logs"


def _parse_timestamp(value: str) -> int:
    """Parse a timestamp string to epoch seconds.

    Accepts:
    - ISO format: "2026-03-06T09:19:00" (treated as UTC)
    - ISO with timezone: "2026-03-06T09:19:00+05:30"
    - Epoch seconds: "1741234740"
    """
    try:
        return int(value)
    except ValueError:
        pass
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


async def query_synthetic_logs(
    instance: str,
    range_minutes: int = 30,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch failure logs from Loki for a specific synthetic check.

    Queries the Loki logs datasource for failed probe executions,
    returning the error messages, HTTP status codes, and request details.

    Time window can be specified as:
    - Relative: --range N (last N minutes from now)
    - Absolute: --start and --end (ISO timestamps or epoch seconds)
    - Mixed: --start with --range (N minutes after start)
    """
    if start_time:
        start_epoch = _parse_timestamp(start_time)
        if end_time:
            end_epoch = _parse_timestamp(end_time)
        else:
            end_epoch = start_epoch + range_minutes * 60
        range_desc = f"{datetime.fromtimestamp(start_epoch, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} to {datetime.fromtimestamp(end_epoch, tz=timezone.utc).strftime('%H:%M UTC')}"
    else:
        now = int(datetime.now(timezone.utc).timestamp())
        start_epoch = now - range_minutes * 60
        end_epoch = now
        range_desc = f"last {range_minutes} minutes"

    start_ns = str(start_epoch * 1000000000)
    end_ns = str(end_epoch * 1000000000)

    query = f'{{instance="{instance}", probe_success="0"}}'
    path = f"/api/datasources/proxy/uid/{LOKI_DATASOURCE_UID}/loki/api/v1/query_range"
    params = {
        "query": query,
        "start": start_ns,
        "end": end_ns,
        "limit": "50",
    }
    result = await _api_get(path, params)

    if isinstance(result, dict) and "error" in result:
        return result

    streams = result.get("data", {}).get("result", []) if isinstance(result, dict) else []

    if not streams:
        return {
            "instance": instance,
            "time_window": range_desc,
            "status": "no_failures",
            "message": f"No failure logs found for '{instance}' in {range_desc}",
        }

    # Parse log entries
    errors = []
    requests = []
    for stream in streams:
        level = stream.get("stream", {}).get("detected_level", "")
        for ts_ns, line in stream.get("values", []):
            ts = datetime.fromtimestamp(int(ts_ns) / 1e9, tz=timezone.utc)
            ts_str = ts.strftime("%H:%M:%S UTC")

            if "test aborted" in line or "Check failed" in line:
                # Extract error details
                entry: Dict[str, Any] = {"timestamp": ts_str, "type": "error"}
                if "expected=" in line and "received=" in line:
                    # Parse assertion: expected=200 received=502
                    parts = line.split()
                    for p in parts:
                        if p.startswith("expected="):
                            entry["expected_status"] = p.split("=")[1]
                        elif p.startswith("received="):
                            entry["received_status"] = p.split("=")[1]
                        elif p.startswith("at="):
                            entry["script_location"] = p.split("=")[1]
                    entry["message"] = line.split("msg=")[-1].strip('" \n') if "msg=" in line else line.strip()
                else:
                    entry["message"] = line.split("msg=")[-1].strip('" \n') if "msg=" in line else line.strip()
                errors.append(entry)

            elif 'msg="Request:' in line and ("POST" in line or "GET" in line):
                # Extract the HTTP request details (e.g., "POST /sdk/campaign/trigger HTTP/1.1")
                msg_start = line.find('msg="Request:') + 13
                if msg_start > 12:
                    req_text = line[msg_start:].strip().strip('"')
                    # Get first meaningful line with the method + path
                    for req_line in req_text.replace("\\n", "\n").split("\n"):
                        req_line = req_line.strip()
                        if req_line.startswith(("POST ", "GET ", "PUT ", "DELETE ", "PATCH ")):
                            requests.append({"timestamp": ts_str, "endpoint": req_line})
                            break

            elif "got a response:" in line and level == "info":
                # Extract response status from console logs
                if "502" in line or "500" in line or "503" in line or "504" in line:
                    msg = line.split('msg="')[-1].rstrip('"') if 'msg="' in line else line
                    errors.append({"timestamp": ts_str, "type": "http_error", "message": msg.strip('" \n')})

    return {
        "instance": instance,
        "time_window": range_desc,
        "failure_count": len(errors),
        "errors": errors,
        "failed_requests": requests,
    }


# ---------------------------------------------------------------------------
# OnCall: who is on call
# ---------------------------------------------------------------------------
#
# Endpoints (OnCall HTTP API v1):
#   GET /api/v1/schedules                          -> list schedules; each carries
#                                                     on_call_now[] (ids on call NOW)
#   GET /api/v1/users/{id}                          -> resolve id -> name + Slack id
#   GET /api/v1/schedules/{id}/final_shifts        -> resolved shift windows (next shift)
# "current" reads on_call_now[] directly (computed by Grafana, no date math) then
# resolves each id to a name + Slack mention. "next" uses final_shifts to find the
# upcoming shift. Both replace scraping the weekly #alerts Slack shift-update post.


def _parse_iso(value: str) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp from the OnCall API into an aware datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def oncall_schedules() -> Dict[str, Any]:
    """List all OnCall schedules with their API ids."""
    result = await _oncall_api_get("/api/v1/schedules")
    if isinstance(result, dict) and "error" in result:
        return result
    items = result.get("results", []) if isinstance(result, dict) else []
    schedules = [
        {"id": s.get("id"), "name": s.get("name"), "type": s.get("type")}
        for s in items
    ]
    return {"count": len(schedules), "schedules": schedules}


async def _resolve_schedule(schedule: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Map a schedule name (or id) to its full OnCall schedule object.

    Returns (schedule_obj, error_dict). Exactly one is non-None. The schedule
    object includes ``on_call_now`` (list of user ids computed by Grafana), so
    callers can determine who is on call right now without any date-window math.
    """
    listing = await _oncall_api_get("/api/v1/schedules")
    if isinstance(listing, dict) and "error" in listing:
        return None, listing
    items = listing.get("results", []) if isinstance(listing, dict) else []

    # Exact id match first, then exact name (case-insensitive), then substring.
    for s in items:
        if s.get("id") == schedule:
            return s, None
    for s in items:
        if (s.get("name") or "").lower() == schedule.lower():
            return s, None
    for s in items:
        if schedule.lower() in (s.get("name") or "").lower():
            return s, None

    available = [s.get("name") for s in items]
    return None, {
        "error": f"No schedule matching '{schedule}' found.",
        "available_schedules": available,
    }


async def _resolve_user(user_id: str) -> Dict[str, Any]:
    """Resolve an OnCall user id to a compact identity (name + Slack mention).

    GET /api/v1/users/{id} returns the user object with username, email and a
    ``slack`` block. We surface the Slack user id as ``slack_mention`` so callers
    can ping the on-call engineer directly.
    """
    user = await _oncall_api_get(f"/api/v1/users/{user_id}")
    if isinstance(user, dict) and "error" in user:
        # Degrade gracefully: still return the raw id so the caller is not blind.
        # Keep a consistent shape (slack_mention present) so downstream consumers
        # can safely read it without guarding for the error branch.
        return {
            "oncall_user_id": user_id,
            "oncall_name": None,
            "email": None,
            "slack_user_id": None,
            "slack_mention": None,
            "error": user["error"],
        }
    slack = user.get("slack") or {}
    slack_user_id = slack.get("user_id")
    return {
        "oncall_user_id": user_id,
        "oncall_name": user.get("username"),
        "email": user.get("email"),
        "slack_user_id": slack_user_id,
        "slack_mention": f"<@{slack_user_id}>" if slack_user_id else None,
        "timezone": user.get("timezone"),
    }


async def _final_shifts(
    schedule_id: str, start: datetime, end: datetime
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Fetch resolved (final) shifts for a schedule between start and end (UTC)."""
    params = {
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
    }
    result = await _oncall_api_get(
        f"/api/v1/schedules/{schedule_id}/final_shifts", params
    )
    if isinstance(result, dict) and "error" in result:
        return result
    if isinstance(result, dict):
        return result.get("results", [])
    return result if isinstance(result, list) else []


def _extract_shift_user_ids(shift: Dict[str, Any]) -> List[str]:
    """Pull OnCall user ids out of a final_shift entry.

    The documented ``final_shifts`` response exposes the assigned user via a
    single ``user_pk`` id string. We read that first, falling back to a
    ``users`` array (items may be plain id strings or objects carrying a
    ``pk``/``id``) for forward/backward compatibility. All shapes are
    normalised to a list of id strings resolvable via /api/v1/users/{id}.
    """
    ids: List[str] = []
    user_pk = shift.get("user_pk")
    if user_pk:
        ids.append(user_pk)
    for u in shift.get("users", []) or []:
        if isinstance(u, str):
            ids.append(u)
        elif isinstance(u, dict):
            uid = u.get("pk") or u.get("id")
            if uid:
                ids.append(uid)
    return ids


def _shift_start_end(shift: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Return (shift_start, shift_end) raw ISO strings from a final_shift entry.

    The documented field names are ``shift_start``/``shift_end``; we keep
    ``start``/``end`` as a fallback in case the API shape differs.
    """
    start = shift.get("shift_start") or shift.get("start")
    end = shift.get("shift_end") or shift.get("end")
    return start, end


async def oncall_current(schedule: str = "tech-loma") -> Dict[str, Any]:
    """Return who is on call RIGHT NOW for the given schedule.

    Uses the validated 2-call path:
      1. GET /api/v1/schedules  -> read ``on_call_now`` (ids Grafana already
         computed for the current moment; no date-window math).
      2. GET /api/v1/users/{id} -> resolve each id to name + Slack mention.
    """
    sched, err = await _resolve_schedule(schedule)
    if err:
        return err

    now = datetime.now(timezone.utc)
    schedule_id = sched.get("id")
    on_call_now = sched.get("on_call_now") or []

    if not on_call_now:
        return {
            "schedule": schedule,
            "schedule_id": schedule_id,
            "status": "no_active_shift",
            "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "message": f"No active on-call shift found for '{schedule}' right now.",
        }

    oncall = list(await asyncio.gather(*[_resolve_user(uid) for uid in on_call_now]))

    # Enrich each on-call entry with its current shift window (start/end) so
    # callers can answer "on call until when". on_call_now itself carries no
    # timing, so we look it up from final_shifts. This is best-effort: if the
    # lookup fails we still return who is on call.
    shifts = await _final_shifts(schedule_id, now - timedelta(days=1), now + timedelta(days=1))
    if isinstance(shifts, list):
        window_by_uid: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        for s in shifts:
            raw_start, raw_end = _shift_start_end(s)
            start = _parse_iso(raw_start or "")
            end = _parse_iso(raw_end or "")
            if start and end and start <= now < end:
                for uid in _extract_shift_user_ids(s):
                    window_by_uid[uid] = (raw_start, raw_end)
        for entry in oncall:
            uid = entry.get("oncall_user_id")
            if uid in window_by_uid:
                entry["shift_start"], entry["shift_end"] = window_by_uid[uid]

    return {
        "schedule": schedule,
        "schedule_id": schedule_id,
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "oncall": oncall,
    }


async def oncall_next(schedule: str = "tech-loma") -> Dict[str, Any]:
    """Return who is on call NEXT (the upcoming shift after the current one).

    ``on_call_now`` only covers the present, so the next rotation is resolved
    from /final_shifts: find the earliest shift that starts after now, then
    resolve its user ids to names + Slack mentions via /api/v1/users/{id}.
    """
    sched, err = await _resolve_schedule(schedule)
    if err:
        return err

    schedule_id = sched.get("id")
    now = datetime.now(timezone.utc)
    # Look ahead up to ~30 days to find the next distinct shift.
    shifts = await _final_shifts(schedule_id, now - timedelta(days=1), now + timedelta(days=30))
    if isinstance(shifts, dict) and "error" in shifts:
        return shifts

    upcoming = []
    for s in shifts:
        raw_start, _ = _shift_start_end(s)
        start = _parse_iso(raw_start or "")
        if start and start > now:
            upcoming.append((start, s))

    if not upcoming:
        return {
            "schedule": schedule,
            "schedule_id": schedule_id,
            "status": "no_upcoming_shift",
            "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "message": f"No upcoming on-call shift found for '{schedule}'.",
        }

    upcoming.sort(key=lambda x: x[0])
    earliest_start = upcoming[0][0]

    # Resolve users for all shifts that start at the earliest upcoming boundary.
    # Collect (uid, shift) pairs first, then resolve users concurrently.
    pending = []
    seen_ids = set()
    for st, s in upcoming:
        if st != earliest_start:
            continue
        for uid in _extract_shift_user_ids(s):
            if uid in seen_ids:
                continue
            seen_ids.add(uid)
            pending.append((uid, s))

    resolved = await asyncio.gather(*[_resolve_user(uid) for uid, _ in pending])
    nxt = []
    for (uid, s), entry in zip(pending, resolved):
        raw_start, raw_end = _shift_start_end(s)
        entry["shift_start"] = raw_start
        entry["shift_end"] = raw_end
        nxt.append(entry)

    return {
        "schedule": schedule,
        "schedule_id": schedule_id,
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "next": nxt,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_usage():
    print("Usage:")
    print("  python3 tools/grafana.py alerts list [--state firing|resolved|all]")
    print("    List current alert instances")
    print()
    print("  python3 tools/grafana.py alerts rules")
    print("    List alert rules (Infra Alerts)")
    print()
    print("  python3 tools/grafana.py alerts silence <consumer-group> [--duration 30] [--comment C]")
    print("    Silence alerts for a consumer group (duration in minutes)")
    print()
    print("  python3 tools/grafana.py alerts history <consumer-group> [--range 7]")
    print("    Show firing history with windows (range in days, default 7)")
    print()
    print("  python3 tools/grafana.py query lag <consumer-group> [--range 30] [--time ISO]")
    print("    Query lag trend over time (range in minutes)")
    print("    --time: query around a historical timestamp (ISO 8601, e.g. 2026-03-04T14:00:00Z)")
    print()
    print("  python3 tools/grafana.py query state <consumer-group>")
    print("    Query consumer group state (Stable/Dead/Rebalancing/etc)")
    print()
    print("  python3 tools/grafana.py query members <consumer-group>")
    print("    Query active member count")
    print()
    print("  python3 tools/grafana.py query all-lag")
    print("    Snapshot of all consumer group lags")
    print()
    print("  python3 tools/grafana.py query synthetics [<instance>] [--range 30]")
    print("    Synthetic check status (all checks, or detailed for one instance)")
    print()
    print("  python3 tools/grafana.py query synthetic-logs <instance> [--range 30]")
    print("    Fetch failure logs for a synthetic check (error messages, HTTP codes)")
    print()
    print("  python3 tools/grafana.py oncall current [--schedule tech-loma]")
    print("    Who is on call right now (Grafana IRM/OnCall API)")
    print()
    print("  python3 tools/grafana.py oncall next [--schedule tech-loma]")
    print("    Who is on call next")
    print()
    print("  python3 tools/grafana.py oncall schedules")
    print("    List OnCall schedules with their API ids")
    print()
    print("  Note: oncall commands require GRAFANA_ONCALL_URL + GRAFANA_ONCALL_TOKEN")
    sys.exit(1)


def _parse_flag(args: List[str], flag: str) -> Optional[str]:
    """Extract a flag and its value from args list, mutating args in place."""
    if flag not in args:
        return None
    idx = args.index(flag)
    if idx + 1 >= len(args):
        print(f"Error: {flag} requires an argument")
        sys.exit(1)
    val = args[idx + 1]
    del args[idx: idx + 2]
    return val


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) < 3:
        _print_usage()

    group = sys.argv[1]
    command = sys.argv[2]
    rest = list(sys.argv[3:])

    if group == "alerts":
        if command == "list":
            state = _parse_flag(rest, "--state") or "all"
            result = asyncio.run(list_alerts(state=state))

        elif command == "rules":
            result = asyncio.run(list_alert_rules())

        elif command == "silence":
            if not rest:
                print("Error: silence requires a consumer group name")
                sys.exit(1)
            consumer = rest.pop(0)
            duration = _parse_flag(rest, "--duration")
            comment = _parse_flag(rest, "--comment")
            result = asyncio.run(create_silence(
                consumer_group=consumer,
                duration_minutes=int(duration) if duration else 30,
                comment=comment or "Auto-triage: transient lag",
            ))

        elif command == "history":
            if not rest:
                print("Error: history requires a consumer group name")
                sys.exit(1)
            consumer = rest.pop(0)
            range_days = _parse_flag(rest, "--range")
            result = asyncio.run(alert_history(
                consumer_group=consumer,
                range_days=int(range_days) if range_days else 7,
            ))

        else:
            print(f"Unknown alerts command: {command}")
            _print_usage()

    elif group == "query":
        if command == "lag":
            if not rest:
                print("Error: lag requires a consumer group name")
                sys.exit(1)
            consumer = rest.pop(0)
            range_min = _parse_flag(rest, "--range")
            time_str = _parse_flag(rest, "--time")
            at_time = None  # type: Optional[datetime]
            if time_str:
                try:
                    # Support ISO 8601 format (e.g. 2026-03-04T14:00:00Z)
                    at_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                except ValueError:
                    print(f"Error: --time must be ISO 8601 format (e.g. 2026-03-04T14:00:00Z), got: {time_str}")
                    sys.exit(1)
            result = asyncio.run(query_lag(
                consumer_group=consumer,
                range_minutes=int(range_min) if range_min else 30,
                at_time=at_time,
            ))

        elif command == "state":
            if not rest:
                print("Error: state requires a consumer group name")
                sys.exit(1)
            result = asyncio.run(query_state(rest[0]))

        elif command == "members":
            if not rest:
                print("Error: members requires a consumer group name")
                sys.exit(1)
            result = asyncio.run(query_members(rest[0]))

        elif command == "all-lag":
            result = asyncio.run(query_all_lag())

        elif command == "synthetics":
            instance = rest.pop(0) if rest and not rest[0].startswith("--") else None
            range_min = _parse_flag(rest, "--range")
            result = asyncio.run(query_synthetics(
                instance=instance,
                range_minutes=int(range_min) if range_min else 30,
            ))

        elif command == "synthetic-logs":
            if not rest:
                print("Error: synthetic-logs requires an instance name")
                sys.exit(1)
            instance = rest.pop(0)
            range_min = _parse_flag(rest, "--range")
            start_flag = _parse_flag(rest, "--start")
            end_flag = _parse_flag(rest, "--end")
            result = asyncio.run(query_synthetic_logs(
                instance=instance,
                range_minutes=int(range_min) if range_min else 30,
                start_time=start_flag,
                end_time=end_flag,
            ))

        else:
            print(f"Unknown query command: {command}")
            _print_usage()

    elif group == "oncall":
        if command == "current":
            schedule = _parse_flag(rest, "--schedule") or "tech-loma"
            result = asyncio.run(oncall_current(schedule=schedule))

        elif command == "next":
            schedule = _parse_flag(rest, "--schedule") or "tech-loma"
            result = asyncio.run(oncall_next(schedule=schedule))

        elif command == "schedules":
            result = asyncio.run(oncall_schedules())

        else:
            print(f"Unknown oncall command: {command}")
            _print_usage()

    else:
        print(f"Unknown command group: {group}")
        _print_usage()

    print(json.dumps(result, indent=2))
