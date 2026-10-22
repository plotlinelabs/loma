"""
Registry of active streaming sessions.

Maps conversation_id -> the handle that can interrupt (and, for the Claude SDK
runtime, inject into) the run so that mid-stream endpoints (inject, interrupt)
can find the right runtime while the agent is working.

Every runtime registers here: the Claude SDK path registers its
``ClaudeSDKClient``; OpenCode and Codex register a ``RunHandle`` that aborts
their turn. ``ActiveStream.stopped`` lets the run loop tell a user stop apart
from a normal completion so the conversation is persisted as ``interrupted``.

It also owns the one-run-per-conversation claim (``try_claim``): two runs of
the same conversation would fight over the same devices, proxies, files and
branches, so a second message must be injected or queued, never run in
parallel. The backend is a single aiohttp process (one ``loma-backend``
replica, no multi-worker server), so an in-process claim is authoritative.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from time import monotonic, time
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Reason persisted on the conversation document for a user-initiated stop.
# The dashboard matches on this to render "Stopped by user" rather than the
# generic "server restarted" interruption copy.
STOPPED_BY_USER_REASON = "Stopped by user"

# A stop can arrive before the runtime has registered its handle (server or
# session warm-up). Remember it briefly so register() applies it; the TTL
# guarantees a stale request can never abort a later run of the conversation.
PENDING_STOP_TTL_SECONDS = 120.0

_lock = asyncio.Lock()
_streams: dict[str, "ActiveStream"] = {}
_pending_stops: dict[str, float] = {}
_claims: dict[str, "RunClaim"] = {}


@dataclass
class ActiveStream:
    conversation_id: str
    # Anything with ``async interrupt()``; ``async query(message)`` is optional
    # (only the Claude SDK client supports mid-stream injection).
    client: Any
    user_email: str
    started_at: float = field(default_factory=monotonic)
    stop_requested: asyncio.Event = field(default_factory=asyncio.Event)
    # A live handle this one displaced (should never happen with try_claim);
    # kept so Stop still reaches that run too.
    superseded: "ActiveStream | None" = None
    ended: bool = False
    # The task running the run; if it finished without unregistering (crash,
    # abandoned generator) the handle is dead even though ``ended`` is False.
    task: asyncio.Task | None = None
    # Messages injected mid-run whose turn has not finished yet. The run loop
    # must not end while one is waiting, or the message is never answered and
    # the user (or the dashboard) sends it again as a new run.
    pending_injections: int = 0
    # Set once the run loop has decided to finish. From then on nothing reads
    # the client, so injections are refused and the caller queues instead.
    closing: bool = False
    # Epoch seconds when the earliest per-user MCP token baked into this client
    # expires. Past it, injections are refused so the next run gets fresh tokens.
    credentials_expire_at: float | None = None

    @property
    def credentials_expired(self) -> bool:
        return self.credentials_expire_at is not None and time() >= self.credentials_expire_at

    @property
    def live(self) -> bool:
        return not self.ended and (self.task is None or not self.task.done())

    @property
    def stopped(self) -> bool:
        """True once the user asked to stop this run."""
        return self.stop_requested.is_set()

    async def interrupt(self) -> None:
        """Record the stop request, then abort the runtime's current turn."""
        self.stop_requested.set()
        await self.client.interrupt()
        if self.superseded is not None and not self.superseded.ended:
            await self.superseded.interrupt()


@dataclass
class RunClaim:
    run_id: str
    # The task that claimed; if it finishes without releasing (early return,
    # crash) the claim is stale and the next run may take it over.
    task: asyncio.Task | None
    released: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def live(self) -> bool:
        return not self.released.is_set() and (self.task is None or not self.task.done())


class RunHandle:
    """Stop-only handle for runtimes without mid-stream injection (OpenCode, Codex)."""

    supports_injection = False

    def __init__(self, interrupt: Callable[[], Awaitable[None]], runtime: str):
        self._interrupt = interrupt
        self.runtime = runtime

    async def interrupt(self) -> None:
        await self._interrupt()

    async def query(self, message: str) -> None:
        raise RuntimeError(f"Mid-stream injection is not supported for {self.runtime} runs")


async def register(
    conversation_id: str, client: Any, user_email: str, credentials_expire_at: float | None = None,
) -> ActiveStream:
    stream = ActiveStream(
        conversation_id=conversation_id,
        client=client,
        user_email=user_email,
        task=asyncio.current_task(),
        credentials_expire_at=credentials_expire_at,
    )
    async with _lock:
        existing = _streams.get(conversation_id)
        if existing is not None and existing.live:
            # try_claim should make this impossible; never drop the old
            # handle silently or its run becomes unstoppable.
            logger.error(
                "Conversation %s already has a live run handle; chaining it so Stop reaches both",
                conversation_id,
            )
            stream.superseded = existing
        _streams[conversation_id] = stream
        requested_at = _pending_stops.pop(conversation_id, None)
    logger.info("Registered active stream for conversation %s", conversation_id)
    if requested_at is not None and monotonic() - requested_at <= PENDING_STOP_TTL_SECONDS:
        # The runtime checks `.stopped` before starting (or right after
        # sending) its turn, so the run ends as interrupted without work.
        stream.stop_requested.set()
        logger.info("Applied pending stop to conversation %s", conversation_id)
    return stream


async def request_pending_stop(conversation_id: str) -> None:
    """Record a stop for a run that has not registered its handle yet."""
    async with _lock:
        _pending_stops[conversation_id] = monotonic()
    logger.info("Stop requested for conversation %s before its runtime registered", conversation_id)


async def unregister(conversation_id: str, stream: ActiveStream) -> None:
    """Remove ``stream`` — and only ``stream`` — from the registry."""
    async with _lock:
        stream.ended = True
        stream.closing = True
        if _streams.get(conversation_id) is not stream:
            return
        previous = stream.superseded
        if previous is not None and previous.live:
            _streams[conversation_id] = previous
        else:
            _streams.pop(conversation_id, None)
            _pending_stops.pop(conversation_id, None)
    logger.info("Unregistered active stream for conversation %s", conversation_id)


async def try_claim(conversation_id: str, run_id: str) -> bool:
    """Atomically claim ``conversation_id`` for ``run_id``.

    Returns False while another live run holds it. Re-entrant for the task that
    already holds the claim (e.g. the dashboard route claims, then
    ``stream_agent`` checks again in the same task) — the outer claim is kept.
    """
    task = asyncio.current_task()
    async with _lock:
        held = _claims.get(conversation_id)
        if held is not None and held.live:
            return held.run_id == run_id or (task is not None and held.task is task)
        _claims[conversation_id] = RunClaim(run_id=run_id, task=task)
    return True


async def release_claim(conversation_id: str, run_id: str) -> None:
    """Release the claim if ``run_id`` still owns it."""
    async with _lock:
        held = _claims.get(conversation_id)
        if held is None or held.run_id != run_id:
            return
        _claims.pop(conversation_id, None)
    held.released.set()


async def wait_for_release(conversation_id: str, poll_seconds: float = 5.0) -> None:
    """Wait until the current claim is released (or goes stale)."""
    while True:
        async with _lock:
            held = _claims.get(conversation_id)
        if held is None or not held.live:
            return
        try:
            await asyncio.wait_for(held.released.wait(), timeout=poll_seconds)
            return
        except asyncio.TimeoutError:
            continue


def supports_injection(stream: ActiveStream) -> bool:
    """Only the Claude SDK client can take a message mid-run."""
    return getattr(stream.client, "supports_injection", True) and callable(
        getattr(stream.client, "query", None)
    )


async def inject(stream: ActiveStream, message: str) -> bool:
    """Send ``message`` into the running turn; False if the run can't take it.

    Refused once the run is closing (its loop no longer reads the client), so
    the message is never swallowed by a run that has already finished; the
    caller then queues it for the next run instead. Counting the injection
    under the same lock the run loop uses to decide to finish means the run
    either sees it and keeps going, or the injection is refused: never both,
    never neither.
    """
    if not supports_injection(stream):
        return False
    if stream.credentials_expired:
        logger.info("Not injecting into %s: its MCP tokens expired; queueing for a fresh run",
                    stream.conversation_id)
        return False
    async with _lock:
        if stream.closing or not stream.live:
            return False
        stream.pending_injections += 1
    try:
        await stream.client.query(message)
    except Exception:
        async with _lock:
            stream.pending_injections = max(0, stream.pending_injections - 1)
        raise
    return True


async def end_turn(stream: ActiveStream, *, may_finish: bool) -> bool:
    """Called by the run loop on every turn result; True means finish now.

    A pending injection is answered by the next turn, so this turn's result
    consumes it and the run keeps reading. Otherwise, if the run has nothing
    else to wait for (``may_finish``), it is marked closing atomically so no
    injection can slip in after this decision.
    """
    async with _lock:
        if stream.pending_injections > 0:
            stream.pending_injections -= 1
            return False
        if may_finish:
            stream.closing = True
            return True
        return False


async def get_stream(conversation_id: str) -> ActiveStream | None:
    """The live handle whoever started it. The caller must have checked that
    the user may act on the conversation (e.g. stop a shared-board task)."""
    async with _lock:
        return _streams.get(conversation_id)


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
