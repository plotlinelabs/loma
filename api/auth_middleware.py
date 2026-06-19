"""Auth middleware for the Python backend.

Reads the X-User-Email header injected by the Next.js middleware
and attaches user identity + system role to the request.
"""

import logging
import os

from aiohttp import web

from api.auth_helpers import ROLE_HIERARCHY
from observability.db import get_db

logger = logging.getLogger(__name__)

# Routes that don't require authentication (webhooks use their own auth, e.g. HMAC)
_PUBLIC_PREFIXES = ("/webhooks/", "/webhook", "/health", "/api/oauth/google/callback", "/api/oauth/slack/callback")

_IS_PREVIEW = os.environ.get("PREVIEW_MODE", "").lower() in ("1", "true", "yes")
_IS_DEV = os.environ.get("ENV", "").upper() == "DEV"


def _configured_default_role() -> str:
    role = os.environ.get("PREVIEW_SYSTEM_ROLE", "").strip()
    if (_IS_PREVIEW or _IS_DEV) and role in ROLE_HIERARCHY:
        return role
    if _IS_PREVIEW:
        return "maintainer"
    if _IS_DEV:
        return "operator"
    return "chatter"


_DEFAULT_ROLE = _configured_default_role()

# Preview deployments delete the Next.js middleware (no Google OAuth), so there's
# no X-User-Email header. Use a fallback identity instead.
_PREVIEW_EMAIL = os.environ.get("PREVIEW_USER_EMAIL", "preview@example.com")


@web.middleware
async def auth_middleware(request, handler):
    """Extract user identity from X-User-Email header, attach to request."""

    # Skip auth for webhook routes and health checks
    if any(request.path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await handler(request)

    # CORS preflight requests don't carry auth headers
    if request.method == "OPTIONS":
        return await handler(request)

    # API routes: attach user identity if available (never block)
    if request.path.startswith("/api/"):
        user_email = request.headers.get("X-User-Email", "").strip()
        using_preview_fallback = False
        if not user_email:
            # Preview: no Next.js middleware → no header; use fallback identity
            # Production: rewrites() may not forward middleware-injected headers,
            # so fall back gracefully instead of blocking.
            if _IS_PREVIEW or _IS_DEV:
                user_email = _PREVIEW_EMAIL
                using_preview_fallback = True
            else:
                user_email = ""

        # Attach user email to request for downstream handlers
        request["user_email"] = user_email
        request["preview_fallback_user"] = using_preview_fallback

        # Look up user from DB to get system role
        db = get_db()
        if db is not None:
            user = await db.users.find_one({"email": user_email})
            request["user"] = user
            if using_preview_fallback:
                request["system_role"] = _DEFAULT_ROLE
            else:
                request["system_role"] = (
                    user.get("system_role", _DEFAULT_ROLE) if user else _DEFAULT_ROLE
                )
        else:
            request["user"] = None
            request["system_role"] = _DEFAULT_ROLE

    return await handler(request)
