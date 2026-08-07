"""Natural text-to-speech for Voice Mode, backed by Deepgram Aura 2."""
import logging
import os

import aiohttp
from aiohttp import web

from api.auth_helpers import get_user_email

logger = logging.getLogger(__name__)

DEEPGRAM_SPEAK_URL = "https://api.deepgram.com/v1/speak"
VOICE_MODELS = {
    "thalia": "aura-2-thalia-en",
    "apollo": "aura-2-apollo-en",
    "andromeda": "aura-2-andromeda-en",
    "orion": "aura-2-orion-en",
}
MAX_TEXT_LENGTH = 2_000


async def handle_speech(request: web.Request) -> web.Response:
    if not get_user_email(request):
        return web.json_response({"error": "Not authenticated"}, status=401)

    api_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if not api_key:
        return web.json_response({"error": "Natural speech is not configured"}, status=503)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    text = str(body.get("text") or "").strip()
    voice = str(body.get("voice") or "")
    if not text or len(text) > MAX_TEXT_LENGTH:
        return web.json_response({"error": "Text must be between 1 and 2000 characters"}, status=400)
    model = VOICE_MODELS.get(voice)
    if not model:
        return web.json_response({"error": "Select a valid voice"}, status=400)

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            DEEPGRAM_SPEAK_URL,
            params={"model": model, "encoding": "mp3"},
            json={"text": text},
            headers={"Authorization": f"Token {api_key}"},
        ) as response:
            audio = await response.read()
            if response.status != 200:
                logger.error("[SPEECH] Deepgram %s: %s", response.status, audio[:300])
                return web.json_response({"error": "Speech generation failed"}, status=502)
    return web.Response(body=audio, content_type="audio/mpeg")


def setup_speech_routes(app: web.Application):
    app.router.add_post("/api/voice/speech", handle_speech)
