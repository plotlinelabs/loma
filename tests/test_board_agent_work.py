import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from api import task_routes


@pytest.mark.parametrize('doc,expected', [(None, True), ({'task_board': {}}, True), ({'task_board': {'show_agent_work': False}}, False)])
def test_visibility_defaults(doc, expected):
    assert task_routes.get_board_config(doc)['show_agent_work'] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [False, True])
async def test_visibility_only_update_is_scoped(monkeypatch, value):
    users = SimpleNamespace(update_one=AsyncMock(), find_one=AsyncMock(return_value={'task_board': {'show_agent_work': value}}))
    conversations = SimpleNamespace(update_many=AsyncMock())
    monkeypatch.setattr(task_routes, 'get_db', lambda: SimpleNamespace(users=users, conversations=conversations))
    monkeypatch.setattr(task_routes, 'get_user_email', lambda _: 'owner@example.com')
    response = await task_routes.handle_put_board_settings(SimpleNamespace(json=AsyncMock(return_value={'show_agent_work': value})))
    assert response.status == 200
    assert json.loads(response.text)['show_agent_work'] is value
    users.update_one.assert_awaited_once_with({'email': 'owner@example.com'}, {'$set': {'task_board.show_agent_work': value}})
    conversations.update_many.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{'show_agent_work': 'false'}, {'show_agent_work': 0}, {'show_agent_work': None}, []])
async def test_invalid_preference_rejected(monkeypatch, body):
    users = SimpleNamespace(update_one=AsyncMock())
    monkeypatch.setattr(task_routes, 'get_db', lambda: SimpleNamespace(users=users))
    monkeypatch.setattr(task_routes, 'get_user_email', lambda _: 'owner@example.com')
    response = await task_routes.handle_put_board_settings(SimpleNamespace(json=AsyncMock(return_value=body)))
    assert response.status == 400
    users.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_save_preserves_dismissal(monkeypatch):
    users = SimpleNamespace(update_one=AsyncMock(), find_one=AsyncMock(return_value={'task_board': {'show_agent_work': False}}))
    monkeypatch.setattr(task_routes, 'get_db', lambda: SimpleNamespace(users=users))
    monkeypatch.setattr(task_routes, 'get_user_email', lambda _: 'owner@example.com')
    response = await task_routes.handle_put_board_settings(SimpleNamespace(json=AsyncMock(return_value={'prompt': 'Context', 'lanes': [{'id': 'todo', 'name': 'Todo'}]})))
    assert response.status == 200
    assert json.loads(response.text)['show_agent_work'] is False
    assert 'task_board.show_agent_work' not in users.update_one.await_args.args[1]['$set']


@pytest.mark.asyncio
async def test_settings_restore(monkeypatch):
    users = SimpleNamespace(update_one=AsyncMock(), find_one=AsyncMock(return_value={'task_board': {'show_agent_work': False}}))
    monkeypatch.setattr(task_routes, 'get_db', lambda: SimpleNamespace(users=users))
    monkeypatch.setattr(task_routes, 'get_user_email', lambda _: 'owner@example.com')
    response = await task_routes.handle_put_board_settings(SimpleNamespace(json=AsyncMock(return_value={'prompt': 'Context', 'lanes': [{'id': 'todo', 'name': 'Todo'}], 'show_agent_work': True})))
    assert response.status == 200
    assert json.loads(response.text)['show_agent_work'] is True
    assert users.update_one.await_args.args[1]['$set']['task_board.show_agent_work'] is True


@pytest.mark.asyncio
async def test_anonymous_cannot_dismiss(monkeypatch):
    users = SimpleNamespace(update_one=AsyncMock())
    monkeypatch.setattr(task_routes, 'get_db', lambda: SimpleNamespace(users=users))
    monkeypatch.setattr(task_routes, 'get_user_email', lambda _: None)
    response = await task_routes.handle_put_board_settings(SimpleNamespace(json=AsyncMock(return_value={'show_agent_work': False})))
    assert response.status == 401
    users.update_one.assert_not_awaited()
