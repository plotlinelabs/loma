"""Natural text-to-speech for Voice Mode, backed by ElevenLabs Flash."""
import logging
import os

import aiohttp
from aiohttp import web

from api.auth_helpers import get_user_email

logger = logging.getLogger(__name__)

ELEVENLABS_SPEAK_URL = "https://api.elevenlabs.io/v1/text-to-speech"
VOICE_IDS = {
    "rachel": "21m00Tcm4TlvDq8ikWAM",
    "adam": "pNInz6obpgDQGcFmaJgB",
    "bella": "EXAVITQu4vr4xnSDxMaL",
    "antoni": "ErXwobaYiN019PkySvjV",
}
MAX_TEXT_LENGTH = 2_000
MIN_SPEED = 0.7
MAX_SPEED = 1.2


async def handle_speech(request: web.Request) -> web.Response:
    if not get_user_email(request):
        return web.json_response({"error": "Not authenticated"}, status=401)

    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        return web.json_response({"error": "Natural speech is not configured"}, status=503)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    text = str(body.get("text") or "").strip()
    voice = str(body.get("voice") or "")
    try:
        speed = float(body.get("speed", 1.1))
    except (TypeError, ValueError):
        speed = 0
    if not text or len(text) > MAX_TEXT_LENGTH:
        return web.json_response({"error": "Text must be between 1 and 2000 characters"}, status=400)
    voice_id = VOICE_IDS.get(voice)
    if not voice_id:
        return web.json_response({"error": "Select a valid voice"}, status=400)
    if not MIN_SPEED <= speed <= MAX_SPEED:
        return web.json_response({"error": "Speed must be between 0.7 and 1.2"}, status=400)

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{ELEVENLABS_SPEAK_URL}/{voice_id}/stream",
            params={"output_format": "mp3_44100_128"},
            json={
                "text": text,
                "model_id": "eleven_flash_v2_5",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "speed": speed,
                },
            },
            headers={"xi-api-key": api_key, "Accept": "audio/mpeg"},
        ) as response:
            audio = await response.read()
            if response.status != 200:
                logger.error("[SPEECH] ElevenLabs %s: %s", response.status, audio[:300])
                return web.json_response({"error": "Speech generation failed"}, status=502)
    return web.Response(body=audio, content_type="audio/mpeg")


def setup_speech_routes(app: web.Application):
    app.router.add_post("/api/voice/speech", handle_speech)
