"""Authenticated proxy for Deepgram live transcription and endpointing."""
import asyncio
import json
import logging
import os
import secrets
import time

import aiohttp
from aiohttp import WSMsgType, web

from api.auth_helpers import get_user_email

logger = logging.getLogger(__name__)
DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"
TOKEN_TTL = 30
_tokens: dict[str, float] = {}


async def handle_listen_token(request: web.Request) -> web.Response:
    if not get_user_email(request):
        return web.json_response({"error": "Not authenticated"}, status=401)
    now = time.time()
    for token, expiry in list(_tokens.items()):
        if expiry < now:
            _tokens.pop(token, None)
    token = secrets.token_urlsafe(32)
    _tokens[token] = now + TOKEN_TTL
    return web.json_response({"token": token})


async def handle_listen_ws(request: web.Request) -> web.StreamResponse:
    token = request.query.get("token", "")
    expiry = _tokens.pop(token, None)
    if not expiry or expiry < time.time():
        return web.json_response({"error": "Invalid or expired token"}, status=403)
    api_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if not api_key:
        return web.json_response({"error": "Live transcription is not configured"}, status=503)

    browser_ws = web.WebSocketResponse(heartbeat=20)
    await browser_ws.prepare(request)
    params = {
        "model": "nova-3",
        "smart_format": "true",
        "interim_results": "true",
        "vad_events": "true",
        "endpointing": "500",
        "utterance_end_ms": "1000",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                DEEPGRAM_WS_URL,
                params=params,
                headers={"Authorization": f"Token {api_key}"},
                heartbeat=20,
            ) as deepgram_ws:
                async def browser_to_deepgram():
                    async for message in browser_ws:
                        if message.type == WSMsgType.BINARY:
                            await deepgram_ws.send_bytes(message.data)
                        elif message.type == WSMsgType.TEXT and message.data == "close":
                            await deepgram_ws.send_str(json.dumps({"type": "CloseStream"}))
                            break

                async def deepgram_to_browser():
                    async for message in deepgram_ws:
                        if message.type == WSMsgType.TEXT:
                            await browser_ws.send_str(message.data)
                        elif message.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                            break

                tasks = [asyncio.create_task(browser_to_deepgram()), asyncio.create_task(deepgram_to_browser())]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*done, return_exceptions=True)
    except Exception:
        logger.exception("[VOICE LISTEN] Deepgram streaming failed")
        if not browser_ws.closed:
            await browser_ws.send_json({"type": "Error", "description": "Live transcription failed"})
    finally:
        await browser_ws.close()
    return browser_ws


def setup_voice_listen_routes(app: web.Application):
    app.router.add_post("/api/voice/listen-token", handle_listen_token)
    app.router.add_get("/api/voice/listen", handle_listen_ws)
