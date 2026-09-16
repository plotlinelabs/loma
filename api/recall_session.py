"""Client of the separate session issuer. No private key, no identity headers.

The URL is deployment configuration, never request/model input. Only the initial
launch forwards the actual browser session cookie; it never reaches an agent.
"""
import os
from urllib.parse import urlsplit
import aiohttp
from config.recall import recall_enabled


def issuer_url():
    url = os.environ.get('LOMA_RECALL_ISSUER_URL', 'http://loma-dashboard:3001').rstrip('/')
    parsed = urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError('invalid_issuer_url')
    if parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', 'loma-dashboard'):
        raise ValueError('issuer_requires_tls')
    return url


async def issuer_call(body, cookie=None):
    headers = {'Cookie': cookie} if cookie else {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        async with session.post(issuer_url() + '/api/recall-session', json=body,
                                headers=headers, allow_redirects=False) as response:
            raw = await response.content.read(16385)
            if response.status != 200 or len(raw) > 16384:
                raise ValueError('issuer_unavailable')
            import json
            return json.loads(raw)


async def launch_recall(request, conversation_id, email):
    if not recall_enabled() or not request.headers.get('Cookie') or not conversation_id:
        return None
    try:
        result = await issuer_call({'action': 'launch', 'conversation_id': conversation_id},
                                   request.headers['Cookie'])
        if result.get('email') != email:
            await issuer_call({'action': 'revoke', 'grant': result['grant']})
            return None
        return result
    except (ValueError, aiohttp.ClientError, TimeoutError):
        return None  # Normal chat remains available during an issuer outage.


async def runtime_public_key(token):
    result = await issuer_call({'action': 'validate', 'capability': token})
    return result['public_key']
