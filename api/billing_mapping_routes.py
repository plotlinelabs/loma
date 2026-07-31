"""Maintainer-only organization to MonetizeNow billing mapping API."""

import asyncio
from datetime import datetime, timezone
from typing import Any

from aiohttp import web
from bson import ObjectId

from api.auth_helpers import get_user_email, require_operator_or_above
from api.plotline_db import get_plotline_db
from observability.db import get_db
from tools.monetize_now import account_contracts, get_account, get_contract

ACTIVE = "ACTIVE"


def _id(value: Any) -> str:
    return str(value) if value is not None else ""


def _payload(result: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(result, dict) or result.get("error"):
        return None
    data = result.get("data", result)
    return data if isinstance(data, dict) else None


def _items(result: dict[str, Any]) -> list[dict[str, Any]]:
    data: Any = result.get("data", result) if isinstance(result, dict) else {}
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("content", "items", "results", "contracts"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _contract_account_id(contract: dict[str, Any]) -> str:
    account = contract.get("account")
    if isinstance(account, dict):
        return _id(account.get("id") or account.get("accountId"))
    return _id(contract.get("accountId") or contract.get("account_id"))


def _contract_view(contract: dict[str, Any]) -> dict[str, Any]:
    contract_id = _id(contract.get("id") or contract.get("contractId"))
    return {
        "id": contract_id,
        "name": contract.get("name") or contract.get("displayName") or contract_id,
        "status": str(contract.get("status") or "UNKNOWN").upper(),
        "accountId": _contract_account_id(contract),
        "legalEntityId": _id(contract.get("legalEntityId")),
        "startDate": contract.get("startDate") or contract.get("effectiveFrom"),
        "url": f"https://app.monetizenow.io/contracts/{contract_id}" if contract_id else None,
    }


def classify_product(billing_id: str | None, contract: dict[str, Any] | None, account_id: str | None) -> str:
    if not billing_id:
        return "contract_missing"
    if not contract:
        return "invalid_contract"
    if str(contract.get("status") or "").upper() != ACTIVE:
        return "inactive_contract"
    if not account_id:
        return "account_not_linked"
    if _contract_account_id(contract) != account_id:
        return "account_mismatch"
    return "correctly_linked"


async def _load_contract(billing_id: str | None, semaphore: asyncio.Semaphore):
    if not billing_id:
        return None
    async with semaphore:
        return _payload(await get_contract(billing_id))


async def handle_list_billing_mappings(request: web.Request) -> web.Response:
    require_operator_or_above(request)
    plotline_db, loma_db = get_plotline_db(), get_db()
    if plotline_db is None or loma_db is None:
        return web.json_response({"error": "Billing mapping databases are not configured"}, status=503)

    org_docs = await plotline_db.orgs.find({}, {"name": 1, "products": 1, "isBlocked": 1}).sort("name", 1).to_list(None)
    org_ids = [org["_id"] for org in org_docs]
    product_docs = await plotline_db.products.find(
        {"orgId": {"$in": org_ids}}, {"name": 1, "orgId": 1, "billingId": 1}
    ).to_list(None)
    mappings = await loma_db.billing_account_mappings.find(
        {"organization_id": {"$in": [_id(value) for value in org_ids]}}
    ).to_list(None)
    mapping_by_org = {item["organization_id"]: item for item in mappings}

    semaphore = asyncio.Semaphore(12)
    unique_billing_ids = {product.get("billingId") for product in product_docs if product.get("billingId")}
    contracts = await asyncio.gather(*[_load_contract(value, semaphore) for value in unique_billing_ids])
    contract_by_id = dict(zip(unique_billing_ids, contracts))
    products_by_org: dict[str, list[dict[str, Any]]] = {}
    for product in product_docs:
        org_id = _id(product.get("orgId"))
        billing_id = product.get("billingId")
        contract = contract_by_id.get(billing_id)
        account_id = (mapping_by_org.get(org_id) or {}).get("monetize_now_account_id")
        products_by_org.setdefault(org_id, []).append({
            "id": _id(product.get("_id")),
            "name": product.get("name") or "Unnamed product",
            "billingId": billing_id,
            "status": classify_product(billing_id, contract, account_id),
            "contract": _contract_view(contract) if contract else None,
        })

    organizations = []
    for org in org_docs:
        org_id = _id(org["_id"])
        mapping = mapping_by_org.get(org_id) or {}
        products = products_by_org.get(org_id, [])
        organizations.append({
            "id": org_id,
            "name": org.get("name") or "Unnamed organization",
            "isBlocked": bool(org.get("isBlocked")),
            "monetizeNowAccountId": mapping.get("monetize_now_account_id"),
            "products": products,
            "summary": {status: sum(p["status"] == status for p in products) for status in {
                "correctly_linked", "contract_missing", "invalid_contract", "inactive_contract",
                "account_mismatch", "account_not_linked",
            }},
        })
    return web.json_response({"organizations": organizations})


async def handle_set_account_mapping(request: web.Request) -> web.Response:
    require_operator_or_above(request)
    plotline_db, loma_db = get_plotline_db(), get_db()
    if plotline_db is None or loma_db is None:
        return web.json_response({"error": "Billing mapping databases are not configured"}, status=503)
    org_id = request.match_info["organization_id"]
    if not ObjectId.is_valid(org_id) or not await plotline_db.orgs.find_one({"_id": ObjectId(org_id)}):
        return web.json_response({"error": "Organization not found"}, status=404)
    body = await request.json()
    account_id = str(body.get("accountId") or "").strip()
    account = _payload(await get_account(account_id)) if account_id else None
    if not account:
        return web.json_response({"error": "Valid MonetizeNow accountId is required"}, status=400)
    now, actor = datetime.now(timezone.utc), get_user_email(request)
    old = await loma_db.billing_account_mappings.find_one({"organization_id": org_id})
    await loma_db.billing_account_mappings.update_one(
        {"organization_id": org_id},
        {"$set": {"organization_id": org_id, "monetize_now_account_id": account_id, "updated_at": now, "updated_by": actor}},
        upsert=True,
    )
    await loma_db.billing_mapping_audit.insert_one({
        "type": "account_mapping", "organization_id": org_id,
        "old_value": (old or {}).get("monetize_now_account_id"), "new_value": account_id,
        "updated_at": now, "updated_by": actor,
    })
    return web.json_response({"organizationId": org_id, "accountId": account_id})


async def handle_active_contracts(request: web.Request) -> web.Response:
    require_operator_or_above(request)
    loma_db = get_db()
    org_id = request.match_info["organization_id"]
    mapping = await loma_db.billing_account_mappings.find_one({"organization_id": org_id}) if loma_db is not None else None
    account_id = (mapping or {}).get("monetize_now_account_id")
    if not account_id:
        return web.json_response({"error": "Link a MonetizeNow account first"}, status=400)
    result = await account_contracts(account_id, status=ACTIVE, page_size=100)
    if result.get("error"):
        return web.json_response({"error": result["error"]}, status=502)
    contracts = [_contract_view(item) for item in _items(result) if str(item.get("status") or "").upper() == ACTIVE]
    return web.json_response({"contracts": contracts})


async def handle_set_product_contract(request: web.Request) -> web.Response:
    require_operator_or_above(request)
    plotline_db, loma_db = get_plotline_db(), get_db()
    if plotline_db is None or loma_db is None:
        return web.json_response({"error": "Billing mapping databases are not configured"}, status=503)
    product_id = request.match_info["product_id"]
    if not ObjectId.is_valid(product_id):
        return web.json_response({"error": "Product not found"}, status=404)
    product = await plotline_db.products.find_one({"_id": ObjectId(product_id)})
    if not product:
        return web.json_response({"error": "Product not found"}, status=404)
    body = await request.json()
    contract_id = str(body.get("contractId") or "").strip()
    contract = _payload(await get_contract(contract_id))
    mapping = await loma_db.billing_account_mappings.find_one({"organization_id": _id(product.get("orgId"))})
    account_id = (mapping or {}).get("monetize_now_account_id")
    if not contract or str(contract.get("status") or "").upper() != ACTIVE:
        return web.json_response({"error": "Only an ACTIVE contract can be linked"}, status=400)
    if not account_id or _contract_account_id(contract) != account_id:
        return web.json_response({"error": "Contract does not belong to the mapped account"}, status=400)
    now, actor, old = datetime.now(timezone.utc), get_user_email(request), product.get("billingId")
    await plotline_db.products.update_one({"_id": ObjectId(product_id)}, {"$set": {"billingId": contract_id, "updatedAt": now}})
    await loma_db.billing_mapping_audit.insert_one({
        "type": "product_contract", "organization_id": _id(product.get("orgId")), "product_id": product_id,
        "old_value": old, "new_value": contract_id, "updated_at": now, "updated_by": actor,
    })
    return web.json_response({"productId": product_id, "contractId": contract_id})


def setup_billing_mapping_routes(app: web.Application):
    app.router.add_get("/api/billing-mappings", handle_list_billing_mappings)
    app.router.add_put("/api/billing-mappings/organizations/{organization_id}/account", handle_set_account_mapping)
    app.router.add_get("/api/billing-mappings/organizations/{organization_id}/contracts", handle_active_contracts)
    app.router.add_put("/api/billing-mappings/products/{product_id}/contract", handle_set_product_contract)
