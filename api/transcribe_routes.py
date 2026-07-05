"""Speech-to-text route for composer dictation.

The dashboard records audio with MediaRecorder (audio/mp4 on iOS Safari,
audio/webm on Chrome) and posts the blob here; we forward it to OpenAI's
transcription API and return plain text. Server-side so the API key stays
private and quality beats on-device engines (Web Speech API is also broken
in installed iOS PWAs, which is the primary dictation surface).
"""
import logging
import os

import aiohttp
from aiohttp import web

from api.auth_helpers import get_user_email

logger = logging.getLogger(__name__)

OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
TRANSCRIBE_MODEL = "gpt-4o-transcribe"

# OpenAI rejects >25MB; the client caps recordings at ~3 minutes anyway.
MAX_AUDIO_BYTES = 20 * 1024 * 1024
# A tap-and-immediate-stop produces a few hundred bytes of container header —
# not worth an API call.
MIN_AUDIO_BYTES = 1024

# Vocabulary hint — biases the model toward product names and the jargon
# that on-device dictation reliably mangles.
VOCAB_PROMPT = (
    "Loma, Plotline, OpenCode, Claude, Anthropic, MongoDB, Atlas, aiohttp, "
    "Next.js, PWA, Slack, Linear, HubSpot, Grain, webhook, kanban, repo, PR, "
    "API, MCP, VAPID, STT, agent, prompt, dashboard."
)


async def handle_transcribe(request: web.Request) -> web.Response:
    """POST /api/transcribe — multipart audio in, {"text": ...} out."""
    if not get_user_email(request):
        return web.json_response({"error": "Not authenticated"}, status=401)

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return web.json_response(
            {"error": "Transcription is not configured (missing OPENAI_API_KEY)"},
            status=503,
        )

    reader = await request.multipart()
    audio: bytes | None = None
    filename = "audio.webm"
    content_type = "audio/webm"
    async for part in reader:
        if part.name != "audio":
            continue
        filename = part.filename or filename
        content_type = part.headers.get("Content-Type") or content_type
        audio = await part.read(decode=False)
        break

    if audio is None:
        return web.json_response({"error": "No audio file in request"}, status=400)
    if len(audio) < MIN_AUDIO_BYTES:
        return web.json_response({"error": "Recording too short"}, status=400)
    if len(audio) > MAX_AUDIO_BYTES:
        return web.json_response({"error": "Recording too large"}, status=413)

    form = aiohttp.FormData()
    form.add_field("file", audio, filename=filename, content_type=content_type)
    form.add_field("model", TRANSCRIBE_MODEL)
    form.add_field("prompt", VOCAB_PROMPT)

    try:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                OPENAI_TRANSCRIBE_URL,
                data=form,
                headers={"Authorization": f"Bearer {api_key}"},
            ) as resp:
                payload = await resp.json(content_type=None)
                if resp.status != 200:
                    message = (payload.get("error") or {}).get("message") or "Transcription failed"
                    logger.error("[TRANSCRIBE] OpenAI %s: %s", resp.status, message)
                    return web.json_response({"error": message}, status=502)
    except Exception:
        logger.exception("[TRANSCRIBE] request failed")
        return web.json_response({"error": "Transcription failed"}, status=502)

    return web.json_response({"text": (payload.get("text") or "").strip()})


def setup_transcribe_routes(app: web.Application):
    """Register transcription routes."""
    app.router.add_post("/api/transcribe", handle_transcribe)
