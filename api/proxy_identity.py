"""Short-lived, purpose-bound identity assertions from trusted HTTP proxies."""
import hashlib
import hmac
import os
import time

DOMAIN = 'loma-proxy-identity-v1'
MAX_AGE = 60


def _key() -> bytes:
    value = os.environ.get('BACKEND_PROXY_SECRET') or os.environ.get('OAUTH_ENCRYPTION_KEY', '')
    if len(value) < 32:
        raise RuntimeError('Backend proxy authentication is not configured')
    return value.encode()


def sign_identity(email: str, timestamp: str) -> str:
    if not email or any(c in email for c in '\r\n\0'):
        raise ValueError('Invalid identity')
    payload = f'{DOMAIN}\n{timestamp}\n{email}'.encode()
    return hmac.new(_key(), payload, hashlib.sha256).hexdigest()


def verify_identity(email: str, timestamp: str, signature: str) -> bool:
    # Require configuration even on invalid requests; callers can return 503.
    _key()
    try:
        if not timestamp.isascii() or not timestamp.isdecimal() or len(timestamp) != 10:
            return False
        age = time.time() - int(timestamp)
        if not 0 <= age <= MAX_AGE or len(signature) != 64:
            return False
        return hmac.compare_digest(sign_identity(email, timestamp), signature)
    except (ValueError, TypeError):
        return False
