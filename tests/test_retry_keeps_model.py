"""A run that fails over to another account keeps the model and env it started with."""

import pytest


@pytest.mark.asyncio
async def test_failover_reacquires_with_the_runs_model(monkeypatch):
    import agent.client as agent_client
    monkeypatch.delenv('LOMA_REMOTE_WORKERS', raising=False)
    seen = []

    class AuthFailingClient:
        _pool_account = {'email': 'a@example.test'}
        _pool_model = None

        async def query(self, *args, **kwargs):
            raise RuntimeError('401 authentication_error')

        def receive_response(self):
            raise RuntimeError('401 authentication_error')

    class FakePool:
        async def acquire(self, **kwargs):
            seen.append(kwargs)
            if len(seen) == 1:
                return AuthFailingClient()
            raise RuntimeError('no more accounts')

        async def release(self, *args, **kwargs):
            return None

        def mark_account_exhausted(self, *args, **kwargs):
            return None

        def status(self):
            return {'accounts': [{'email': 'a@example.test'}], 'available': 0, 'in_use': 0,
                    'pool_size': 1, 'queue_depth': 0}

    monkeypatch.setattr(agent_client, 'get_pool', lambda: FakePool())
    _ = [e async for e in agent_client.stream_agent('hi', selected_model='anthropic/claude-test')]
    assert len(seen) >= 2, seen
    assert seen[1].get('model') == seen[0].get('model') == 'claude-test'
