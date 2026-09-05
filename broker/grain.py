"""Narrow read-only Grain adapter; personal OAuth only, no org fallback.

Contract: https://developers.grain.com/ (v2 transcript text endpoint).
Expired tokens fail closed until a trusted refresh service is integrated.
"""
import re
from datetime import datetime, timezone

import aiohttp

from api.oauth_helpers import decrypt_token
from broker.service import Denied
from utils.secret_redaction import redact_secrets

MAX_TRANSCRIPT_BYTES = 1024 * 1024


class GrainTranscript:
    @staticmethod
    def valid_resource(value):
        return isinstance(value, str) and bool(re.fullmatch(
            r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value,
        ))

    async def execute(self, db, email, recording_id):
        if not self.valid_resource(recording_id):
            raise Denied()
        # Read on every invocation; deleting a connection revokes future access.
        doc = await db.oauth_tokens.find_one({"user_email": email, "provider": "grain"})
        if not doc:
            raise Denied()
        expiry = doc.get("token_expiry")
        if expiry is not None and expiry.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
            raise Denied()
        token = decrypt_token(doc["access_token"])
        if not token:
            raise Denied()
        # No worker-supplied hosts, methods, paths, query strings, or headers.
        url = f"https://api.grain.com/_/public-api/v2/recordings/{recording_id}/transcript.txt"
        async with aiohttp.ClientSession(
            trust_env=False, timeout=aiohttp.ClientTimeout(total=30),
            auto_decompress=False,
        ) as session:
            async with session.get(url, headers={
                "Authorization": f"Bearer {token}", "Public-Api-Version": "2025-10-31",
                "Accept-Encoding": "identity",
            }, allow_redirects=False) as response:
                if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                    raise Denied()
                chunks = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    chunks.extend(chunk)
                    if len(chunks) > MAX_TRANSCRIPT_BYTES:
                        raise Denied()
        # Defense in depth for an upstream echo. Do not relay provider headers.
        transcript = chunks.decode("utf-8").replace(token, "[REDACTED]")
        return {"recording_id": recording_id, "transcript": redact_secrets(transcript)}
