"""Verify the existing signed Next.js human-session gateway."""
import hashlib
import hmac
import os
import time
from aiohttp import web
from api.auth_helpers import get_user_email
from observability.db import get_db


async def verify_dashboard_signature(request):
    db = get_db()
    try:
        config = await db.gateway_config.find_one({"_id": "human-session"}) if db is not None else None
    except Exception:
        raise web.HTTPServiceUnavailable(text="Gateway configuration unavailable")
    # A managed key takes precedence on both sides, including migrated deployments.
    key = config.get("secret", "") if config else os.getenv('LOMA_WORK_GATEWAY_SECRET', '')
    stamp = request.headers.get('X-Work-Time', '')
    email = get_user_email(request)
    if request.content_length and request.content_length > 100000:
        raise web.HTTPRequestEntityTooLarge(max_size=100000, actual_size=request.content_length)
    raw = await request.read()
    try:
        fresh = abs(time.time() - int(stamp)) < 60
    except ValueError:
        fresh = False
    payload = '\n'.join([stamp, request.method, request.path, email, hashlib.sha256(raw).hexdigest()])
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if len(key) < 32 or not fresh or not email or not hmac.compare_digest(signature, request.headers.get('X-Work-Signature', '')):
        raise web.HTTPUnauthorized(text='Verified dashboard session required')
    return email, raw
