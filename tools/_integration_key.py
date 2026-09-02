"""Shared helper: load an API key from the loma_observability.integrations collection.

CLI tools that historically read credentials from env vars can use this as a
fallback so the same credentials managed on the dashboard are available to
them without setting machine-level environment variables.

Usage in a tool:
    from tools._integration_key import get_integration_key
    key = os.environ.get("MY_ENV_VAR") or get_integration_key("my_provider")
"""

import logging
import os

logger = logging.getLogger(__name__)

_cache: dict[str, str] = {}


def get_integration_key(provider: str) -> str:
    """Load the decrypted API key for *provider* from db.integrations.

    Returns the decrypted key, or empty string on any failure (missing env vars,
    no matching doc, decryption error).  Results are cached in-process.
    """
    if provider in _cache:
        return _cache[provider]

    enc_key = os.environ.get("OAUTH_ENCRYPTION_KEY", "").strip()
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not enc_key or not uri:
        return ""

    try:
        from cryptography.fernet import Fernet
        from pymongo import MongoClient

        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        try:
            doc = client.loma_observability.integrations.find_one(
                {"provider": provider, "status": "active"},
                {"api_key_encrypted": 1, "extra_fields_encrypted": 1},
            )
        finally:
            client.close()

        if not doc or not doc.get("api_key_encrypted"):
            return ""

        f = Fernet(enc_key.encode())
        key = f.decrypt(doc["api_key_encrypted"].encode()).decode()
        _cache[provider] = key
        return key
    except Exception as e:
        logger.warning(
            "%s integration key fallback failed (%s)", provider, type(e).__name__,
        )
        return ""


def get_integration_extra(provider: str, field: str) -> str:
    """Load a decrypted extra field for *provider* from db.integrations."""
    cache_key = f"{provider}:{field}"
    if cache_key in _cache:
        return _cache[cache_key]

    enc_key = os.environ.get("OAUTH_ENCRYPTION_KEY", "").strip()
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not enc_key or not uri:
        return ""

    try:
        from cryptography.fernet import Fernet
        from pymongo import MongoClient

        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        try:
            doc = client.loma_observability.integrations.find_one(
                {"provider": provider, "status": "active"},
                {"extra_fields_encrypted": 1},
            )
        finally:
            client.close()

        if not doc:
            return ""

        extras = doc.get("extra_fields_encrypted", {})
        if not extras or field not in extras:
            return ""

        f = Fernet(enc_key.encode())
        val = f.decrypt(extras[field].encode()).decode()
        _cache[cache_key] = val
        return val
    except Exception as e:
        logger.warning(
            "%s integration extra '%s' fallback failed (%s)",
            provider, field, type(e).__name__,
        )
        return ""
