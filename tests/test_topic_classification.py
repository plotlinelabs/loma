import asyncio

from api.routes import _classify_topic_llm, _enrich_conversation


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, self._stderr


def test_topic_classification_returns_valid_topic(monkeypatch):
    async def create_process(*args, **kwargs):
        return FakeProcess(b'{"result":"integration"}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    assert asyncio.run(_classify_topic_llm("Help integrate the SDK")) == "integration"


def test_topic_classification_accepts_genuine_other(monkeypatch):
    async def create_process(*args, **kwargs):
        return FakeProcess(b'{"result":"other"}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    assert asyncio.run(_classify_topic_llm("Say hello")) == "other"


def test_topic_classification_retries_cli_failure(monkeypatch):
    processes = [
        FakeProcess(stderr=b"temporary failure", returncode=1),
        FakeProcess(b'{"result":"sdk"}'),
    ]

    async def create_process(*args, **kwargs):
        return processes.pop(0)

    async def no_sleep(_delay):
        pass

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    assert asyncio.run(_classify_topic_llm("Android SDK crash")) == "sdk"
    assert not processes


def test_topic_classification_does_not_convert_invalid_output_to_other(monkeypatch):
    calls = 0

    async def create_process(*args, **kwargs):
        nonlocal calls
        calls += 1
        return FakeProcess(b'{"result":"not-a-valid-topic"}')

    async def no_sleep(_delay):
        pass

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    assert asyncio.run(_classify_topic_llm("Unclear request")) is None
    assert calls == 3


def test_topic_classification_retries_timeouts(monkeypatch):
    calls = 0

    async def create_process(*args, **kwargs):
        return FakeProcess()

    async def timeout(coro, *args, **kwargs):
        nonlocal calls
        calls += 1
        coro.close()
        raise asyncio.TimeoutError

    async def no_sleep(_delay):
        pass

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(asyncio, "wait_for", timeout)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    assert asyncio.run(_classify_topic_llm("Unclear request")) is None
    assert calls == 3


def test_enrichment_leaves_failed_topic_unset_for_later_retry(monkeypatch):
    class Conversations:
        def __init__(self):
            self.update = None

        async def update_one(self, query, update):
            self.update = update

    class DB:
        conversations = Conversations()

    async def failed_classification(*args):
        return None

    monkeypatch.setattr("api.routes._classify_topic_llm", failed_classification)
    conversation = {
        "conversation_id": "conversation-1",
        "prompt": "Unclear request",
        "title": "Existing title",
    }

    result = asyncio.run(_enrich_conversation(DB(), conversation))

    assert "topic" not in result
    assert DB.conversations.update is None
