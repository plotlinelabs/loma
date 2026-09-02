"""
Registry of active streaming sessions.

Maps conversation_id -> active SDK client so that mid-stream endpoints
(inject, interrupt) can find the right client while the agent is working.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from time import monotonic

from claude_agent_sdk import ClaudeSDKClient

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_streams: dict[str, "ActiveStream"] = {}


@dataclass
class ActiveStream:
    conversation_id: str
    client: ClaudeSDKClient
    user_email: str
    started_at: float = field(default_factory=monotonic)


async def register(conversation_id: str, client: ClaudeSDKClient, user_email: str) -> None:
    async with _lock:
        _streams[conversation_id] = ActiveStream(
            conversation_id=conversation_id,
            client=client,
            user_email=user_email,
        )
    logger.info("Registered active stream for conversation %s", conversation_id)


async def unregister(conversation_id: str) -> None:
    async with _lock:
        removed = _streams.pop(conversation_id, None)
    if removed:
        logger.info("Unregistered active stream for conversation %s", conversation_id)


async def get_for_user(conversation_id: str, user_email: str) -> ActiveStream | None:
    async with _lock:
        stream = _streams.get(conversation_id)
    if stream is None:
        return None
    if stream.user_email and user_email and stream.user_email != user_email:
        logger.warning(
            "User %s attempted to access stream owned by %s",
            user_email, stream.user_email,
        )
        return None
    return stream
