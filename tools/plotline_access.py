"""Provision product access through Plotline's authoritative team API."""
import os
import aiohttp


class PlotlineAccessError(RuntimeError):
    pass


async def team_action(product_id, action, data):
    required = ("PLOTLINE_API_URL", "PLOTLINE_API_SECRET", "PLOTLINE_SERVICE_MEMBER_ID")
    if not all(os.environ.get(name) for name in required):
        raise PlotlineAccessError("Plotline dashboard provisioning is not configured")
    headers = {
        "Authorization": f"Bearer {os.environ['PLOTLINE_API_SECRET']}",
        "x-product-id": product_id,
        "x-member-id": os.environ["PLOTLINE_SERVICE_MEMBER_ID"],
    }
    url = os.environ["PLOTLINE_API_URL"].rstrip("/") + "/internal/team"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post(url, headers=headers, json={"action": action, "data": data}) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400:
                error = payload.get("error", {}) if isinstance(payload, dict) else {}
                raise PlotlineAccessError(error.get("message") or f"Dashboard API returned {response.status}")
            return payload.get("data", payload)


async def grant(product_id, email, name, role):
    return await team_action(product_id, "invite", {
        "email": email, "name": name, "role": role.title(),
    })


async def revoke(product_id, member_id):
    return await team_action(product_id, "remove", {"target": member_id})
