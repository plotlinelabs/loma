"""The recovery sweep must not leave a stale run without started_at running forever."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import recovery


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return list(self._docs)

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _matches(doc, query):
    """Tiny evaluator for the operators the sweep uses."""
    for key, cond in query.items():
        if key == "$and":
            if not all(_matches(doc, q) for q in cond):
                return False
        elif key == "$or":
            if not any(_matches(doc, q) for q in cond):
                return False
        elif isinstance(cond, dict):
            value = doc.get(key)
            for op, arg in cond.items():
                if op == "$lt" and not (value is not None and value < arg):
                    return False
                if op == "$gte" and not (value is not None and value >= arg):
                    return False
        elif cond is None:
            if doc.get(key) is not None:
                return False
        elif doc.get(key) != cond:
            return False
    return True


@pytest.mark.asyncio
async def test_running_conversation_without_started_at_is_marked_interrupted():
    docs = [{"conversation_id": "legacy", "status": "running", "last_heartbeat": None, "started_at": None}]
    db = MagicMock()
    db.conversations.find = lambda query, *a: _Cursor([d for d in docs if _matches(d, query)])
    marked = []

    async def update_many(query, update):
        hits = [d for d in docs if _matches(d, query)]
        marked.extend(d["conversation_id"] for d in hits)
        return MagicMock(modified_count=len(hits))

    db.conversations.update_many = AsyncMock(side_effect=update_many)
    with patch.object(recovery, "get_db", return_value=db), \
            patch.object(recovery, "_resume_conversation", new_callable=AsyncMock) as resume, \
            patch("observability.push.fire_task_push"):
        await recovery._run_recovery_check()
    resume.assert_not_called()
    assert marked == ["legacy"]
