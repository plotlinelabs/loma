"""Backend-only transport for a remote chat worker.

This module never starts a local process. The caller supplies authenticated run
context and a trusted, schema-validating tool gateway. Workers cannot choose a
principal, extend their scope or directly invoke an observer/database method.
"""
import asyncio
import json
import ssl
from urllib.parse import urlsplit

import aiohttp

from isolation.protocol import MAX_FRAME, MAX_INPUT, ProtocolError, response_frame, worker_frame


class WorkerUnavailable(RuntimeError):
    pass


def validate_origin(url):
    target = urlsplit(url)
    if (target.scheme != 'https' or not target.hostname or target.username
            or target.password or target.query or target.fragment or target.path not in ('', '/')):
        raise ValueError('Worker endpoint must be an HTTPS origin')


def transport_context(url, ca, cert, key):
    validate_origin(url)
    context = ssl.create_default_context(cafile=ca)
    context.load_cert_chain(cert, key)
    return context


async def stream_worker(*, session, url, token, tls, authority, input,
                        authorize, execute_tool, max_seconds=3600):
    """Yield chat text; validate current access before connecting and every tool.

    authorize(authority) must check active account, run cancellation, current
    policy/grants and resource access. execute_tool must validate arguments using
    a fixed schema and keep secrets in the broker. No raw CLI/path pass-through.
    Neither callback nor allowed_tools may be selected by the worker. On ambiguous
    completion this function never reconnects/replays a run or a tool request.
    """
    if not authority.user_email or not authority.run_id:
        raise WorkerUnavailable('An authenticated run identity is required')
    if not callable(authorize) or not callable(execute_tool):
        raise WorkerUnavailable('A trusted tool gateway is required')
    if not isinstance(input, dict):
        raise ValueError('Worker input must be an object')
    encoded = json.dumps({'type': 'start', 'input': input}, allow_nan=False)
    if len(encoded.encode()) > MAX_INPUT:
        raise ValueError('Worker input exceeds the limit')
    # Config-only origin, never user controlled. A caller may inject a local
    # aiohttp session in tests, but production must use transport_context above.
    validate_origin(url)
    if (not isinstance(tls, ssl.SSLContext) or tls.verify_mode != ssl.CERT_REQUIRED
            or not tls.check_hostname or len(token) < 32):
        raise WorkerUnavailable('Authenticated TLS worker transport is required')
    if not 0 < max_seconds <= 14400:
        raise ValueError('Invalid worker deadline')
    completed = False
    seen = set()
    try:
        async with asyncio.timeout(max_seconds):
            if not await authorize(authority):
                raise WorkerUnavailable('Run access is no longer valid')
            async with session.ws_connect(url.rstrip('/') + '/v1/run',
                    headers={'Authorization': 'Bearer ' + token}, ssl=tls,
                    max_msg_size=MAX_FRAME, heartbeat=20) as ws:
                await ws.send_str(encoded)
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        raise ProtocolError('Worker connection failed')
                    frame = worker_frame(msg.data)
                    if not await authorize(authority):
                        raise WorkerUnavailable('Run access is no longer valid')
                    if frame['type'] == 'done':
                        completed = True
                        break
                    if frame['type'] == 'text':
                        yield frame['text']
                        continue
                    if frame['id'] in seen or len(seen) >= 500:
                        raise ProtocolError('Repeated or excessive worker tool request')
                    seen.add(frame['id'])
                    if frame['tool'] not in authority.allowed_tools:
                        result = {'error': 'Tool is not allowed for this run'}
                    else:
                        # Arguments remain untrusted. The gateway is responsible
                        # for exact action/resource schemas and approval gates.
                        result = await execute_tool(authority, frame['tool'], frame['arguments'])
                    await ws.send_str(response_frame({'type': 'tool_response',
                                                     'id': frame['id'], 'result': result}))
            if not completed:
                raise WorkerUnavailable('Worker disconnected; work was not replayed')
    except (aiohttp.ClientError, TimeoutError, ProtocolError) as exc:
        raise WorkerUnavailable('Isolated worker failed; work was not replayed') from exc
