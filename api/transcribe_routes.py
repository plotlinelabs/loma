"""Speech-to-text route for composer dictation.

The dashboard records audio with MediaRecorder (audio/mp4 on iOS Safari,
audio/webm on Chrome) and posts the blob here; we forward it to a hosted
transcription API and return plain text. Server-side so the API key stays
private and quality beats on-device engines (Web Speech API is also broken
in installed iOS PWAs, which is the primary dictation surface).

Provider selection: Deepgram's Nova-3 when DEEPGRAM_API_KEY is set
(preferred - purpose-built STT, sub-second latency, ~40% cheaper than
gpt-4o-transcribe), otherwise OpenAI's whisper-1. Both are pure
speech-to-text models: unlike the previous gpt-4o-transcribe (a chat LLM),
they cannot "answer" a spoken question instead of transcribing it.
"""
import logging
import os

import aiohttp
from aiohttp import web

from agent.prompt import get_prompt_setting
from api.auth_helpers import get_user_email
from api.prompt_setting_defaults import get_default_prompt_setting

logger = logging.getLogger(__name__)

DEEPGRAM_TRANSCRIBE_URL = "https://api.deepgram.com/v1/listen"
DEEPGRAM_TRANSCRIBE_MODEL = "nova-3"
OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_TRANSCRIBE_MODEL = "whisper-1"

# OpenAI rejects >25MB and Deepgram accepts far more; the client caps
# recordings at ~3 minutes anyway.
MAX_AUDIO_BYTES = 20 * 1024 * 1024
# A tap-and-immediate-stop produces a few hundred bytes of container header —
# not worth an API call.
MIN_AUDIO_BYTES = 1024

# Deepgram caps keyterm prompting at 100 terms per request.
MAX_KEYTERMS = 100


def _vocab_prompt() -> str:
    """Vocabulary hint — biases the model toward product names and the jargon
    that on-device dictation reliably mangles. Admin-edited via the dashboard
    ("Dictation Vocabulary" prompt setting); generic tech terms by default."""
    return (
        get_prompt_setting("dictation_vocabulary").strip()
        or get_default_prompt_setting("dictation_vocabulary")
    )


def _vocab_keyterms(vocab: str) -> list[str]:
    """Split the vocabulary hint into individual terms for Deepgram's
    keyterm prompting (Nova-3's equivalent of Whisper's `prompt` field)."""
    terms = [t.strip() for t in vocab.replace("\n", ",").split(",")]
    return [t for t in terms if t][:MAX_KEYTERMS]


async def _transcribe_deepgram(api_key: str, audio: bytes, content_type: str) -> tuple[str | None, str | None]:
    """Send raw audio to Deepgram Nova-3. Returns (text, error)."""
    params = [("model", DEEPGRAM_TRANSCRIBE_MODEL), ("smart_format", "true")]
    vocab = _vocab_prompt()
    if vocab:
        params.extend(("keyterm", term) for term in _vocab_keyterms(vocab))

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            DEEPGRAM_TRANSCRIBE_URL,
            params=params,
            data=audio,
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": content_type,
            },
        ) as resp:
            payload = await resp.json(content_type=None)
            if resp.status != 200:
                message = (
                    payload.get("err_msg")
                    or payload.get("error")
                    or "Transcription failed"
                )
                logger.error("[TRANSCRIBE] Deepgram %s: %s", resp.status, message)
                return None, str(message)
    try:
        text = payload["results"]["channels"][0]["alternatives"][0]["transcript"]
    except (KeyError, IndexError, TypeError):
        logger.error("[TRANSCRIBE] Deepgram: unexpected response shape")
        return None, "Transcription failed"
    return (text or "").strip(), None


async def _transcribe_openai(
    api_key: str, audio: bytes, filename: str, content_type: str
) -> tuple[str | None, str | None]:
    """Send audio to OpenAI whisper-1 (multipart). Returns (text, error)."""
    form = aiohttp.FormData()
    form.add_field("file", audio, filename=filename, content_type=content_type)
    form.add_field("model", OPENAI_TRANSCRIBE_MODEL)
    vocab = _vocab_prompt()
    if vocab:
        form.add_field("prompt", vocab)

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
                return None, str(message)
    return (payload.get("text") or "").strip(), None


async def handle_transcribe(request: web.Request) -> web.Response:
    """POST /api/transcribe — multipart audio in, {"text": ...} out."""
    if not get_user_email(request):
        return web.json_response({"error": "Not authenticated"}, status=401)

    deepgram_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not deepgram_key and not openai_key:
        return web.json_response(
            {"error": "Transcription is not configured (set DEEPGRAM_API_KEY or OPENAI_API_KEY)"},
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

    try:
        if deepgram_key:
            text, error = await _transcribe_deepgram(deepgram_key, audio, content_type)
        else:
            text, error = await _transcribe_openai(openai_key, audio, filename, content_type)
    except Exception:
        logger.exception("[TRANSCRIBE] request failed")
        return web.json_response({"error": "Transcription failed"}, status=502)

    if error is not None:
        return web.json_response({"error": error}, status=502)
    return web.json_response({"text": text})


def setup_transcribe_routes(app: web.Application):
    """Register transcription routes."""
    app.router.add_post("/api/transcribe", handle_transcribe)
