"""Apollo.io API tools — full API coverage.

Provides CLI commands for the Loma agent across all Apollo API categories:
  - People: search, enrich, bulk enrich
  - Organizations: search, enrich, bulk enrich, get info, job postings
  - News: search news articles
  - Contacts: create, bulk create, bulk update, view, update, search,
              update stage, update owner, stages, lists
  - Deals: create, view, update, list, stages
  - Sequences: search, add contacts, update touch
  - Tasks: create, search
  - Calls: create, search, update
  - Emails: search outreach emails, email account stats
  - Custom Fields: create, list
  - Users: list users
  - Email Accounts: list email accounts
  - API: usage stats, health check

Requires APOLLO_API_KEY environment variable.
API docs: https://docs.apollo.io/reference/authentication

Usage (called by the agent via Bash):
  python3 tools/apollo.py search --title "VP Engineering" --location "San Francisco"
  python3 tools/apollo.py enrich --linkedin_url "https://www.linkedin.com/in/john-doe/"
  python3 tools/apollo.py org-search --keywords "fintech" --location "Singapore"
  python3 tools/apollo.py contacts-search --keywords "product manager"
  python3 tools/apollo.py news-search --keywords "Series B funding"
  python3 tools/apollo.py api-health
  ... (see --help for all commands)
"""

import asyncio
import json
import os
import sys
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

APOLLO_BASE_URL = "https://api.apollo.io/api/v1"


def _get_api_key() -> str:
    key = os.environ.get("APOLLO_API_KEY", "")
    if not key:
        raise ValueError(
            "APOLLO_API_KEY environment variable is not set. "
            "Please configure it before using Apollo.io tools."
        )
    return key


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": _get_api_key(),
    }


async def _api_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Generic API request handler with error handling."""
    url = f"{APOLLO_BASE_URL}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            kwargs: dict[str, Any] = {
                "headers": _headers(),
                "timeout": aiohttp.ClientTimeout(total=timeout),
            }
            if body is not None:
                kwargs["json"] = body

            http_method = getattr(session, method.lower())
            async with http_method(url, **kwargs) as resp:
                if resp.status == 429:
                    return {"error": "Apollo.io rate limit reached. Please wait and try again."}
                if resp.status == 401:
                    return {"error": "Apollo.io API key is invalid or expired."}
                if resp.status not in (200, 201):
                    error_text = await resp.text()
                    return {"error": f"Apollo.io API error (HTTP {resp.status}): {error_text[:500]}"}
                data = await resp.json()
                return data
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to Apollo.io API: {e}"}


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_person(person: dict[str, Any]) -> dict[str, Any]:
    """Format a person record into a clean dict."""
    org = person.get("organization", {}) or {}
    return {
        "name": person.get("name") or "Unknown",
        "title": person.get("title") or "N/A",
        "company": org.get("name") or person.get("organization_name") or "N/A",
        "location": ", ".join(
            p for p in [person.get("city"), person.get("state"), person.get("country")] if p
        ) or "N/A",
        "headline": person.get("headline") or "",
        "linkedin_url": person.get("linkedin_url") or "",
        "apollo_id": person.get("id") or "",
    }


def _format_enriched_person(person: dict[str, Any]) -> dict[str, Any]:
    """Format an enriched person record with contact details."""
    org = person.get("organization", {}) or {}
    phone = ""
    phone_numbers = person.get("phone_numbers") or []
    if phone_numbers:
        phone = phone_numbers[0].get("sanitized_number", "") or phone_numbers[0].get("raw_number", "")

    result: dict[str, Any] = {
        "name": (person.get("name") or f"{person.get('first_name', '')} {person.get('last_name', '')}").strip() or "Unknown",
        "title": person.get("title") or "N/A",
        "company": org.get("name") or person.get("organization_name") or "N/A",
        "email": person.get("email") or "Not available",
        "phone": phone or "Not available",
        "linkedin_url": person.get("linkedin_url") or "Not available",
        "location": ", ".join(
            p for p in [person.get("city"), person.get("state"), person.get("country")] if p
        ) or "N/A",
        "seniority": person.get("seniority") or "N/A",
    }

    if org.get("estimated_num_employees"):
        result["company_size"] = f"{org['estimated_num_employees']} employees"
    if org.get("industry"):
        result["industry"] = org["industry"]
    if org.get("primary_domain") or org.get("website_url"):
        result["company_domain"] = org.get("primary_domain") or org.get("website_url")

    return result


def _format_organization(org: dict[str, Any]) -> dict[str, Any]:
    """Format an organization record."""
    return {
        "name": org.get("name") or "Unknown",
        "domain": org.get("primary_domain") or org.get("website_url") or "N/A",
        "industry": org.get("industry") or "N/A",
        "estimated_num_employees": org.get("estimated_num_employees") or "N/A",
        "location": ", ".join(
            p for p in [org.get("city"), org.get("state"), org.get("country")] if p
        ) or "N/A",
        "linkedin_url": org.get("linkedin_url") or "",
        "founded_year": org.get("founded_year") or "N/A",
        "apollo_id": org.get("id") or "",
    }


def _format_contact(contact: dict[str, Any]) -> dict[str, Any]:
    """Format a contact record."""
    return {
        "id": contact.get("id") or "",
        "name": f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip() or "Unknown",
        "title": contact.get("title") or "N/A",
        "email": contact.get("email") or "N/A",
        "organization_name": contact.get("organization_name") or "N/A",
        "owner_id": contact.get("owner_id") or "",
        "contact_stage_id": contact.get("contact_stage_id") or "",
        "created_at": contact.get("created_at") or "",
    }


def _format_deal(deal: dict[str, Any]) -> dict[str, Any]:
    """Format a deal/opportunity record."""
    return {
        "id": deal.get("id") or "",
        "name": deal.get("name") or "Unknown",
        "amount": deal.get("amount") or 0,
        "currency": deal.get("currency") or "USD",
        "opportunity_stage_id": deal.get("opportunity_stage_id") or deal.get("deal_stage_id") or "",
        "owner_id": deal.get("owner_id") or "",
        "account_id": deal.get("account_id") or "",
        "status": deal.get("status") or "N/A",
        "closed_date": deal.get("closed_date") or "",
        "created_at": deal.get("created_at") or "",
    }


# ---------------------------------------------------------------------------
# People API
# ---------------------------------------------------------------------------

async def people_search(
    person_titles: list[str] | None = None,
    person_seniorities: list[str] | None = None,
    person_locations: list[str] | None = None,
    q_organization_domains: list[str] | None = None,
    organization_num_employees_ranges: list[str] | None = None,
    q_keywords: str | None = None,
    page: int = 1,
    per_page: int = 25,
) -> dict[str, Any]:
    """Search Apollo.io for people matching the given filters."""
    body: dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}

    if person_titles:
        body["person_titles"] = person_titles
    if person_seniorities:
        body["person_seniorities"] = person_seniorities
    if person_locations:
        body["person_locations"] = person_locations
    if q_organization_domains:
        body["q_organization_domains"] = "\n".join(q_organization_domains)
    if organization_num_employees_ranges:
        body["organization_num_employees_ranges"] = organization_num_employees_ranges
    if q_keywords:
        body["q_keywords"] = q_keywords

    data = await _api_request("post", "/mixed_people/search", body)
    if "error" in data:
        return data

    people = data.get("people", [])
    pagination = data.get("pagination", {})

    if not people:
        return {"error": "No people found matching the given filters. Try broadening your search."}

    return {
        "total": pagination.get("total_entries", 0),
        "page": pagination.get("page", 1),
        "total_pages": pagination.get("total_pages", 1),
        "people": [_format_person(p) for p in people],
    }


async def people_enrich(
    apollo_id: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    organization_name: str | None = None,
    domain: str | None = None,
    linkedin_url: str | None = None,
    reveal_personal_emails: bool = False,
    reveal_phone_number: bool = False,
) -> dict[str, Any]:
    """Enrich a person with contact details from Apollo.io."""
    body: dict[str, Any] = {}

    if apollo_id:
        body["id"] = apollo_id
    if first_name:
        body["first_name"] = first_name
    if last_name:
        body["last_name"] = last_name
    if email:
        body["email"] = email
    if organization_name:
        body["organization_name"] = organization_name
    if domain:
        body["domain"] = domain
    if linkedin_url:
        body["linkedin_url"] = linkedin_url
    if reveal_personal_emails:
        body["reveal_personal_emails"] = True
    if reveal_phone_number:
        body["reveal_phone_number"] = True

    identifiers = ["id", "email", "linkedin_url", "first_name"]
    if not any(body.get(k) for k in identifiers):
        return {
            "error": "Please provide at least one identifier: apollo_id, email, linkedin_url, or first_name + last_name + organization_name"
        }

    data = await _api_request("post", "/people/match", body)
    if "error" in data:
        return data

    person = data.get("person")
    if not person:
        return {"error": "No matching person found. Try providing more details (full name + company, or LinkedIn URL)."}

    return _format_enriched_person(person)


async def bulk_people_enrich(
    details: list[dict[str, Any]],
    reveal_personal_emails: bool = False,
    reveal_phone_number: bool = False,
) -> dict[str, Any]:
    """Bulk enrich up to 10 people with contact details.

    Each item in details should have at least one of:
      - id (apollo_id), email, linkedin_url, first_name+last_name+organization_name
    """
    if len(details) > 10:
        return {"error": "Bulk enrichment supports a maximum of 10 people per request."}

    body: dict[str, Any] = {
        "details": details,
        "reveal_personal_emails": reveal_personal_emails,
        "reveal_phone_number": reveal_phone_number,
    }

    data = await _api_request("post", "/people/bulk_match", body)
    if "error" in data:
        return data

    matches = data.get("matches", [])
    return {
        "total": len(matches),
        "people": [_format_enriched_person(m) for m in matches if m],
    }


# ---------------------------------------------------------------------------
# Organization API
# ---------------------------------------------------------------------------

async def organization_search(
    q_organization_domains: list[str] | None = None,
    organization_locations: list[str] | None = None,
    organization_num_employees_ranges: list[str] | None = None,
    organization_ids: list[str] | None = None,
    q_keywords: str | None = None,
    page: int = 1,
    per_page: int = 25,
) -> dict[str, Any]:
    """Search Apollo.io for organizations matching filters."""
    body: dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}

    if q_organization_domains:
        body["q_organization_domains"] = "\n".join(q_organization_domains)
    if organization_locations:
        body["organization_locations"] = organization_locations
    if organization_num_employees_ranges:
        body["organization_num_employees_ranges"] = organization_num_employees_ranges
    if organization_ids:
        body["organization_ids"] = organization_ids
    if q_keywords:
        body["q_keywords"] = q_keywords

    data = await _api_request("post", "/mixed_companies/search", body)
    if "error" in data:
        return data

    orgs = data.get("organizations", [])
    pagination = data.get("pagination", {})

    if not orgs:
        return {"error": "No organizations found matching the given filters."}

    return {
        "total": pagination.get("total_entries", 0),
        "page": pagination.get("page", 1),
        "total_pages": pagination.get("total_pages", 1),
        "organizations": [_format_organization(o) for o in orgs],
    }


async def organization_enrich(domain: str) -> dict[str, Any]:
    """Enrich a single organization by domain."""
    body: dict[str, Any] = {"domain": domain}
    data = await _api_request("post", "/organizations/enrich", body)
    if "error" in data:
        return data

    org = data.get("organization")
    if not org:
        return {"error": f"No organization found for domain: {domain}"}

    return _format_organization(org)


async def bulk_organization_enrich(domains: list[str]) -> dict[str, Any]:
    """Bulk enrich up to 10 organizations by domain."""
    if len(domains) > 10:
        return {"error": "Bulk organization enrichment supports max 10 domains per request."}

    body = {"domains": domains}
    data = await _api_request("post", "/organizations/bulk_enrich", body)
    if "error" in data:
        return data

    orgs = data.get("organizations", [])
    return {
        "total": len(orgs),
        "organizations": [_format_organization(o) for o in orgs if o],
    }


async def get_organization_info(organization_id: str) -> dict[str, Any]:
    """Get complete info for a specific organization by ID."""
    data = await _api_request("get", f"/organizations/{organization_id}")
    if "error" in data:
        return data

    org = data.get("organization")
    if not org:
        return {"error": f"Organization not found: {organization_id}"}

    return _format_organization(org)


async def get_organization_job_postings(organization_id: str) -> dict[str, Any]:
    """Get job postings for a specific organization."""
    data = await _api_request(
        "get", f"/organizations/job_postings?organization_id={organization_id}"
    )
    if "error" in data:
        return data

    job_postings = data.get("job_postings", [])
    return {
        "total": len(job_postings),
        "job_postings": [
            {
                "id": jp.get("id") or "",
                "title": jp.get("title") or "N/A",
                "url": jp.get("url") or "",
                "city": jp.get("city") or "N/A",
                "state": jp.get("state") or "N/A",
                "country": jp.get("country") or "N/A",
                "posted_at": jp.get("posted_at") or "",
                "description": (jp.get("description") or "")[:500],
            }
            for jp in job_postings
        ],
    }


# ---------------------------------------------------------------------------
# News API
# ---------------------------------------------------------------------------

async def search_news_articles(
    q_keywords: str | None = None,
    organization_ids: list[str] | None = None,
    page: int = 1,
    per_page: int = 25,
    **extra_filters: Any,
) -> dict[str, Any]:
    """Search for news articles in Apollo."""
    body: dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}
    if q_keywords:
        body["q_keywords"] = q_keywords
    if organization_ids:
        body["organization_ids"] = organization_ids
    body.update(extra_filters)

    data = await _api_request("post", "/news_articles/search", body)
    if "error" in data:
        return data

    articles = data.get("news_articles", [])
    pagination = data.get("pagination", {})

    return {
        "total": pagination.get("total_entries", 0),
        "page": pagination.get("page", 1),
        "total_pages": pagination.get("total_pages", 1),
        "news_articles": [
            {
                "id": a.get("id") or "",
                "title": a.get("title") or "N/A",
                "url": a.get("url") or "",
                "source": a.get("source") or "N/A",
                "published_at": a.get("published_at") or "",
                "snippet": (a.get("snippet") or a.get("description") or "")[:500],
                "organization_id": a.get("organization_id") or "",
            }
            for a in articles
        ],
    }


# ---------------------------------------------------------------------------
# Contacts API
# ---------------------------------------------------------------------------

async def create_contact(
    first_name: str,
    last_name: str,
    email: str | None = None,
    title: str | None = None,
    organization_name: str | None = None,
    account_id: str | None = None,
    owner_id: str | None = None,
    label_names: list[str] | None = None,
    **extra_fields: Any,
) -> dict[str, Any]:
    """Create a new contact in your Apollo account."""
    body: dict[str, Any] = {
        "first_name": first_name,
        "last_name": last_name,
    }
    if email:
        body["email"] = email
    if title:
        body["title"] = title
    if organization_name:
        body["organization_name"] = organization_name
    if account_id:
        body["account_id"] = account_id
    if owner_id:
        body["owner_id"] = owner_id
    if label_names:
        body["label_names"] = label_names
    body.update(extra_fields)

    data = await _api_request("post", "/contacts", body)
    if "error" in data:
        return data

    contact = data.get("contact", data)
    return _format_contact(contact)


async def bulk_create_contacts(contacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Bulk create up to 100 contacts."""
    if len(contacts) > 100:
        return {"error": "Bulk create supports max 100 contacts per request."}

    body = {"contacts": contacts}
    data = await _api_request("post", "/contacts/bulk_create", body)
    if "error" in data:
        return data

    created = data.get("contacts", [])
    return {
        "total_created": len(created),
        "contacts": [_format_contact(c) for c in created],
    }


async def bulk_update_contacts(
    contact_ids: list[str],
    **fields: Any,
) -> dict[str, Any]:
    """Bulk update contacts. Pass fields to update as keyword arguments.

    Common fields: contact_stage_id, owner_id, label_names, etc.
    """
    if not contact_ids:
        return {"error": "contact_ids list is required."}
    if not fields:
        return {"error": "No fields provided to update."}

    body: dict[str, Any] = {"contact_ids": contact_ids}
    body.update(fields)

    data = await _api_request("post", "/contacts/bulk_update", body)
    if "error" in data:
        return data

    contacts = data.get("contacts", [])
    return {
        "total_updated": len(contacts),
        "contacts": [_format_contact(c) for c in contacts],
    }


async def view_contact(contact_id: str) -> dict[str, Any]:
    """View a specific contact by ID."""
    data = await _api_request("get", f"/contacts/{contact_id}")
    if "error" in data:
        return data

    contact = data.get("contact", data)
    return _format_contact(contact)


async def update_contact(contact_id: str, **fields: Any) -> dict[str, Any]:
    """Update an existing contact."""
    if not fields:
        return {"error": "No fields provided to update."}

    data = await _api_request("put", f"/contacts/{contact_id}", fields)
    if "error" in data:
        return data

    contact = data.get("contact", data)
    return _format_contact(contact)


async def update_contact_stage(contact_id: str, contact_stage_id: str) -> dict[str, Any]:
    """Update the stage for a single contact."""
    body = {"contact_stage_id": contact_stage_id}
    data = await _api_request("put", f"/contacts/{contact_id}/stage", body)
    if "error" in data:
        return data

    contact = data.get("contact", data)
    return _format_contact(contact)


async def update_contact_owner(contact_id: str, owner_id: str) -> dict[str, Any]:
    """Update the owner for a single contact."""
    body = {"owner_id": owner_id}
    data = await _api_request("put", f"/contacts/{contact_id}/owner", body)
    if "error" in data:
        return data

    contact = data.get("contact", data)
    return _format_contact(contact)


async def search_contacts(
    q_keywords: str | None = None,
    contact_stage_ids: list[str] | None = None,
    sort_by_field: str | None = None,
    sort_ascending: bool = False,
    page: int = 1,
    per_page: int = 25,
    **extra_filters: Any,
) -> dict[str, Any]:
    """Search for contacts in your Apollo account."""
    body: dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}
    if q_keywords:
        body["q_keywords"] = q_keywords
    if contact_stage_ids:
        body["contact_stage_ids"] = contact_stage_ids
    if sort_by_field:
        body["sort_by_field"] = sort_by_field
        body["sort_ascending"] = sort_ascending
    body.update(extra_filters)

    data = await _api_request("post", "/contacts/search", body)
    if "error" in data:
        return data

    contacts = data.get("contacts", [])
    pagination = data.get("pagination", {})

    return {
        "total": pagination.get("total_entries", 0),
        "page": pagination.get("page", 1),
        "total_pages": pagination.get("total_pages", 1),
        "contacts": [_format_contact(c) for c in contacts],
    }


async def list_contact_stages() -> dict[str, Any]:
    """List all contact stages in your Apollo account."""
    data = await _api_request("get", "/contact_stages")
    if "error" in data:
        return data

    stages = data.get("contact_stages", [])
    return {
        "stages": [
            {"id": s.get("id"), "name": s.get("name"), "order": s.get("order")}
            for s in stages
        ]
    }


async def list_contact_lists() -> dict[str, Any]:
    """List all contact lists in your Apollo account."""
    data = await _api_request("get", "/contact_lists")
    if "error" in data:
        return data

    lists = data.get("contact_lists", [])
    return {
        "contact_lists": [
            {
                "id": cl.get("id") or "",
                "name": cl.get("name") or "Unknown",
                "cached_count": cl.get("cached_count") or 0,
                "created_at": cl.get("created_at") or "",
            }
            for cl in lists
        ]
    }


# ---------------------------------------------------------------------------
# Deals API (Apollo /opportunities endpoints)
# ---------------------------------------------------------------------------

async def create_deal(
    name: str,
    amount: float,
    opportunity_stage_id: str,
    account_id: str | None = None,
    owner_id: str | None = None,
    closed_date: str | None = None,
    **extra_fields: Any,
) -> dict[str, Any]:
    """Create a new deal (opportunity).

    Required fields: name, amount, opportunity_stage_id.
    """
    body: dict[str, Any] = {
        "name": name,
        "amount": amount,
        "opportunity_stage_id": opportunity_stage_id,
    }
    if account_id:
        body["account_id"] = account_id
    if owner_id:
        body["owner_id"] = owner_id
    if closed_date:
        body["closed_date"] = closed_date
    body.update(extra_fields)

    data = await _api_request("post", "/opportunities", body)
    if "error" in data:
        return data

    deal = data.get("opportunity", data)
    return _format_deal(deal)


async def list_deals(
    page: int = 1,
    per_page: int = 25,
) -> dict[str, Any]:
    """List all deals (opportunities) in your Apollo account."""
    params = f"?page={page}&per_page={min(per_page, 100)}"
    data = await _api_request("get", f"/opportunities{params}")
    if "error" in data:
        return data

    deals = data.get("opportunities", [])
    pagination = data.get("pagination", {})

    return {
        "total": pagination.get("total_entries", 0),
        "page": pagination.get("page", 1),
        "total_pages": pagination.get("total_pages", 1),
        "deals": [_format_deal(d) for d in deals],
    }


async def get_deal(deal_id: str) -> dict[str, Any]:
    """Get a specific deal (opportunity) by ID."""
    data = await _api_request("get", f"/opportunities/{deal_id}")
    if "error" in data:
        return data

    deal = data.get("opportunity", data)
    return _format_deal(deal)


async def update_deal(deal_id: str, **fields: Any) -> dict[str, Any]:
    """Update an existing deal (opportunity)."""
    if not fields:
        return {"error": "No fields provided to update."}

    data = await _api_request("put", f"/opportunities/{deal_id}", fields)
    if "error" in data:
        return data

    deal = data.get("opportunity", data)
    return _format_deal(deal)


async def list_deal_stages() -> dict[str, Any]:
    """List all deal (opportunity) stages."""
    data = await _api_request("get", "/opportunity_stages")
    if "error" in data:
        return data

    stages = data.get("opportunity_stages", [])
    return {
        "stages": [
            {"id": s.get("id"), "name": s.get("name"), "order": s.get("order")}
            for s in stages
        ]
    }


# ---------------------------------------------------------------------------
# Sequences API
# ---------------------------------------------------------------------------

async def search_sequences(
    q_keywords: str | None = None,
    page: int = 1,
    per_page: int = 25,
) -> dict[str, Any]:
    """Search for sequences in your Apollo account."""
    body: dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}
    if q_keywords:
        body["q_keywords"] = q_keywords

    data = await _api_request("post", "/emailer_campaigns/search", body)
    if "error" in data:
        return data

    sequences = data.get("emailer_campaigns", [])
    pagination = data.get("pagination", {})

    return {
        "total": pagination.get("total_entries", 0),
        "page": pagination.get("page", 1),
        "total_pages": pagination.get("total_pages", 1),
        "sequences": [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "active": s.get("active"),
                "created_at": s.get("created_at"),
                "num_steps": s.get("num_steps"),
                "user_id": s.get("user_id"),
            }
            for s in sequences
        ],
    }


async def add_contacts_to_sequence(
    sequence_id: str,
    contact_ids: list[str],
    emailer_campaign_id: str | None = None,
    send_email_from_email_account_id: str | None = None,
    sequence_active_in_other_campaigns: bool = False,
) -> dict[str, Any]:
    """Add contacts to an existing sequence."""
    body: dict[str, Any] = {
        "contact_ids": contact_ids,
        "emailer_campaign_id": emailer_campaign_id or sequence_id,
        "sequence_active_in_other_campaigns": sequence_active_in_other_campaigns,
    }
    if send_email_from_email_account_id:
        body["send_email_from_email_account_id"] = send_email_from_email_account_id

    return await _api_request(
        "post", f"/emailer_campaigns/{sequence_id}/add_contact_ids", body
    )


async def update_sequence_touch(
    campaign_id: str,
    touch_id: str,
    status: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Update a touch (step) in a sequence.

    campaign_id: The sequence/campaign ID.
    touch_id: The emailer touch ID within the campaign.
    status: e.g. "active", "paused", etc.
    Additional fields can be passed as keyword arguments.
    """
    body: dict[str, Any] = {}
    if status:
        body["status"] = status
    body.update(fields)

    if not body:
        return {"error": "No fields provided to update."}

    return await _api_request(
        "put",
        f"/emailer_campaigns/{campaign_id}/emailer_touches/{touch_id}",
        body,
    )


# ---------------------------------------------------------------------------
# Tasks API
# ---------------------------------------------------------------------------

async def create_task(
    contact_id: str | None = None,
    account_id: str | None = None,
    user_id: str | None = None,
    type: str = "action_item",
    priority: str = "medium",
    due_date: str | None = None,
    note: str | None = None,
    **extra_fields: Any,
) -> dict[str, Any]:
    """Create a task in Apollo.

    type: one of action_item, call, email
    priority: one of low, medium, high
    """
    body: dict[str, Any] = {
        "type": type,
        "priority": priority,
    }
    if contact_id:
        body["contact_id"] = contact_id
    if account_id:
        body["account_id"] = account_id
    if user_id:
        body["user_id"] = user_id
    if due_date:
        body["due_date"] = due_date
    if note:
        body["note"] = note
    body.update(extra_fields)

    data = await _api_request("post", "/tasks", body)
    if "error" in data:
        return data

    task = data.get("task", data)
    return {
        "id": task.get("id"),
        "type": task.get("type"),
        "priority": task.get("priority"),
        "status": task.get("status"),
        "due_date": task.get("due_date"),
        "note": task.get("note"),
        "created_at": task.get("created_at"),
    }


async def search_tasks(
    q_keywords: str | None = None,
    page: int = 1,
    per_page: int = 25,
    **extra_filters: Any,
) -> dict[str, Any]:
    """Search for tasks in your Apollo account."""
    body: dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}
    if q_keywords:
        body["q_keywords"] = q_keywords
    body.update(extra_filters)

    data = await _api_request("post", "/tasks/search", body)
    if "error" in data:
        return data

    tasks = data.get("tasks", [])
    pagination = data.get("pagination", {})

    return {
        "total": pagination.get("total_entries", 0),
        "page": pagination.get("page", 1),
        "total_pages": pagination.get("total_pages", 1),
        "tasks": [
            {
                "id": t.get("id"),
                "type": t.get("type"),
                "priority": t.get("priority"),
                "status": t.get("status"),
                "due_date": t.get("due_date"),
                "note": t.get("note"),
            }
            for t in tasks
        ],
    }


# ---------------------------------------------------------------------------
# Calls API
# ---------------------------------------------------------------------------

async def create_call_record(
    contact_id: str | None = None,
    account_id: str | None = None,
    user_id: str | None = None,
    disposition: str | None = None,
    duration: int | None = None,
    direction: str = "outbound",
    note: str | None = None,
    **extra_fields: Any,
) -> dict[str, Any]:
    """Log a call record in Apollo (for calls made outside Apollo)."""
    body: dict[str, Any] = {"direction": direction}
    if contact_id:
        body["contact_id"] = contact_id
    if account_id:
        body["account_id"] = account_id
    if user_id:
        body["user_id"] = user_id
    if disposition:
        body["disposition"] = disposition
    if duration is not None:
        body["duration"] = duration
    if note:
        body["note"] = note
    body.update(extra_fields)

    return await _api_request("post", "/calls", body)


async def update_call_record(**fields: Any) -> dict[str, Any]:
    """Update call records.

    The Apollo PUT /calls endpoint accepts the update payload directly.
    Pass the call data fields (including 'id') as keyword arguments.
    """
    if not fields:
        return {"error": "No fields provided to update."}
    return await _api_request("put", "/calls", fields)


async def search_calls(
    page: int = 1,
    per_page: int = 25,
    **extra_filters: Any,
) -> dict[str, Any]:
    """Search for call records."""
    body: dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}
    body.update(extra_filters)

    data = await _api_request("post", "/calls/search", body)
    if "error" in data:
        return data

    calls = data.get("calls", [])
    pagination = data.get("pagination", {})

    return {
        "total": pagination.get("total_entries", 0),
        "page": pagination.get("page", 1),
        "total_pages": pagination.get("total_pages", 1),
        "calls": calls,
    }


# ---------------------------------------------------------------------------
# Emails API
# ---------------------------------------------------------------------------

async def search_outreach_emails(
    page: int = 1,
    per_page: int = 25,
    **extra_filters: Any,
) -> dict[str, Any]:
    """Search for outreach emails sent via Apollo sequences."""
    body: dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}
    body.update(extra_filters)

    data = await _api_request("post", "/emailer_messages/search", body)
    if "error" in data:
        return data

    messages = data.get("emailer_messages", [])
    pagination = data.get("pagination", {})

    return {
        "total": pagination.get("total_entries", 0),
        "page": pagination.get("page", 1),
        "total_pages": pagination.get("total_pages", 1),
        "emails": [
            {
                "id": m.get("id"),
                "subject": m.get("subject"),
                "to": m.get("to"),
                "status": m.get("status"),
                "opened_at": m.get("opened_at"),
                "clicked_at": m.get("clicked_at"),
                "replied_at": m.get("replied_at"),
                "sent_at": m.get("sent_at"),
            }
            for m in messages
        ],
    }


async def get_email_account_stats(email_account_id: str) -> dict[str, Any]:
    """Get stats for a specific email account."""
    data = await _api_request(
        "get", f"/email_accounts/{email_account_id}/email_account_stats"
    )
    if "error" in data:
        return data

    return data


async def list_email_accounts() -> dict[str, Any]:
    """List all linked email accounts."""
    data = await _api_request("get", "/email_accounts")
    if "error" in data:
        return data

    accounts = data.get("email_accounts", [])
    return {
        "email_accounts": [
            {
                "id": a.get("id"),
                "email": a.get("email"),
                "type": a.get("type"),
                "active": a.get("active"),
                "user_id": a.get("user_id"),
            }
            for a in accounts
        ]
    }


# ---------------------------------------------------------------------------
# Custom Fields API
# ---------------------------------------------------------------------------

async def list_custom_fields() -> dict[str, Any]:
    """List all custom fields in your Apollo account."""
    data = await _api_request("get", "/typed_custom_fields")
    if "error" in data:
        return data

    fields = data.get("typed_custom_fields", [])
    return {
        "custom_fields": [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "field_type": f.get("field_type"),
                "entity_type": f.get("entity_type"),
            }
            for f in fields
        ]
    }


async def create_custom_field(
    name: str,
    field_type: str = "text",
    entity_type: str = "contact",
) -> dict[str, Any]:
    """Create a new custom field.

    field_type: text, number, date, dropdown, etc.
    entity_type: contact, account, or deal
    """
    body = {
        "name": name,
        "field_type": field_type,
        "entity_type": entity_type,
    }
    return await _api_request("post", "/typed_custom_fields", body)


# ---------------------------------------------------------------------------
# Users & Email Accounts API
# ---------------------------------------------------------------------------

async def list_users() -> dict[str, Any]:
    """List all users (teammates) in your Apollo account."""
    data = await _api_request("get", "/users")
    if "error" in data:
        return data

    users = data.get("users", [])
    return {
        "users": [
            {
                "id": u.get("id"),
                "name": f"{u.get('first_name', '')} {u.get('last_name', '')}".strip(),
                "email": u.get("email"),
                "role": u.get("role"),
            }
            for u in users
        ]
    }


# ---------------------------------------------------------------------------
# API Usage & Health
# ---------------------------------------------------------------------------

async def view_api_usage() -> dict[str, Any]:
    """View API usage stats and rate limits."""
    return await _api_request("get", "/usage")


async def api_health_check() -> dict[str, Any]:
    """Check the health of the Apollo API and validate credentials."""
    return await _api_request("get", "/auth/health")


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------

def _print_usage():
    commands = """Usage: python3 tools/apollo.py <command> [options]

PEOPLE:
  search                 Search Apollo's people database
    --title TITLE          Job title filter (repeatable)
    --seniority LEVEL      Seniority: senior, manager, director, vp, c_suite (repeatable)
    --location LOC         Location filter (repeatable)
    --domain DOMAIN        Company domain filter (repeatable)
    --company_size RANGE   Employee range: e.g., 51,200 (repeatable)
    --keywords TEXT        Keyword search
    --page N               Page number (default 1)
    --per_page N           Results per page (default 25, max 100)

  enrich                 Enrich a person with contact details
    --apollo_id ID         Apollo person ID
    --name 'First Last'    Full name
    --email EMAIL          Email address
    --company NAME         Company name
    --domain DOMAIN        Company domain
    --linkedin_url URL     LinkedIn profile URL
    --reveal_emails        Include personal emails
    --reveal_phone         Include phone numbers

  bulk-enrich            Bulk enrich people (provide JSON via --data)
    --data JSON            JSON array of person identifiers
    --reveal_emails        Include personal emails
    --reveal_phone         Include phone numbers

ORGANIZATIONS:
  org-search             Search for organizations
    --domain DOMAIN        Company domain (repeatable)
    --location LOC         Location (repeatable)
    --company_size RANGE   Employee range (repeatable)
    --keywords TEXT        Keyword search
    --page N / --per_page N

  org-enrich             Enrich an organization by domain
    --domain DOMAIN        Company domain (required)

  org-bulk-enrich        Bulk enrich organizations
    --domain DOMAIN        Company domain (repeatable, max 10)

  org-get                Get complete organization info
    --id ID                Apollo organization ID (required)

  org-job-postings       Get job postings for an organization
    --id ID                Apollo organization ID (required)

NEWS:
  news-search            Search news articles
    --keywords TEXT        Keyword search
    --org_ids ID           Organization IDs (repeatable)
    --page N / --per_page N

CONTACTS:
  contacts-create        Create a contact
    --first_name NAME      First name (required)
    --last_name NAME       Last name (required)
    --email EMAIL          Email
    --title TITLE          Job title
    --company NAME         Organization name
    --account_id ID        Apollo account ID
    --owner_id ID          Owner user ID

  contacts-get           View a contact
    --id ID                Contact ID (required)

  contacts-update        Update a contact
    --id ID                Contact ID (required)
    --data JSON            JSON object of fields to update

  contacts-search        Search contacts
    --keywords TEXT        Search keywords
    --page N / --per_page N

  contacts-bulk-create   Bulk create contacts
    --data JSON            JSON array of contact objects

  contacts-bulk-update   Bulk update contacts
    --data JSON            JSON with contact_ids array and fields to update

  contacts-update-stage  Update contact stage
    --id ID                Contact ID (required)
    --contact_stage_id ID  Contact stage ID (required)

  contacts-update-owner  Update contact owner
    --id ID                Contact ID (required)
    --owner_id ID          Owner user ID (required)

  contact-stages         List contact stages

  contact-lists          List contact lists

DEALS:
  deals-create           Create a deal
    --name NAME            Deal name (required)
    --amount N             Deal amount (required)
    --opportunity_stage_id ID  Deal stage ID (required)
    --account_id ID        Account ID
    --owner_id ID          Owner user ID
    --closed_date DATE     Closed date (ISO format)

  deals-get              View a deal
    --id ID                Deal ID (required)

  deals-update           Update a deal
    --id ID                Deal ID (required)
    --data JSON            JSON object of fields to update

  deals-list             List all deals
    --page N / --per_page N

  deal-stages            List deal stages

SEQUENCES:
  sequences-search       Search sequences
    --keywords TEXT        Search keywords
    --page N / --per_page N

  sequences-add-contacts Add contacts to a sequence
    --sequence_id ID       Sequence ID (required)
    --contact_ids ID       Contact IDs (repeatable)
    --email_account_id ID  Email account to send from

  sequences-update-touch Update a touch (step) in a sequence
    --campaign_id ID       Sequence/campaign ID (required)
    --touch_id ID          Touch ID (required)
    --status STATUS        e.g. active, paused
    --data JSON            Additional fields as JSON

TASKS:
  tasks-create           Create a task
    --contact_id ID        Contact ID
    --user_id ID           User ID
    --type TYPE            action_item, call, email (default: action_item)
    --priority LEVEL       low, medium, high (default: medium)
    --due_date DATE        Due date (ISO format)
    --note TEXT            Task note

  tasks-search           Search tasks
    --keywords TEXT        Search keywords
    --page N / --per_page N

CALLS:
  calls-create           Create a call record
    --contact_id ID        Contact ID
    --disposition TEXT     Call disposition
    --duration N           Duration in seconds
    --direction DIR        inbound or outbound (default: outbound)
    --note TEXT            Call note

  calls-update           Update a call record
    --data JSON            JSON object of fields to update (must include 'id')

  calls-search           Search call records
    --page N / --per_page N

EMAILS:
  emails-search          Search outreach emails
    --page N / --per_page N

  email-stats            Get email account stats
    --id ID                Email account ID (required)

  email-accounts         List linked email accounts

CUSTOM FIELDS:
  custom-fields          List all custom fields
  custom-fields-create   Create a custom field
    --name NAME            Field name (required)
    --field_type TYPE      text, number, date, dropdown (default: text)
    --entity_type TYPE     contact, account, deal (default: contact)

ADMIN:
  users                  List all users/teammates
  api-usage              View API usage stats and rate limits
  api-health             Check API health and validate credentials
"""
    print(commands)
    sys.exit(1)


def _parse_repeatable(args: list[str], flag: str) -> list[str]:
    """Extract all values for a repeatable flag like --title."""
    values = []
    i = 0
    while i < len(args):
        if args[i] == flag and i + 1 < len(args):
            values.append(args[i + 1])
            i += 2
        else:
            i += 1
    return values


def _parse_single(args: list[str], flag: str, default: str | None = None) -> str | None:
    """Extract a single value for a flag."""
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
    return default


def _has_flag(args: list[str], flag: str) -> bool:
    return flag in args


def _parse_json(args: list[str], flag: str) -> Any:
    """Parse a JSON value from a flag."""
    raw = _parse_single(args, flag)
    if raw:
        return json.loads(raw)
    return None


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) < 2:
        _print_usage()

    command = sys.argv[1]
    rest = sys.argv[2:]

    result: dict[str, Any] | None = None

    # ---- People ----
    if command == "search":
        titles = _parse_repeatable(rest, "--title")
        seniorities = _parse_repeatable(rest, "--seniority")
        locations = _parse_repeatable(rest, "--location")
        domains = _parse_repeatable(rest, "--domain")
        company_sizes = _parse_repeatable(rest, "--company_size")
        keywords = _parse_single(rest, "--keywords")
        page = int(_parse_single(rest, "--page", "1"))
        per_page = int(_parse_single(rest, "--per_page", "25"))

        result = asyncio.run(people_search(
            person_titles=titles or None,
            person_seniorities=seniorities or None,
            person_locations=locations or None,
            q_organization_domains=domains or None,
            organization_num_employees_ranges=company_sizes or None,
            q_keywords=keywords,
            page=page,
            per_page=per_page,
        ))

    elif command == "enrich":
        name = _parse_single(rest, "--name")
        first_name = None
        last_name = None
        if name:
            parts = name.split(None, 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else None

        result = asyncio.run(people_enrich(
            apollo_id=_parse_single(rest, "--apollo_id"),
            first_name=first_name,
            last_name=last_name,
            email=_parse_single(rest, "--email"),
            organization_name=_parse_single(rest, "--company"),
            domain=_parse_single(rest, "--domain"),
            linkedin_url=_parse_single(rest, "--linkedin_url"),
            reveal_personal_emails=_has_flag(rest, "--reveal_emails"),
            reveal_phone_number=_has_flag(rest, "--reveal_phone"),
        ))

    elif command == "bulk-enrich":
        data = _parse_json(rest, "--data")
        if not data:
            print("Error: --data JSON is required for bulk-enrich")
            sys.exit(1)
        result = asyncio.run(bulk_people_enrich(
            details=data,
            reveal_personal_emails=_has_flag(rest, "--reveal_emails"),
            reveal_phone_number=_has_flag(rest, "--reveal_phone"),
        ))

    # ---- Organizations ----
    elif command == "org-search":
        domains = _parse_repeatable(rest, "--domain")
        locations = _parse_repeatable(rest, "--location")
        company_sizes = _parse_repeatable(rest, "--company_size")
        keywords = _parse_single(rest, "--keywords")
        page = int(_parse_single(rest, "--page", "1"))
        per_page = int(_parse_single(rest, "--per_page", "25"))

        result = asyncio.run(organization_search(
            q_organization_domains=domains or None,
            organization_locations=locations or None,
            organization_num_employees_ranges=company_sizes or None,
            q_keywords=keywords,
            page=page,
            per_page=per_page,
        ))

    elif command == "org-enrich":
        domain = _parse_single(rest, "--domain")
        if not domain:
            print("Error: --domain is required for org-enrich")
            sys.exit(1)
        result = asyncio.run(organization_enrich(domain))

    elif command == "org-bulk-enrich":
        domains = _parse_repeatable(rest, "--domain")
        if not domains:
            print("Error: at least one --domain is required for org-bulk-enrich")
            sys.exit(1)
        result = asyncio.run(bulk_organization_enrich(domains))

    elif command == "org-get":
        org_id = _parse_single(rest, "--id")
        if not org_id:
            print("Error: --id is required for org-get")
            sys.exit(1)
        result = asyncio.run(get_organization_info(org_id))

    elif command == "org-job-postings":
        org_id = _parse_single(rest, "--id")
        if not org_id:
            print("Error: --id is required for org-job-postings")
            sys.exit(1)
        result = asyncio.run(get_organization_job_postings(org_id))

    # ---- News ----
    elif command == "news-search":
        keywords = _parse_single(rest, "--keywords")
        org_ids = _parse_repeatable(rest, "--org_ids")
        page = int(_parse_single(rest, "--page", "1"))
        per_page = int(_parse_single(rest, "--per_page", "25"))
        result = asyncio.run(search_news_articles(
            q_keywords=keywords,
            organization_ids=org_ids or None,
            page=page,
            per_page=per_page,
        ))

    # ---- Contacts ----
    elif command == "contacts-create":
        # Check if --data is provided for full JSON input
        data = _parse_json(rest, "--data")
        if data:
            first_name = data.pop("first_name", None)
            last_name = data.pop("last_name", None)
            if not first_name or not last_name:
                print("Error: first_name and last_name are required in --data JSON")
                sys.exit(1)
            result = asyncio.run(create_contact(
                first_name=first_name,
                last_name=last_name,
                **data,
            ))
        else:
            first_name = _parse_single(rest, "--first_name")
            last_name = _parse_single(rest, "--last_name")
            if not first_name or not last_name:
                print("Error: --first_name and --last_name are required")
                sys.exit(1)
            result = asyncio.run(create_contact(
                first_name=first_name,
                last_name=last_name,
                email=_parse_single(rest, "--email"),
                title=_parse_single(rest, "--title"),
                organization_name=_parse_single(rest, "--company"),
                account_id=_parse_single(rest, "--account_id"),
                owner_id=_parse_single(rest, "--owner_id"),
            ))

    elif command == "contacts-bulk-create":
        data = _parse_json(rest, "--data")
        if not data:
            print("Error: --data JSON is required")
            sys.exit(1)
        result = asyncio.run(bulk_create_contacts(data))

    elif command == "contacts-bulk-update":
        data = _parse_json(rest, "--data")
        if not data:
            print("Error: --data JSON is required (must include contact_ids array)")
            sys.exit(1)
        contact_ids = data.pop("contact_ids", [])
        if not contact_ids:
            print("Error: --data JSON must include a 'contact_ids' array")
            sys.exit(1)
        result = asyncio.run(bulk_update_contacts(
            contact_ids=contact_ids,
            **data,
        ))

    elif command == "contacts-get":
        contact_id = _parse_single(rest, "--id")
        if not contact_id:
            print("Error: --id is required")
            sys.exit(1)
        result = asyncio.run(view_contact(contact_id))

    elif command == "contacts-update":
        contact_id = _parse_single(rest, "--id")
        data = _parse_json(rest, "--data")
        if not contact_id or not data:
            print("Error: --id and --data JSON are required")
            sys.exit(1)
        result = asyncio.run(update_contact(contact_id, **data))

    elif command == "contacts-search":
        keywords = _parse_single(rest, "--keywords")
        page = int(_parse_single(rest, "--page", "1"))
        per_page = int(_parse_single(rest, "--per_page", "25"))
        result = asyncio.run(search_contacts(q_keywords=keywords, page=page, per_page=per_page))

    elif command == "contacts-update-stage":
        contact_id = _parse_single(rest, "--id")
        stage_id = _parse_single(rest, "--contact_stage_id")
        if not contact_id or not stage_id:
            print("Error: --id and --contact_stage_id are required")
            sys.exit(1)
        result = asyncio.run(update_contact_stage(contact_id, stage_id))

    elif command == "contacts-update-owner":
        contact_id = _parse_single(rest, "--id")
        owner_id = _parse_single(rest, "--owner_id")
        if not contact_id or not owner_id:
            print("Error: --id and --owner_id are required")
            sys.exit(1)
        result = asyncio.run(update_contact_owner(contact_id, owner_id))

    elif command == "contact-stages":
        result = asyncio.run(list_contact_stages())

    elif command == "contact-lists":
        result = asyncio.run(list_contact_lists())

    # ---- Deals ----
    elif command == "deals-create":
        # Check if --data is provided for full JSON input
        data = _parse_json(rest, "--data")
        if data:
            name = data.pop("name", None)
            amount = data.pop("amount", None)
            opportunity_stage_id = data.pop("opportunity_stage_id", None)
            if not name or amount is None or not opportunity_stage_id:
                print("Error: name, amount, and opportunity_stage_id are required in --data JSON")
                sys.exit(1)
            result = asyncio.run(create_deal(
                name=name,
                amount=float(amount),
                opportunity_stage_id=opportunity_stage_id,
                **data,
            ))
        else:
            name = _parse_single(rest, "--name")
            amount_str = _parse_single(rest, "--amount")
            opportunity_stage_id = _parse_single(rest, "--opportunity_stage_id")
            if not name or not amount_str or not opportunity_stage_id:
                print("Error: --name, --amount, and --opportunity_stage_id are required for deals-create")
                sys.exit(1)
            result = asyncio.run(create_deal(
                name=name,
                amount=float(amount_str),
                opportunity_stage_id=opportunity_stage_id,
                account_id=_parse_single(rest, "--account_id"),
                owner_id=_parse_single(rest, "--owner_id"),
                closed_date=_parse_single(rest, "--closed_date"),
            ))

    elif command == "deals-get":
        deal_id = _parse_single(rest, "--id")
        if not deal_id:
            print("Error: --id is required")
            sys.exit(1)
        result = asyncio.run(get_deal(deal_id))

    elif command == "deals-update":
        deal_id = _parse_single(rest, "--id")
        data = _parse_json(rest, "--data")
        if not deal_id or not data:
            print("Error: --id and --data JSON are required")
            sys.exit(1)
        result = asyncio.run(update_deal(deal_id, **data))

    elif command == "deals-list":
        page = int(_parse_single(rest, "--page", "1"))
        per_page = int(_parse_single(rest, "--per_page", "25"))
        result = asyncio.run(list_deals(page=page, per_page=per_page))

    elif command == "deal-stages":
        result = asyncio.run(list_deal_stages())

    # ---- Sequences ----
    elif command == "sequences-search":
        keywords = _parse_single(rest, "--keywords")
        page = int(_parse_single(rest, "--page", "1"))
        per_page = int(_parse_single(rest, "--per_page", "25"))
        result = asyncio.run(search_sequences(q_keywords=keywords, page=page, per_page=per_page))

    elif command == "sequences-add-contacts":
        seq_id = _parse_single(rest, "--sequence_id")
        contact_ids = _parse_repeatable(rest, "--contact_ids")
        if not seq_id or not contact_ids:
            print("Error: --sequence_id and --contact_ids are required")
            sys.exit(1)
        result = asyncio.run(add_contacts_to_sequence(
            sequence_id=seq_id,
            contact_ids=contact_ids,
            send_email_from_email_account_id=_parse_single(rest, "--email_account_id"),
        ))

    elif command == "sequences-update-touch":
        campaign_id = _parse_single(rest, "--campaign_id")
        touch_id = _parse_single(rest, "--touch_id")
        if not campaign_id or not touch_id:
            print("Error: --campaign_id and --touch_id are required")
            sys.exit(1)
        status = _parse_single(rest, "--status")
        extra_data = _parse_json(rest, "--data") or {}
        result = asyncio.run(update_sequence_touch(
            campaign_id=campaign_id,
            touch_id=touch_id,
            status=status,
            **extra_data,
        ))

    # ---- Tasks ----
    elif command == "tasks-create":
        # Check if --data is provided for full JSON input
        data = _parse_json(rest, "--data")
        if data:
            result = asyncio.run(create_task(**data))
        else:
            result = asyncio.run(create_task(
                contact_id=_parse_single(rest, "--contact_id"),
                account_id=_parse_single(rest, "--account_id"),
                user_id=_parse_single(rest, "--user_id"),
                type=_parse_single(rest, "--type", "action_item"),
                priority=_parse_single(rest, "--priority", "medium"),
                due_date=_parse_single(rest, "--due_date"),
                note=_parse_single(rest, "--note"),
            ))

    elif command == "tasks-search":
        keywords = _parse_single(rest, "--keywords")
        page = int(_parse_single(rest, "--page", "1"))
        per_page = int(_parse_single(rest, "--per_page", "25"))
        result = asyncio.run(search_tasks(q_keywords=keywords, page=page, per_page=per_page))

    # ---- Calls ----
    elif command == "calls-create":
        # Check if --data is provided for full JSON input
        data = _parse_json(rest, "--data")
        if data:
            result = asyncio.run(create_call_record(**data))
        else:
            result = asyncio.run(create_call_record(
                contact_id=_parse_single(rest, "--contact_id"),
                account_id=_parse_single(rest, "--account_id"),
                user_id=_parse_single(rest, "--user_id"),
                disposition=_parse_single(rest, "--disposition"),
                duration=int(_parse_single(rest, "--duration", "0")) or None,
                direction=_parse_single(rest, "--direction", "outbound"),
                note=_parse_single(rest, "--note"),
            ))

    elif command == "calls-update":
        data = _parse_json(rest, "--data")
        if not data:
            print("Error: --data JSON is required for calls-update")
            sys.exit(1)
        result = asyncio.run(update_call_record(**data))

    elif command == "calls-search":
        page = int(_parse_single(rest, "--page", "1"))
        per_page = int(_parse_single(rest, "--per_page", "25"))
        result = asyncio.run(search_calls(page=page, per_page=per_page))

    # ---- Emails ----
    elif command == "emails-search":
        page = int(_parse_single(rest, "--page", "1"))
        per_page = int(_parse_single(rest, "--per_page", "25"))
        result = asyncio.run(search_outreach_emails(page=page, per_page=per_page))

    elif command == "email-stats":
        email_account_id = _parse_single(rest, "--id")
        if not email_account_id:
            print("Error: --id is required for email-stats")
            sys.exit(1)
        result = asyncio.run(get_email_account_stats(email_account_id))

    elif command == "email-accounts":
        result = asyncio.run(list_email_accounts())

    # ---- Custom Fields ----
    elif command == "custom-fields":
        result = asyncio.run(list_custom_fields())

    elif command == "custom-fields-create":
        name = _parse_single(rest, "--name")
        if not name:
            print("Error: --name is required")
            sys.exit(1)
        result = asyncio.run(create_custom_field(
            name=name,
            field_type=_parse_single(rest, "--field_type", "text"),
            entity_type=_parse_single(rest, "--entity_type", "contact"),
        ))

    # ---- Admin ----
    elif command == "users":
        result = asyncio.run(list_users())

    elif command == "api-usage":
        result = asyncio.run(view_api_usage())

    elif command == "api-health":
        result = asyncio.run(api_health_check())

    else:
        print(f"Unknown command: {command}")
        _print_usage()

    if result is not None:
        print(json.dumps(result, indent=2))
