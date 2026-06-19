"""MonetizeNow billing API client.

Provides CLI commands for the Loma agent:
  1. monetize_now.py search-accounts <query>                    — Search accounts by name/id/customId
  2. monetize_now.py get-account <accountId>                    — Get account by ID
  3. monetize_now.py get-account-by-custom-id <customId>        — Get account by custom ID
  4. monetize_now.py list-accounts [--status S] [--page N] [--page-size N]  — List all accounts
  5. monetize_now.py get-contract <contractId>                  — Get contract by ID
  6. monetize_now.py list-contracts [--status S] [--account-id A] [--page N] [--page-size N]  — List contracts
  7. monetize_now.py account-contracts <accountId> [--status S]  — List contracts for an account
  8. monetize_now.py get-bill-group <billGroupId>               — Get bill group by ID (direct)
  9. monetize_now.py account-bill-groups <accountId>            — List bill groups for an account
  10. monetize_now.py get-account-bill-group <accountId> <billGroupId> — Get specific bill group
  11. monetize_now.py get-invoice <invoiceId>                    — Get invoice by ID
  12. monetize_now.py account-invoices <accountId> [--status S] [--bill-group-id B]  — List invoices for account
  13. monetize_now.py bill-group-invoices <accountId> <billGroupId>  — List invoices for bill group
  14. monetize_now.py get-subscription <subscriptionId>          — Get subscription by ID
  15. monetize_now.py account-subscriptions <accountId>          — List subscriptions for account
  16. monetize_now.py bill-group-subscriptions <billGroupId>     — List subscriptions for bill group

Requires MONETIZE_NOW_API_KEY and MONETIZE_NOW_BASE_URL environment variables.
API docs: https://docs.monetizenow.io/reference

Usage (called by the agent via Bash):
  python3 tools/monetize_now.py search-accounts "Example Corp"
  python3 tools/monetize_now.py get-contract cntr_abc123
  python3 tools/monetize_now.py list-contracts --status ACTIVE --page-size 20
"""

import asyncio
import json
import os
import sys
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


def _get_base_url() -> str:
    url = os.environ.get("MONETIZE_NOW_BASE_URL", "")
    if not url:
        raise ValueError(
            "MONETIZE_NOW_BASE_URL environment variable is not set. "
            "Expected value: https://api.monetizeplatform.com/api"
        )
    return url.rstrip("/")


def _get_api_key() -> str:
    key = os.environ.get("MONETIZE_NOW_API_KEY", "")
    if not key:
        raise ValueError(
            "MONETIZE_NOW_API_KEY environment variable is not set. "
            "Please configure it before using MonetizeNow tools."
        )
    return key


def _headers() -> dict[str, str]:
    return {
        "x-api-key": _get_api_key(),
        "Accept": "application/json",
    }


async def _api_get(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    """Shared GET helper. Returns parsed JSON or {"error": "..."}."""
    base = _get_base_url()
    url = f"{base}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=_headers(), params=params,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 401:
                    return {"error": "MonetizeNow API key is invalid or expired. Check MONETIZE_NOW_API_KEY."}
                if resp.status == 404:
                    return {"error": f"Not found: {path}"}
                if resp.status == 429:
                    return {"error": "MonetizeNow rate limit reached. Try again shortly."}
                if resp.status >= 400:
                    text = await resp.text()
                    return {"error": f"MonetizeNow API error (HTTP {resp.status}): {text[:500]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to MonetizeNow API: {e}"}


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


async def search_accounts(query: str, page: int = 0, page_size: int = 20) -> dict[str, Any]:
    """Search accounts by id, customId, and name."""
    params: dict[str, str] = {"query": query, "pageSize": str(page_size)}
    if page > 0:
        params["currentPage"] = str(page)
    return await _api_get("/accounts/search", params)


async def get_account(account_id: str) -> dict[str, Any]:
    """Get account by account ID."""
    return await _api_get(f"/accounts/{account_id}")


async def get_account_by_custom_id(custom_id: str) -> dict[str, Any]:
    """Get account by custom ID."""
    return await _api_get(f"/accounts/customId/{custom_id}")


async def list_accounts(
    status: str | None = None,
    page: int = 0,
    page_size: int = 20,
    sort: str | None = None,
) -> dict[str, Any]:
    """List all accounts with optional filters."""
    params: dict[str, str] = {"pageSize": str(page_size)}
    if page > 0:
        params["currentPage"] = str(page)
    if status:
        params["status"] = status
    if sort:
        params["sort"] = sort
    return await _api_get("/accounts", params)


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


async def get_contract(contract_id: str) -> dict[str, Any]:
    """Get contract by ID. Use billingId from MongoDB products collection."""
    return await _api_get(f"/contracts/{contract_id}")


async def list_contracts(
    status: str | None = None,
    account_id: str | None = None,
    page: int = 0,
    page_size: int = 20,
    sort: str | None = None,
) -> dict[str, Any]:
    """List all contracts with optional filters."""
    params: dict[str, str] = {"pageSize": str(page_size)}
    if page > 0:
        params["currentPage"] = str(page)
    if status:
        params["status"] = status
    if account_id:
        params["accountId"] = account_id
    if sort:
        params["sort"] = sort
    return await _api_get("/contracts", params)


async def account_contracts(
    account_id: str,
    status: str | None = None,
    page: int = 0,
    page_size: int = 20,
) -> dict[str, Any]:
    """List contracts for a specific account."""
    params: dict[str, str] = {"pageSize": str(page_size)}
    if page > 0:
        params["currentPage"] = str(page)
    if status:
        params["status"] = status
    return await _api_get(f"/accounts/{account_id}/contracts", params)


# ---------------------------------------------------------------------------
# Bill Groups
# ---------------------------------------------------------------------------


async def get_bill_group(bill_group_id: str) -> dict[str, Any]:
    """Get bill group directly by ID."""
    return await _api_get(f"/billGroups/{bill_group_id}")


async def account_bill_groups(
    account_id: str,
    status: str | None = None,
    page: int = 0,
    page_size: int = 20,
) -> dict[str, Any]:
    """List all bill groups for an account."""
    params: dict[str, str] = {"pageSize": str(page_size)}
    if page > 0:
        params["currentPage"] = str(page)
    if status:
        params["status"] = status
    return await _api_get(f"/accounts/{account_id}/billGroups", params)


async def get_account_bill_group(account_id: str, bill_group_id: str) -> dict[str, Any]:
    """Get a specific bill group for an account."""
    return await _api_get(f"/accounts/{account_id}/billGroups/{bill_group_id}")


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


async def get_invoice(invoice_id: str) -> dict[str, Any]:
    """Get invoice by ID."""
    return await _api_get(f"/invoices/{invoice_id}")


async def account_invoices(
    account_id: str,
    status: str | None = None,
    bill_group_id: str | None = None,
    page: int = 0,
    page_size: int = 20,
    sort: str | None = None,
) -> dict[str, Any]:
    """List invoices for an account with optional filters."""
    params: dict[str, str] = {"pageSize": str(page_size)}
    if page > 0:
        params["currentPage"] = str(page)
    if status:
        params["status"] = status
    if bill_group_id:
        params["billGroupId"] = bill_group_id
    if sort:
        params["sort"] = sort
    return await _api_get(f"/accounts/{account_id}/invoices", params)


async def bill_group_invoices(
    account_id: str,
    bill_group_id: str,
    page: int = 0,
    page_size: int = 20,
    sort: str | None = None,
) -> dict[str, Any]:
    """List invoices for a specific bill group."""
    params: dict[str, str] = {"pageSize": str(page_size)}
    if page > 0:
        params["currentPage"] = str(page)
    if sort:
        params["sort"] = sort
    return await _api_get(f"/accounts/{account_id}/billGroups/{bill_group_id}/invoices", params)


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


async def get_subscription(subscription_id: str) -> dict[str, Any]:
    """Get subscription by ID."""
    return await _api_get(f"/subscriptions/{subscription_id}")


async def account_subscriptions(
    account_id: str,
    billing_status: str | None = None,
    page: int = 0,
    page_size: int = 20,
) -> dict[str, Any]:
    """List subscriptions for an account."""
    params: dict[str, str] = {"pageSize": str(page_size)}
    if page > 0:
        params["currentPage"] = str(page)
    if billing_status:
        params["billingStatus"] = billing_status
    return await _api_get(f"/accounts/{account_id}/subscriptions", params)


async def bill_group_subscriptions(
    bill_group_id: str,
    billing_status: str | None = None,
    page: int = 0,
    page_size: int = 20,
) -> dict[str, Any]:
    """List subscriptions for a bill group."""
    params: dict[str, str] = {"pageSize": str(page_size)}
    if page > 0:
        params["currentPage"] = str(page)
    if billing_status:
        params["billingStatus"] = billing_status
    return await _api_get(f"/billGroups/{bill_group_id}/subscriptions", params)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_usage():
    print("MonetizeNow CLI \u2014 Example billing data lookups")
    print()
    print("Usage:")
    print("  python3 tools/monetize_now.py <command> [args] [options]")
    print()
    print("Account commands:")
    print("  search-accounts <query>                         Search by name/id/customId")
    print("  get-account <accountId>                         Get account by ID")
    print("  get-account-by-custom-id <customId>             Get account by custom ID")
    print("  list-accounts [--status S] [--page N] [--page-size N] [--sort S]")
    print()
    print("Contract commands:")
    print("  get-contract <contractId>                       Get contract by ID")
    print("  list-contracts [--status S] [--account-id A] [--page N] [--page-size N]")
    print("  account-contracts <accountId> [--status S]      List contracts for account")
    print()
    print("Bill Group commands:")
    print("  get-bill-group <billGroupId>                    Get bill group by ID")
    print("  account-bill-groups <accountId> [--status S]    List bill groups for account")
    print("  get-account-bill-group <accountId> <billGroupId>  Get specific bill group")
    print()
    print("Invoice commands:")
    print("  get-invoice <invoiceId>                         Get invoice by ID")
    print("  account-invoices <accountId> [--status S] [--bill-group-id B]")
    print("  bill-group-invoices <accountId> <billGroupId>   List invoices for bill group")
    print()
    print("Subscription commands:")
    print("  get-subscription <subscriptionId>               Get subscription by ID")
    print("  account-subscriptions <accountId> [--billing-status S]")
    print("  bill-group-subscriptions <billGroupId> [--billing-status S]")
    print()
    print("Common options:")
    print("  --page N          Page number (0-based, default 0)")
    print("  --page-size N     Results per page (default 20)")
    print("  --sort S          Sort field (e.g. 'createDate,desc')")
    print()
    print("Status values:")
    print("  Account:      ACTIVE, CANCELED, SUSPENDED, INACTIVE")
    print("  Contract:     ACTIVE, CANCELED, PENDING, FINISHED")
    print("  Invoice:      DRAFT, CANCELED, UNPAID, PENDING, PAID, REVERSED")
    print("  Bill Group:   ACTIVE, INACTIVE, CANCELED, SUSPENDED")
    print("  Subscription: ACTIVE, CANCELED, PENDING, SUSPENDED (billingStatus)")
    print()
    print("Note: CANCELED uses single L (not CANCELLED). FINISHED (not EXPIRED).")
    sys.exit(1)


def _parse_flag(args: list[str], flag: str) -> str | None:
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


def _parse_common_flags(args: list[str]) -> dict[str, Any]:
    """Extract common pagination/sort flags."""
    result: dict[str, Any] = {}
    page_str = _parse_flag(args, "--page")
    if page_str is not None:
        result["page"] = int(page_str)
    ps_str = _parse_flag(args, "--page-size")
    if ps_str is not None:
        result["page_size"] = int(ps_str)
    sort_val = _parse_flag(args, "--sort")
    if sort_val is not None:
        result["sort"] = sort_val
    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) < 2:
        _print_usage()

    command = sys.argv[1]
    rest = list(sys.argv[2:])

    # --- Accounts ---
    if command == "search-accounts":
        if not rest:
            print("Error: search-accounts requires a query string")
            sys.exit(1)
        common = _parse_common_flags(rest)
        query = " ".join(rest)  # remaining args after flags are the query
        result = asyncio.run(search_accounts(query, **common))

    elif command == "get-account":
        if not rest:
            print("Error: get-account requires an account ID")
            sys.exit(1)
        result = asyncio.run(get_account(rest[0]))

    elif command == "get-account-by-custom-id":
        if not rest:
            print("Error: get-account-by-custom-id requires a custom ID")
            sys.exit(1)
        result = asyncio.run(get_account_by_custom_id(rest[0]))

    elif command == "list-accounts":
        status = _parse_flag(rest, "--status")
        common = _parse_common_flags(rest)
        result = asyncio.run(list_accounts(status=status, **common))

    # --- Contracts ---
    elif command == "get-contract":
        if not rest:
            print("Error: get-contract requires a contract ID")
            sys.exit(1)
        result = asyncio.run(get_contract(rest[0]))

    elif command == "list-contracts":
        status = _parse_flag(rest, "--status")
        account_id = _parse_flag(rest, "--account-id")
        common = _parse_common_flags(rest)
        result = asyncio.run(list_contracts(status=status, account_id=account_id, **common))

    elif command == "account-contracts":
        if not rest:
            print("Error: account-contracts requires an account ID")
            sys.exit(1)
        status = _parse_flag(rest, "--status")
        common = _parse_common_flags(rest)
        result = asyncio.run(account_contracts(rest[0], status=status, **common))

    # --- Bill Groups ---
    elif command == "get-bill-group":
        if not rest:
            print("Error: get-bill-group requires a bill group ID")
            sys.exit(1)
        result = asyncio.run(get_bill_group(rest[0]))

    elif command == "account-bill-groups":
        if not rest:
            print("Error: account-bill-groups requires an account ID")
            sys.exit(1)
        status = _parse_flag(rest, "--status")
        common = _parse_common_flags(rest)
        result = asyncio.run(account_bill_groups(rest[0], status=status, **common))

    elif command == "get-account-bill-group":
        if len(rest) < 2:
            print("Error: get-account-bill-group requires <accountId> <billGroupId>")
            sys.exit(1)
        result = asyncio.run(get_account_bill_group(rest[0], rest[1]))

    # --- Invoices ---
    elif command == "get-invoice":
        if not rest:
            print("Error: get-invoice requires an invoice ID")
            sys.exit(1)
        result = asyncio.run(get_invoice(rest[0]))

    elif command == "account-invoices":
        if not rest:
            print("Error: account-invoices requires an account ID")
            sys.exit(1)
        status = _parse_flag(rest, "--status")
        bg_id = _parse_flag(rest, "--bill-group-id")
        common = _parse_common_flags(rest)
        result = asyncio.run(account_invoices(rest[0], status=status, bill_group_id=bg_id, **common))

    elif command == "bill-group-invoices":
        if len(rest) < 2:
            print("Error: bill-group-invoices requires <accountId> <billGroupId>")
            sys.exit(1)
        common = _parse_common_flags(rest)
        result = asyncio.run(bill_group_invoices(rest[0], rest[1], **common))

    # --- Subscriptions ---
    elif command == "get-subscription":
        if not rest:
            print("Error: get-subscription requires a subscription ID")
            sys.exit(1)
        result = asyncio.run(get_subscription(rest[0]))

    elif command == "account-subscriptions":
        if not rest:
            print("Error: account-subscriptions requires an account ID")
            sys.exit(1)
        billing_status = _parse_flag(rest, "--billing-status")
        common = _parse_common_flags(rest)
        result = asyncio.run(account_subscriptions(rest[0], billing_status=billing_status, **common))

    elif command == "bill-group-subscriptions":
        if not rest:
            print("Error: bill-group-subscriptions requires a bill group ID")
            sys.exit(1)
        billing_status = _parse_flag(rest, "--billing-status")
        common = _parse_common_flags(rest)
        result = asyncio.run(bill_group_subscriptions(rest[0], billing_status=billing_status, **common))

    else:
        print(f"Unknown command: {command}")
        _print_usage()

    print(json.dumps(result, indent=2, default=str))
