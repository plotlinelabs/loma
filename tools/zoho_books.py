"""Zoho Books API client.

Provides CLI commands for the Loma agent:
  1. zoho_books.py get-contact --region REGION CONTACT_ID
  2. zoho_books.py search-contacts --region REGION --name NAME [--status STATUS]
  3. zoho_books.py search-contacts-by-company --region REGION --company NAME [--status STATUS]
  4. zoho_books.py list-invoices --region REGION --customer-id ID [--status STATUS]
  5. zoho_books.py get-invoice --region REGION INVOICE_ID
  6. zoho_books.py list-estimates --region REGION --customer-id ID [--status STATUS]
  7. zoho_books.py get-estimate --region REGION ESTIMATE_ID
  8. zoho_books.py list-credit-notes --region REGION --customer-id ID
  9. zoho_books.py get-credit-note --region REGION CREDITNOTE_ID
 10. zoho_books.py list-payments --region REGION --customer-id ID
 11. zoho_books.py get-payment --region REGION PAYMENT_ID
 12. zoho_books.py list-recurring-invoices --region REGION --customer-id ID
 13. echo JSON | zoho_books.py send-invoice --region REGION INVOICE_ID
 14. echo JSON | zoho_books.py send-estimate --region REGION ESTIMATE_ID
 15. zoho_books.py download-invoice-pdf --region REGION --output PATH INVOICE_ID
 16. zoho_books.py download-estimate-pdf --region REGION --output PATH ESTIMATE_ID

Requires per-region environment variables:
  ZOHO_CLIENT_ID_IN, ZOHO_CLIENT_SECRET_IN, ZOHO_REFRESH_TOKEN_IN, ZOHO_ORGANIZATION_ID_IN
  ZOHO_CLIENT_ID_US, ZOHO_CLIENT_SECRET_US, ZOHO_REFRESH_TOKEN_US, ZOHO_ORGANIZATION_ID_US

Access tokens are cached to /tmp/.zoho_token_{region}.json and reused across
invocations until they expire (1 hour lifetime, refreshed 5 min early).

Usage (called by the agent via Bash):
  python3 tools/zoho_books.py get-contact --region in 123456789
  python3 tools/zoho_books.py search-contacts --region in --name "Example Corp"
  python3 tools/zoho_books.py list-invoices --region in --customer-id 123456789
  python3 tools/zoho_books.py download-invoice-pdf --region in --output /tmp/invoice.pdf 123456789
"""

import asyncio
import json
import os
import sys
import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Region-specific configuration
REGION_CONFIG = {
    "in": {
        "accounts_url": "https://accounts.zoho.in",
        "api_base": "https://books.zoho.in/api/v3",
        "client_id_env": "ZOHO_CLIENT_ID_IN",
        "client_secret_env": "ZOHO_CLIENT_SECRET_IN",
        "refresh_token_env": "ZOHO_REFRESH_TOKEN_IN",
        "org_id_env": "ZOHO_ORGANIZATION_ID_IN",
    },
    "us": {
        "accounts_url": "https://accounts.zoho.com",
        "api_base": "https://books.zoho.com/api/v3",
        "client_id_env": "ZOHO_CLIENT_ID_US",
        "client_secret_env": "ZOHO_CLIENT_SECRET_US",
        "refresh_token_env": "ZOHO_REFRESH_TOKEN_US",
        "org_id_env": "ZOHO_ORGANIZATION_ID_US",
    },
}

# In-memory cache for the current process (avoids repeated file reads within
# a single invocation that makes multiple API calls)
_token_cache: dict[str, str] = {}

# Zoho access tokens are valid for 3600s (1 hour). We refresh 5 min early to
# avoid edge-case expiry during a request.
_TOKEN_LIFETIME_SECONDS = 3600
_TOKEN_SAFETY_MARGIN_SECONDS = 300

# File-based token cache directory
_TOKEN_CACHE_DIR = "/tmp"


# --- Token persistence helpers ---


def _token_cache_path(region: str) -> str:
    """Return the file path for a region's cached token."""
    return os.path.join(_TOKEN_CACHE_DIR, f".zoho_token_{region}.json")


def _load_cached_token(region: str) -> str | None:
    """Load a cached access token from disk if it exists and is not expired."""
    path = _token_cache_path(region)
    try:
        with open(path, "r") as f:
            data = json.load(f)
        token = data.get("access_token", "")
        expires_at = data.get("expires_at", 0)
        if token and time.time() < expires_at:
            return token
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        pass
    return None


def _save_cached_token(region: str, token: str) -> None:
    """Persist an access token to disk with an expiry timestamp."""
    path = _token_cache_path(region)
    expires_at = time.time() + _TOKEN_LIFETIME_SECONDS - _TOKEN_SAFETY_MARGIN_SECONDS
    try:
        with open(path, "w") as f:
            json.dump({"access_token": token, "expires_at": expires_at}, f)
    except OSError:
        # Non-fatal -- worst case we just refresh again next invocation
        pass


def _invalidate_cached_token(region: str) -> None:
    """Remove a cached token from both memory and disk."""
    _token_cache.pop(region, None)
    path = _token_cache_path(region)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


# --- Configuration helpers ---


def _get_region_config(region: str) -> dict[str, str]:
    """Get region-specific configuration. Validates region and env vars."""
    region = region.lower()
    if region not in REGION_CONFIG:
        raise ValueError(f"Invalid region '{region}'. Must be 'in' or 'us'.")
    config = REGION_CONFIG[region]

    # Validate all required env vars exist
    missing = []
    for key in ("client_id_env", "client_secret_env", "refresh_token_env", "org_id_env"):
        env_var = config[key]
        if not os.environ.get(env_var):
            missing.append(env_var)
    if missing:
        raise ValueError(
            f"Missing environment variables for region '{region}': {', '.join(missing)}"
        )
    return config


async def _refresh_access_token(region: str, force: bool = False) -> str:
    """Get an access token for a region.

    Checks in-memory cache -> file cache -> refreshes from Zoho OAuth.
    The token is persisted to disk so subsequent CLI invocations reuse it
    until it expires (1 hour, with a 5-min safety margin).

    Set force=True to bypass caches and get a fresh token (e.g., after a 401).
    """
    if not force:
        # 1. Check in-memory cache (same process, multiple API calls)
        if region in _token_cache:
            return _token_cache[region]

        # 2. Check file-based cache (across process invocations)
        cached = _load_cached_token(region)
        if cached:
            _token_cache[region] = cached
            return cached

    config = _get_region_config(region)
    url = f"{config['accounts_url']}/oauth/v2/token"
    data = {
        "refresh_token": os.environ[config["refresh_token_env"]],
        "client_id": os.environ[config["client_id_env"]],
        "client_secret": os.environ[config["client_secret_env"]],
        "grant_type": "refresh_token",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, data=data, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ValueError(
                        f"Token refresh failed (HTTP {resp.status}): {text[:300]}"
                    )
                result = await resp.json()
                token = result.get("access_token")
                if not token:
                    raise ValueError(
                        f"Token refresh response missing access_token: {json.dumps(result)[:300]}"
                    )
                # Persist to both in-memory and file cache
                _token_cache[region] = token
                _save_cached_token(region, token)
                return token
    except aiohttp.ClientError as e:
        raise ValueError(f"Failed to connect for token refresh: {e}")


# --- Shared HTTP helpers ---


async def _api_get(
    region: str, endpoint: str, params: dict[str, str] | None = None
) -> dict[str, Any]:
    """GET helper with automatic token refresh. Returns parsed JSON or {"error": "..."}."""
    try:
        config = _get_region_config(region)
        token = await _refresh_access_token(region)
    except ValueError as e:
        return {"error": str(e)}

    url = f"{config['api_base']}/{endpoint.lstrip('/')}"
    query_params = {"organization_id": os.environ[config["org_id_env"]]}
    if params:
        query_params.update(params)

    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, params=query_params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 401:
                    # Token expired despite our safety margin -- force-refresh
                    # and retry once
                    _invalidate_cached_token(region)
                    try:
                        new_token = await _refresh_access_token(region, force=True)
                    except ValueError as e:
                        return {"error": f"Token refresh after 401 failed: {e}"}
                    headers["Authorization"] = f"Zoho-oauthtoken {new_token}"
                    async with session.get(
                        url, headers=headers, params=query_params,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as retry_resp:
                        if retry_resp.status != 200:
                            text = await retry_resp.text()
                            return {"error": f"Zoho API error after token refresh (HTTP {retry_resp.status}): {text[:500]}"}
                        return await retry_resp.json()
                if resp.status == 404:
                    return {"error": f"Not found: {endpoint}"}
                if resp.status == 429:
                    return {"error": "Zoho rate limit reached (100 req/min). Try again shortly."}
                if resp.status != 200:
                    text = await resp.text()
                    return {"error": f"Zoho API error (HTTP {resp.status}): {text[:500]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to Zoho Books API: {e}"}


async def _api_get_pdf(
    region: str, endpoint: str, output_path: str
) -> dict[str, Any]:
    """GET helper that downloads a PDF file. Uses accept=pdf query param.

    Returns {"file_path": "...", "size_bytes": N} on success or {"error": "..."}.
    """
    try:
        config = _get_region_config(region)
        token = await _refresh_access_token(region)
    except ValueError as e:
        return {"error": str(e)}

    url = f"{config['api_base']}/{endpoint.lstrip('/')}"
    query_params = {
        "organization_id": os.environ[config["org_id_env"]],
        "accept": "pdf",
    }

    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
        "Accept": "application/pdf",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, params=query_params,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 401:
                    _token_cache.pop(region, None)
                    return {"error": "Zoho access token expired or invalid. Please retry."}
                if resp.status == 404:
                    return {"error": f"Not found: {endpoint}"}
                if resp.status == 429:
                    return {"error": "Zoho rate limit reached (100 req/min). Try again shortly."}
                if resp.status != 200:
                    text = await resp.text()
                    return {"error": f"Zoho API error (HTTP {resp.status}): {text[:500]}"}

                # Check content type to confirm we got a PDF
                content_type = resp.headers.get("Content-Type", "")
                if "application/pdf" not in content_type and "application/octet-stream" not in content_type:
                    # Zoho may return JSON error even on 200
                    text = await resp.text()
                    return {"error": f"Expected PDF but got {content_type}: {text[:500]}"}

                # Ensure output directory exists
                output_dir = os.path.dirname(output_path)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)

                # Write PDF to file
                pdf_bytes = await resp.read()
                with open(output_path, "wb") as f:
                    f.write(pdf_bytes)

                return {
                    "file_path": output_path,
                    "size_bytes": len(pdf_bytes),
                }
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to Zoho Books API: {e}"}


async def _api_post(
    region: str, endpoint: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """POST helper with automatic token refresh. Returns parsed JSON or {"error": "..."}."""
    try:
        config = _get_region_config(region)
        token = await _refresh_access_token(region)
    except ValueError as e:
        return {"error": str(e)}

    url = f"{config['api_base']}/{endpoint.lstrip('/')}"
    query_params = {"organization_id": os.environ[config["org_id_env"]]}

    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, params=query_params,
                json=body or {},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 401:
                    # Token expired -- force-refresh and retry once
                    _invalidate_cached_token(region)
                    try:
                        new_token = await _refresh_access_token(region, force=True)
                    except ValueError as e:
                        return {"error": f"Token refresh after 401 failed: {e}"}
                    headers["Authorization"] = f"Zoho-oauthtoken {new_token}"
                    async with session.post(
                        url, headers=headers, params=query_params,
                        json=body or {},
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as retry_resp:
                        if retry_resp.status not in (200, 201):
                            text = await retry_resp.text()
                            return {"error": f"Zoho API error after token refresh (HTTP {retry_resp.status}): {text[:500]}"}
                        return await retry_resp.json()
                if resp.status == 429:
                    return {"error": "Zoho rate limit reached (100 req/min). Try again shortly."}
                if resp.status not in (200, 201):
                    text = await resp.text()
                    return {"error": f"Zoho API error (HTTP {resp.status}): {text[:500]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to Zoho Books API: {e}"}


# --- Public async functions ---


async def get_contact(region: str, contact_id: str) -> dict[str, Any]:
    """Get a contact by ID."""
    return await _api_get(region, f"/contacts/{contact_id}")


async def search_contacts(
    region: str, name: str, status: str | None = None
) -> dict[str, Any]:
    """Search contacts by name with optional status filter."""
    params: dict[str, str] = {"contact_name": name}
    if status:
        params["status"] = status
    return await _api_get(region, "/contacts", params)


async def search_contacts_by_company(
    region: str, company_name: str, status: str | None = None
) -> dict[str, Any]:
    """Search contacts by company name with optional status filter."""
    params: dict[str, str] = {"company_name": company_name}
    if status:
        params["status"] = status
    return await _api_get(region, "/contacts", params)


async def list_invoices(
    region: str, customer_id: str, status: str | None = None
) -> dict[str, Any]:
    """List invoices for a customer."""
    params: dict[str, str] = {"customer_id": customer_id}
    if status:
        params["status"] = status
    return await _api_get(region, "/invoices", params)


async def get_invoice(region: str, invoice_id: str) -> dict[str, Any]:
    """Get invoice details by ID."""
    return await _api_get(region, f"/invoices/{invoice_id}")


async def download_invoice_pdf(
    region: str, invoice_id: str, output_path: str
) -> dict[str, Any]:
    """Download an invoice as a PDF file."""
    return await _api_get_pdf(region, f"/invoices/{invoice_id}", output_path)


async def list_estimates(
    region: str, customer_id: str, status: str | None = None
) -> dict[str, Any]:
    """List estimates (proforma invoices) for a customer."""
    params: dict[str, str] = {"customer_id": customer_id}
    if status:
        params["status"] = status
    return await _api_get(region, "/estimates", params)


async def get_estimate(region: str, estimate_id: str) -> dict[str, Any]:
    """Get estimate details by ID."""
    return await _api_get(region, f"/estimates/{estimate_id}")


async def download_estimate_pdf(
    region: str, estimate_id: str, output_path: str
) -> dict[str, Any]:
    """Download an estimate as a PDF file."""
    return await _api_get_pdf(region, f"/estimates/{estimate_id}", output_path)


async def list_credit_notes(region: str, customer_id: str) -> dict[str, Any]:
    """List credit notes for a customer."""
    params: dict[str, str] = {"customer_id": customer_id}
    return await _api_get(region, "/creditnotes", params)


async def get_credit_note(region: str, creditnote_id: str) -> dict[str, Any]:
    """Get credit note details by ID."""
    return await _api_get(region, f"/creditnotes/{creditnote_id}")


async def list_payments(region: str, customer_id: str) -> dict[str, Any]:
    """List customer payments."""
    params: dict[str, str] = {"customer_id": customer_id}
    return await _api_get(region, "/customerpayments", params)


async def get_payment(region: str, payment_id: str) -> dict[str, Any]:
    """Get payment details by ID."""
    return await _api_get(region, f"/customerpayments/{payment_id}")


async def list_recurring_invoices(region: str, customer_id: str) -> dict[str, Any]:
    """List recurring invoices for a customer."""
    params: dict[str, str] = {"customer_id": customer_id}
    return await _api_get(region, "/recurringinvoices", params)


async def send_invoice(
    region: str, invoice_id: str, email_data: dict[str, Any]
) -> dict[str, Any]:
    """Send invoice via email. email_data should contain to_mail_ids, cc_mail_ids, subject, body."""
    return await _api_post(region, f"/invoices/{invoice_id}/email", email_data)


async def send_estimate(
    region: str, estimate_id: str, email_data: dict[str, Any]
) -> dict[str, Any]:
    """Send estimate via email."""
    return await _api_post(region, f"/estimates/{estimate_id}/email", email_data)


# --- CLI entry point ---


def _print_usage():
    print("Zoho Books CLI \u2014 Usage:")
    print()
    print("  python3 tools/zoho_books.py get-contact --region REGION CONTACT_ID")
    print("    Get a contact by ID")
    print()
    print("  python3 tools/zoho_books.py search-contacts --region REGION --name NAME [--status STATUS]")
    print("    Search contacts by name (status: active, inactive, all)")
    print()
    print("  python3 tools/zoho_books.py search-contacts-by-company --region REGION --company NAME [--status STATUS]")
    print("    Search contacts by company name")
    print()
    print("  python3 tools/zoho_books.py list-invoices --region REGION --customer-id ID [--status STATUS]")
    print("    List invoices for a customer (status: sent, draft, overdue, paid, void, unpaid, partially_paid)")
    print()
    print("  python3 tools/zoho_books.py get-invoice --region REGION INVOICE_ID")
    print("    Get invoice details")
    print()
    print("  python3 tools/zoho_books.py download-invoice-pdf --region REGION --output PATH INVOICE_ID")
    print("    Download invoice as PDF to specified path")
    print()
    print("  python3 tools/zoho_books.py list-estimates --region REGION --customer-id ID [--status STATUS]")
    print("    List estimates/proforma invoices (status: draft, sent, invoiced, accepted, declined, expired)")
    print()
    print("  python3 tools/zoho_books.py get-estimate --region REGION ESTIMATE_ID")
    print("    Get estimate details")
    print()
    print("  python3 tools/zoho_books.py download-estimate-pdf --region REGION --output PATH ESTIMATE_ID")
    print("    Download estimate as PDF to specified path")
    print()
    print("  python3 tools/zoho_books.py list-credit-notes --region REGION --customer-id ID")
    print("    List credit notes for a customer")
    print()
    print("  python3 tools/zoho_books.py get-credit-note --region REGION CREDITNOTE_ID")
    print("    Get credit note details")
    print()
    print("  python3 tools/zoho_books.py list-payments --region REGION --customer-id ID")
    print("    List payments for a customer")
    print()
    print("  python3 tools/zoho_books.py get-payment --region REGION PAYMENT_ID")
    print("    Get payment details")
    print()
    print("  python3 tools/zoho_books.py list-recurring-invoices --region REGION --customer-id ID")
    print("    List recurring invoices for a customer")
    print()
    print("  echo JSON | python3 tools/zoho_books.py send-invoice --region REGION INVOICE_ID")
    print('    Send invoice via email (JSON: {"to_mail_ids": [...], "cc_mail_ids": [...], "subject": "...", "body": "..."})')
    print()
    print("  echo JSON | python3 tools/zoho_books.py send-estimate --region REGION ESTIMATE_ID")
    print("    Send estimate via email (same JSON format)")
    sys.exit(1)


def _parse_flag(args: list[str], flag: str) -> str | None:
    """Extract a flag and its single value from args list, mutating args in place."""
    if flag not in args:
        return None
    idx = args.index(flag)
    if idx + 1 >= len(args):
        print(json.dumps({"error": f"{flag} requires an argument"}))
        sys.exit(1)
    val = args[idx + 1]
    del args[idx : idx + 2]
    return val


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()

    if len(sys.argv) < 2:
        _print_usage()

    command = sys.argv[1]
    rest = list(sys.argv[2:])

    # Extract --region (required for all commands)
    region = _parse_flag(rest, "--region")
    if not region:
        print(json.dumps({"error": "--region is required (in or us)"}))
        sys.exit(1)

    result: dict[str, Any] = {}

    if command == "get-contact":
        if not rest:
            print(json.dumps({"error": "get-contact requires a CONTACT_ID"}))
            sys.exit(1)
        result = asyncio.run(get_contact(region, rest[0]))

    elif command == "search-contacts":
        name = _parse_flag(rest, "--name")
        status = _parse_flag(rest, "--status")
        if not name:
            print(json.dumps({"error": "search-contacts requires --name"}))
            sys.exit(1)
        result = asyncio.run(search_contacts(region, name, status=status))

    elif command == "search-contacts-by-company":
        company = _parse_flag(rest, "--company")
        status = _parse_flag(rest, "--status")
        if not company:
            print(json.dumps({"error": "search-contacts-by-company requires --company"}))
            sys.exit(1)
        result = asyncio.run(search_contacts_by_company(region, company, status=status))

    elif command == "list-invoices":
        customer_id = _parse_flag(rest, "--customer-id")
        status = _parse_flag(rest, "--status")
        if not customer_id:
            print(json.dumps({"error": "list-invoices requires --customer-id"}))
            sys.exit(1)
        result = asyncio.run(list_invoices(region, customer_id, status=status))

    elif command == "get-invoice":
        if not rest:
            print(json.dumps({"error": "get-invoice requires an INVOICE_ID"}))
            sys.exit(1)
        result = asyncio.run(get_invoice(region, rest[0]))

    elif command == "download-invoice-pdf":
        output = _parse_flag(rest, "--output")
        if not rest:
            print(json.dumps({"error": "download-invoice-pdf requires an INVOICE_ID"}))
            sys.exit(1)
        if not output:
            print(json.dumps({"error": "download-invoice-pdf requires --output PATH"}))
            sys.exit(1)
        result = asyncio.run(download_invoice_pdf(region, rest[0], output))

    elif command == "list-estimates":
        customer_id = _parse_flag(rest, "--customer-id")
        status = _parse_flag(rest, "--status")
        if not customer_id:
            print(json.dumps({"error": "list-estimates requires --customer-id"}))
            sys.exit(1)
        result = asyncio.run(list_estimates(region, customer_id, status=status))

    elif command == "get-estimate":
        if not rest:
            print(json.dumps({"error": "get-estimate requires an ESTIMATE_ID"}))
            sys.exit(1)
        result = asyncio.run(get_estimate(region, rest[0]))

    elif command == "download-estimate-pdf":
        output = _parse_flag(rest, "--output")
        if not rest:
            print(json.dumps({"error": "download-estimate-pdf requires an ESTIMATE_ID"}))
            sys.exit(1)
        if not output:
            print(json.dumps({"error": "download-estimate-pdf requires --output PATH"}))
            sys.exit(1)
        result = asyncio.run(download_estimate_pdf(region, rest[0], output))

    elif command == "list-credit-notes":
        customer_id = _parse_flag(rest, "--customer-id")
        if not customer_id:
            print(json.dumps({"error": "list-credit-notes requires --customer-id"}))
            sys.exit(1)
        result = asyncio.run(list_credit_notes(region, customer_id))

    elif command == "get-credit-note":
        if not rest:
            print(json.dumps({"error": "get-credit-note requires a CREDITNOTE_ID"}))
            sys.exit(1)
        result = asyncio.run(get_credit_note(region, rest[0]))

    elif command == "list-payments":
        customer_id = _parse_flag(rest, "--customer-id")
        if not customer_id:
            print(json.dumps({"error": "list-payments requires --customer-id"}))
            sys.exit(1)
        result = asyncio.run(list_payments(region, customer_id))

    elif command == "get-payment":
        if not rest:
            print(json.dumps({"error": "get-payment requires a PAYMENT_ID"}))
            sys.exit(1)
        result = asyncio.run(get_payment(region, rest[0]))

    elif command == "list-recurring-invoices":
        customer_id = _parse_flag(rest, "--customer-id")
        if not customer_id:
            print(json.dumps({"error": "list-recurring-invoices requires --customer-id"}))
            sys.exit(1)
        result = asyncio.run(list_recurring_invoices(region, customer_id))

    elif command == "send-invoice":
        if not rest:
            print(json.dumps({"error": "send-invoice requires an INVOICE_ID"}))
            sys.exit(1)
        body_str = sys.stdin.read().strip()
        if not body_str:
            print(json.dumps({"error": "send-invoice requires JSON body on stdin"}))
            sys.exit(1)
        try:
            email_data = json.loads(body_str)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"Invalid JSON on stdin: {e}"}))
            sys.exit(1)
        result = asyncio.run(send_invoice(region, rest[0], email_data))

    elif command == "send-estimate":
        if not rest:
            print(json.dumps({"error": "send-estimate requires an ESTIMATE_ID"}))
            sys.exit(1)
        body_str = sys.stdin.read().strip()
        if not body_str:
            print(json.dumps({"error": "send-estimate requires JSON body on stdin"}))
            sys.exit(1)
        try:
            email_data = json.loads(body_str)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"Invalid JSON on stdin: {e}"}))
            sys.exit(1)
        result = asyncio.run(send_estimate(region, rest[0], email_data))

    else:
        print(json.dumps({"error": f"Unknown command: {command}. Run without arguments for usage."}))
        sys.exit(1)

    print(json.dumps(result, indent=2))
