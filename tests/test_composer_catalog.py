import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from api import routes, task_routes


def test_task_view_retains_selection_and_legacy_default():
    config = {"enabled_skills": [], "enabled_tools": ["Bash", "Read"]}
    task = {"conversation_id": "test", "tool_config": config}
    assert task_routes._task_view(task, ["todo"])["tool_config"] == config
    assert task_routes._task_view({"conversation_id": "legacy"}, ["todo"])["tool_config"] is None
    assert task_routes._TASK_PROJECTION["tool_config"] == 1


@pytest.mark.asyncio
async def test_catalog_includes_canonical_skill_organisation(monkeypatch):
    class Integrations:
        def find(self, query):
            async def rows():
                for row in []:
                    yield row
            return rows()
    monkeypatch.setattr(routes, "get_db", lambda: SimpleNamespace(integrations=Integrations()))
    monkeypatch.setattr(routes.skill_service, "list_skills", AsyncMock(return_value=[{
        "slug": "test", "name": "Test", "scope": "personal", "folder": "Engineering", "tags": ["qa"]
    }]))
    response = await routes.handle_available_tools(None)
    skill = json.loads(response.text)["skills"][0]
    assert skill["scope"] == "personal"
    assert skill["folder"] == "Engineering"
    assert skill["tags"] == ["qa"]
