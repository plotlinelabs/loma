"""Recall-only capability verification. No signing key or token minting route.

The future trusted session/task issuer must run outside agent-readable processes.
Forwarded identity headers and personal-tool tokens are intentionally not accepted.
"""
import base64
import json
import os
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


@dataclass(frozen=True)
class RecallIdentity:
    user_id: str
    email: str
    execution_id: str
    project_id: str | None
    agent_id: str | None


def decode64(value: str) -> bytes:
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


def verify_recall_token(token: str) -> RecallIdentity:
    """Verify exact audience, lifetime, immutable user ID and signed scope."""
    try:
        if not isinstance(token, str) or len(token) > 4096:
            raise ValueError()
        payload, signature = token.split('.')
        key = Ed25519PublicKey.from_public_bytes(
            decode64(os.environ.get('LOMA_RECALL_PUBLIC_KEY', '')),
        )
        key.verify(decode64(signature), payload.encode('ascii'))
        claims = json.loads(decode64(payload))
        if set(claims) != {'aud', 'sub', 'email', 'execution_id', 'project_id', 'agent_id', 'iat', 'exp'}:
            raise ValueError()
        if claims['aud'] != 'loma:recall:fetch:v1':
            raise ValueError()
        now = int(time.time())
        iat, exp = claims['iat'], claims['exp']
        if type(iat) is not int or type(exp) is not int or not iat <= now < exp or not 0 < exp - iat <= 300:
            raise ValueError()
        for name in ('sub', 'email', 'execution_id'):
            value = claims[name]
            if not isinstance(value, str) or not 1 <= len(value) <= 254 or value.strip() != value:
                raise ValueError()
        for name in ('project_id', 'agent_id'):
            value = claims[name]
            if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 128):
                raise ValueError()
        return RecallIdentity(claims['sub'], claims['email'], claims['execution_id'], claims['project_id'], claims['agent_id'])
    except Exception:
        raise ValueError('invalid_recall_credential') from None
