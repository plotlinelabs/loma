"""User auth token helpers for personal Google tools.

Creates and verifies HMAC-signed tokens that bind a CLI tool invocation
to a specific authenticated user. This prevents one user from accessing
another user's OAuth tokens via the agent.

The token is created server-side in stream_agent() with the authenticated
user's email, then passed to CLI tools via --auth-token. The CLI tool
verifies the token matches the --user-email before proceeding.
"""

import base64
import hashlib
import hmac
import json
import os
import time


def _get_key() -> str:
    key = os.environ.get("OAUTH_ENCRYPTION_KEY", "").strip().strip("'\"")
    if not key:
        raise ValueError("OAUTH_ENCRYPTION_KEY environment variable is not set")
    return key


def create_user_auth_token(email: str) -> str:
    """Create an HMAC-signed auth token for a user.

    The token encodes the user's email + timestamp, signed with
    OAUTH_ENCRYPTION_KEY. Valid for 1 hour.
    """
    key = _get_key()
    ts = str(int(time.time()))
    payload = f"{email}:{ts}"
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token_data = json.dumps({"email": email, "ts": ts, "sig": sig})
    return base64.urlsafe_b64encode(token_data.encode()).decode()


def verify_user_auth_token(
    token: str, expected_email: str, max_age: int = 3600,
) -> bool:
    """Verify an auth token and check it matches the expected email.

    Returns True only if:
    1. The HMAC signature is valid
    2. The token email matches expected_email
    3. The token is not expired (default: 1 hour)
    """
    try:
        token_data = json.loads(base64.urlsafe_b64decode(token).decode())
    except Exception:
        return False

    email = token_data.get("email", "")
    ts = token_data.get("ts", "")
    sig = token_data.get("sig", "")

    if not email or not ts or not sig:
        return False

    # Check email matches
    if email != expected_email:
        return False

    # Check expiry
    try:
        if int(time.time()) - int(ts) > max_age:
            return False
    except ValueError:
        return False

    # Verify HMAC
    key = _get_key()
    payload = f"{email}:{ts}"
    expected_sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected_sig)
