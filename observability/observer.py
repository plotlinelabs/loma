import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MAX_CONTENT_LEN = 10_000
HEARTBEAT_INTERVAL_SECONDS = 30

# Valid conversation statuses
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"
STATUS_INTERRUPTED = "interrupted"


def _truncate(text: str, max_len: int = MAX_CONTENT_LEN) -> tuple[str, bool]:
    """Truncate text and return (text, was_truncated)."""
    if len(text) <= max_len:
        return text, False
    return text[:max_len], True


def _safe_json(obj, max_len: int = MAX_CONTENT_LEN) -> tuple[str, bool]:
    """Serialize obj to JSON string, truncated."""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(obj)
    return _truncate(s, max_len)


class ConversationObserver:
    """Captures agent conversation data to MongoDB for observability."""

    def __init__(self, db, metadata: dict, conversation_id: str | None = None):
        self.db = db
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self.metadata = metadata
        self.start_time: datetime | None = None
        self.turn_count = 0
        self.turn_offset = 0  # offset for resumed conversations
        self._turns_data: list[dict] = []  # in-memory copy for confidence assessment
        self._agent_cost_data: dict | None = None
        self._heartbeat_task: asyncio.Task | None = None

    async def start(self):
        """Insert initial conversation document with status='running'."""
        self.start_time = datetime.now(timezone.utc)
        try:
            prompt = self.metadata.get("prompt", "")
            initial_messages = []
            if prompt:
                initial_messages.append({
                    "role": "user",
                    "content": prompt,
                    "timestamp": self.start_time,
                })
            await self.db.conversations.insert_one({
                "conversation_id": self.conversation_id,
                "source": self.metadata.get("source", "unknown"),
                "started_at": self.start_time,
                "finished_at": None,
                "duration_ms": None,
                "status": STATUS_RUNNING,
                "last_heartbeat": self.start_time,
                "metadata": {
                    k: v for k, v in self.metadata.items()
                    if k not in ("source", "prompt", "model")
                },
                "prompt": prompt,
                "model": self.metadata.get("model", ""),
                "total_turns": 0,
                "final_response": "",
                "messages": initial_messages,
                "confidence": None,
                "cost": None,
                "savings": None,
                "claude_account": None,
                "error": None,
            })
            self._start_heartbeat()
        except Exception as e:
            logger.warning("Observability: failed to insert conversation: %s", e)

    async def resume(self):
        """Resume an existing conversation — set turn offset and mark running."""
        self.start_time = datetime.now(timezone.utc)
        try:
            existing = await self.db.conversations.find_one(
                {"conversation_id": self.conversation_id},
                {"total_turns": 1},
            )
            if existing:
                self.turn_offset = existing.get("total_turns", 0)
            prompt = self.metadata.get("prompt", "")
            update: dict = {"$set": {
                "status": STATUS_RUNNING,
                "last_heartbeat": self.start_time,
            }}
            if prompt:
                update["$push"] = {"messages": {
                    "role": "user",
                    "content": prompt,
                    "timestamp": self.start_time,
                }}
            await self.db.conversations.update_one(
                {"conversation_id": self.conversation_id},
                update,
            )
            self._start_heartbeat()
        except Exception as e:
            logger.warning("Observability: failed to resume conversation: %s", e)

    def _start_heartbeat(self):
        """Start the background heartbeat loop."""
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def _stop_heartbeat(self):
        """Cancel the background heartbeat loop."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def _heartbeat_loop(self):
        """Periodically update last_heartbeat while the conversation is running."""
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                now = datetime.now(timezone.utc)
                await self.db.conversations.update_one(
                    {"conversation_id": self.conversation_id},
                    {"$set": {"last_heartbeat": now}},
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Observability: heartbeat failed for %s: %s",
                           self.conversation_id, e)

    async def record_text(self, turn_number: int, text: str):
        """Record a text block for a given turn."""
        turn_number = turn_number + self.turn_offset
        truncated_text, was_truncated = _truncate(text)
        turn_doc_update = {
            "$setOnInsert": {
                "conversation_id": self.conversation_id,
                "turn_number": turn_number,
                "timestamp": datetime.now(timezone.utc),
                "message_type": "assistant",
            },
            "$push": {
                "text_blocks": {
                    "text": truncated_text,
                    "_truncated": was_truncated,
                }
            },
        }
        try:
            await self.db.turns.update_one(
                {"conversation_id": self.conversation_id, "turn_number": turn_number},
                turn_doc_update,
                upsert=True,
            )
        except Exception as e:
            logger.warning("Observability: failed to record text: %s", e)

        # Keep in memory for confidence assessment
        self._ensure_turn(turn_number)
        self._turns_data[turn_number - 1].setdefault("text_blocks", []).append({"text": text[:2000]})

    async def record_tool_call(self, turn_number: int, tool_name: str, tool_use_id: str, input_data):
        """Record a tool call for a given turn."""
        turn_number = turn_number + self.turn_offset
        input_str, input_truncated = _safe_json(input_data)
        tool_call = {
            "tool_name": tool_name,
            "tool_use_id": tool_use_id,
            "input": input_str,
            "_input_truncated": input_truncated,
        }
        try:
            await self.db.turns.update_one(
                {"conversation_id": self.conversation_id, "turn_number": turn_number},
                {
                    "$setOnInsert": {
                        "conversation_id": self.conversation_id,
                        "turn_number": turn_number,
                        "timestamp": datetime.now(timezone.utc),
                        "message_type": "assistant",
                    },
                    "$push": {"tool_calls": tool_call},
                },
                upsert=True,
            )
        except Exception as e:
            logger.warning("Observability: failed to record tool call: %s", e)

        self._ensure_turn(turn_number)
        self._turns_data[turn_number - 1].setdefault("tool_calls", []).append({
            "tool_name": tool_name,
            "input": input_str[:500],
        })

    async def record_artifact(self, artifact_data: dict):
        """Persist an artifact (code or file) to the artifacts collection.

        artifact_data should contain:
          - artifact_id: str
          - title: str
          - language: str
          - version: int
          - type: "code" | "file"
          - content: str (for code artifacts)
          - file_url: str (for file artifacts)
          - file_size: int (for file artifacts)
          - file_type: str (for file artifacts)
        """
        try:
            doc = {
                "conversation_id": self.conversation_id,
                "timestamp": datetime.now(timezone.utc),
                **artifact_data,
            }
            await self.db.artifacts.update_one(
                {
                    "conversation_id": self.conversation_id,
                    "artifact_id": artifact_data["artifact_id"],
                },
                {"$set": doc},
                upsert=True,
            )
        except Exception as e:
            logger.warning("Observability: failed to record artifact: %s", e)

    async def record_tool_result(self, tool_use_id: str, is_error: bool, output: str):
        """Record a tool result, attaching it to the turn that made the call."""
        output_str, output_truncated = _truncate(output)
        tool_result = {
            "tool_use_id": tool_use_id,
            "is_error": is_error,
            "output": output_str,
            "_output_truncated": output_truncated,
        }
        # Find the turn that has this tool_use_id and append the result
        try:
            await self.db.turns.update_one(
                {
                    "conversation_id": self.conversation_id,
                    "tool_calls.tool_use_id": tool_use_id,
                },
                {"$push": {"tool_results": tool_result}},
            )
        except Exception as e:
            logger.warning("Observability: failed to record tool result: %s", e)

        # Add to in-memory data (attach to last turn)
        if self._turns_data:
            self._turns_data[-1].setdefault("tool_results", []).append({
                "output": output[:500],
                "is_error": is_error,
            })

    async def record_usage(self, usage: dict | None, total_cost_usd: float | None):
        """Record agent SDK usage and cost data on the conversation document.

        When resuming, accumulates tokens and cost on top of existing values.
        """
        if usage is None and total_cost_usd is None:
            return

        input_tokens = usage.get("input_tokens", 0) if usage else 0
        output_tokens = usage.get("output_tokens", 0) if usage else 0
        agent_cost = round(total_cost_usd, 6) if total_cost_usd is not None else 0

        if self.turn_offset > 0:
            # Resuming — accumulate on top of existing cost
            try:
                await self.db.conversations.update_one(
                    {"conversation_id": self.conversation_id},
                    {"$inc": {
                        "cost.input_tokens": input_tokens,
                        "cost.output_tokens": output_tokens,
                        "cost.agent_cost_usd": agent_cost,
                        "cost.total_cost_usd": agent_cost,
                    }},
                )
                # Refresh in-memory data for confidence cost merge
                existing = await self.db.conversations.find_one(
                    {"conversation_id": self.conversation_id},
                    {"cost": 1},
                )
                self._agent_cost_data = existing.get("cost") if existing else None
            except Exception as e:
                logger.warning("Observability: failed to record usage (resume): %s", e)
        else:
            cost_data = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "agent_cost_usd": agent_cost,
                "confidence_cost_usd": 0,
                "total_cost_usd": agent_cost,
            }
            self._agent_cost_data = cost_data
            try:
                await self.db.conversations.update_one(
                    {"conversation_id": self.conversation_id},
                    {"$set": {"cost": cost_data}},
                )
            except Exception as e:
                logger.warning("Observability: failed to record usage: %s", e)

    async def finish(self, final_response: str = ""):
        """Mark conversation as completed and trigger confidence assessment."""
        self._stop_heartbeat()
        now = datetime.now(timezone.utc)
        duration_ms = int((now - self.start_time).total_seconds() * 1000) if self.start_time else None
        total_turns = self.turn_offset + self.turn_count

        update_fields: dict = {
            "finished_at": now,
            "status": STATUS_COMPLETED,
            "total_turns": total_turns,
            "final_response": final_response[:5000],
        }
        # Only set duration_ms on first run (not resumed), otherwise accumulate
        if self.turn_offset == 0:
            update_fields["duration_ms"] = duration_ms
        else:
            update_fields["duration_ms"] = duration_ms  # latest run duration

        try:
            update: dict = {"$set": update_fields}
            if final_response:
                update["$push"] = {"messages": {
                    "role": "assistant",
                    "content": final_response[:5000],
                    "timestamp": now,
                }}
            await self.db.conversations.update_one(
                {"conversation_id": self.conversation_id},
                update,
            )
        except Exception as e:
            logger.warning("Observability: failed to finish conversation: %s", e)

        # Fire-and-forget title & topic enrichment
        asyncio.create_task(self._run_title_topic_enrichment(final_response))

        # Fire-and-forget savings estimation
        asyncio.create_task(self._run_savings_estimation(
            final_response=final_response,
            total_turns=total_turns,
            duration_ms=duration_ms,
        ))

    async def record_error(self, error: str):
        """Mark conversation as errored."""
        self._stop_heartbeat()
        now = datetime.now(timezone.utc)
        duration_ms = int((now - self.start_time).total_seconds() * 1000) if self.start_time else None
        try:
            await self.db.conversations.update_one(
                {"conversation_id": self.conversation_id},
                {"$set": {
                    "finished_at": now,
                    "duration_ms": duration_ms,
                    "status": STATUS_ERROR,
                    "total_turns": self.turn_offset + self.turn_count,
                    "error": error[:5000],
                }},
            )
        except Exception as e:
            logger.warning("Observability: failed to record error: %s", e)

    async def record_account(self, account_email: str):
        """Record which Claude account is processing this conversation."""
        try:
            await self.db.conversations.update_one(
                {"conversation_id": self.conversation_id},
                {"$set": {"claude_account": account_email}},
            )
        except Exception as e:
            logger.warning("Observability: failed to record account: %s", e)

    async def mark_interrupted(self, reason: str = "Server shutdown"):
        """Mark conversation as interrupted (e.g., due to server shutdown)."""
        self._stop_heartbeat()
        now = datetime.now(timezone.utc)
        duration_ms = int((now - self.start_time).total_seconds() * 1000) if self.start_time else None
        try:
            await self.db.conversations.update_one(
                {"conversation_id": self.conversation_id},
                {"$set": {
                    "finished_at": now,
                    "duration_ms": duration_ms,
                    "status": STATUS_INTERRUPTED,
                    "total_turns": self.turn_offset + self.turn_count,
                    "error": reason[:5000],
                }},
            )
        except Exception as e:
            logger.warning("Observability: failed to mark conversation as interrupted: %s", e)

    async def _run_title_topic_enrichment(self, final_response: str):
        """Generate title and topic for the conversation via LLM."""
        try:
            from api.routes import _generate_title_llm, _classify_topic_llm
            prompt = self.metadata.get("prompt", "")
            response_snippet = (final_response or "")[:300]

            title, topic = await asyncio.gather(
                _generate_title_llm(prompt, response_snippet),
                _classify_topic_llm(prompt, response_snippet),
            )

            await self.db.conversations.update_one(
                {"conversation_id": self.conversation_id},
                {"$set": {"title": title, "topic": topic}},
            )
            logger.info("Observability: enrichment complete for %s: title=%r topic=%s",
                         self.conversation_id, title, topic)
        except Exception as e:
            logger.warning("Observability: title/topic enrichment failed for %s: %s",
                           self.conversation_id, e)

    async def _run_savings_estimation(
        self,
        final_response: str,
        total_turns: int,
        duration_ms: int | None,
    ):
        """Estimate human cost and savings in background and persist to DB."""
        try:
            from observability.savings import estimate_human_cost

            prompt = self.metadata.get("prompt", "")
            api_cost = 0.0
            if self._agent_cost_data:
                api_cost = self._agent_cost_data.get("total_cost_usd", 0)

            savings_data = estimate_human_cost(
                prompt=prompt,
                final_response=final_response,
                total_turns=total_turns,
                duration_ms=duration_ms,
                api_cost_usd=api_cost,
            )

            await self.db.conversations.update_one(
                {"conversation_id": self.conversation_id},
                {"$set": {"savings": savings_data}},
            )
            logger.info(
                "Observability: savings estimation complete for %s: "
                "human=$%.2f, api=$%.4f, saved=$%.2f (%s, %s min)",
                self.conversation_id,
                savings_data["estimated_human_cost_usd"],
                api_cost,
                savings_data["savings_usd"],
                savings_data["expertise_category"],
                savings_data["estimated_human_duration_minutes"],
            )
        except Exception as e:
            logger.warning("Observability: savings estimation failed for %s: %s",
                          self.conversation_id, e)

    def _ensure_turn(self, turn_number: int):
        """Ensure in-memory turns list has enough entries."""
        while len(self._turns_data) < turn_number:
            self._turns_data.append({})
