"""HTTP client for the customer-admin offboarding API.

Credentials come from the `customer_admin` integration: the API key is the bearer
secret, `base_url` points at the service, `approver_emails` lists who may confirm.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import quote

import aiohttp

PROVIDER = "customer_admin"
OFFBOARD_PATH = "/maintenance/offboard"


def _setting(env: str, field: str | None = None, use_cache: bool = True) -> str:
    value = os.environ.get(env, "").strip()
    if value:
        return value
    from tools._integration_key import get_integration_extra, get_integration_key
    return (get_integration_extra(PROVIDER, field, use_cache=use_cache) if field else get_integration_key(PROVIDER)).strip()


def _config() -> tuple[str, str]:
    secret = _setting("CUSTOMER_ADMIN_API_SECRET")
    base_url = _setting("CUSTOMER_ADMIN_BASE_URL", "base_url").rstrip("/")
    if not secret or not base_url:
        raise ValueError("Customer Admin integration is not connected (needs an API secret and base_url).")
    return base_url, secret


def approver_emails() -> set[str]:
    """Emails allowed to confirm or undo an offboarding; re-read on every call."""
    raw = _setting("CUSTOMER_ADMIN_APPROVERS", "approver_emails", use_cache=False)
    return {email.strip().lower() for email in raw.replace(";", ",").split(",") if email.strip()}


def approval_channel() -> str:
    """Slack channel where proposals are posted for approvers."""
    return _setting("CUSTOMER_ADMIN_APPROVAL_CHANNEL", "approval_channel", use_cache=False)


async def _request(method: str, path: str, body: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    try:
        base_url, secret = _config()
    except ValueError as e:
        return {"error": str(e)}
    headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, f"{base_url}{OFFBOARD_PATH}{path}", headers=headers, json=body,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    data = {"message": (await resp.text())[:500]}
                if resp.status == 401:
                    return {"error": "Customer Admin API secret was rejected.", "status": 401}
                if resp.status == 503:
                    return {"error": "Offboarding is not enabled on the customer-admin service.", "status": 503}
                if resp.status != 200:
                    message = (data or {}).get("message") if isinstance(data, dict) else None
                    return {"error": message or f"HTTP {resp.status}", "status": resp.status}
                # A plan's own failure field is not a request error; keep "error" for failed calls only.
                if isinstance(data, dict) and "error" in data:
                    data["run_error"] = data.pop("error")
                return data
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return {"error": f"Failed to reach the customer-admin API: {e or type(e).__name__}"}


async def search(query: str) -> dict[str, Any]:
    return await _request("GET", f"/search?query={quote(query, safe='')}")


async def create_plan(requested_by: str, org_id: str | None = None, product_ids: list[str] | None = None,
                      reason: str | None = None, mode: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"requestedBy": requested_by}
    if mode:
        body["mode"] = mode
    if org_id:
        body["orgId"] = org_id
    if product_ids:
        body["productIds"] = product_ids
    if reason:
        body["reason"] = reason
    return await _request("POST", "/plans", body)


async def get_plan(plan_id: str) -> dict[str, Any]:
    return await _request("GET", f"/plans/{quote(plan_id, safe='')}")


async def apply_plan(plan_id: str, applied_by: str) -> dict[str, Any]:
    return await _request("POST", f"/plans/{quote(plan_id, safe='')}/apply", {"appliedBy": applied_by}, timeout=300)


async def restore_plan(plan_id: str, restored_by: str) -> dict[str, Any]:
    return await _request("POST", f"/plans/{quote(plan_id, safe='')}/restore", {"restoredBy": restored_by}, timeout=300)
