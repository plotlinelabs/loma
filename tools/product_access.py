"""Provision product access through the configured product team API."""
import os
import aiohttp


class ProductAccessError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


async def team_action(product_id, action, data):
    required = ("PRODUCT_ACCESS_API_URL", "PRODUCT_ACCESS_API_SECRET", "PRODUCT_ACCESS_SERVICE_MEMBER_ID")
    if not all(os.environ.get(name) for name in required):
        raise ProductAccessError("Product access provisioning is not configured")
    headers = {
        "Authorization": f"Bearer {os.environ['PRODUCT_ACCESS_API_SECRET']}",
        "x-product-id": product_id,
        "x-member-id": os.environ["PRODUCT_ACCESS_SERVICE_MEMBER_ID"],
    }
    url = os.environ["PRODUCT_ACCESS_API_URL"].rstrip("/") + "/internal/team"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post(url, headers=headers, json={"action": action, "data": data}) as response:
            try:
                payload = await response.json(content_type=None)
            except (ValueError, aiohttp.ClientError) as exc:
                raise ProductAccessError(
                    f"Dashboard API returned an invalid response ({response.status})",
                    response.status,
                ) from exc
            if response.status >= 400:
                error = payload.get("error", {}) if isinstance(payload, dict) else {}
                raise ProductAccessError(
                    error.get("message") or f"Dashboard API returned {response.status}",
                    response.status,
                )
            result = payload.get("data", payload)
            if not isinstance(result, dict):
                raise ProductAccessError("Dashboard API returned an invalid response")
            return result


async def grant(product_id, email, name, role):
    return await team_action(product_id, "invite", {
        "email": email, "name": name, "role": role.title(),
    })


async def revoke(product_id, member_id):
    return await team_action(product_id, "remove", {"target": member_id})
