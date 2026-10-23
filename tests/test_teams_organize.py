"""Teams hold members + agents; skills and flows carry an optional team_id."""
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from aiohttp import web
from mongomock_motor import AsyncMongoMockClient

from api import flow_routes, governance_routes, routes, skill_service


class Request(dict):
    def __init__(self, body=None, match=None, email="admin@example.com", role="admin"):
        super().__init__(user_email=email, system_role=role)
        self.match_info = match or {}
        self.json = AsyncMock(return_value=body or {})
        self.query = {}


def _body(resp):
    return json.loads(resp.body)


@pytest_asyncio.fixture
async def db():
    db = AsyncMongoMockClient()[f"teams_{uuid.uuid4().hex}"]
    await db.users.insert_many([{"email": "a@example.com"}, {"email": "b@example.com"}])
    await db.agent_identities.insert_many([
        {"agent_id": "ag1", "name": "Support bot"},
        {"agent_id": "ag-gone", "name": "Old", "deleted": True},
    ])
    with patch.object(governance_routes, "get_db", return_value=db), \
            patch.object(routes, "get_db", return_value=db), \
            patch.object(flow_routes, "get_db", return_value=db):
        yield db


@pytest.mark.asyncio
async def test_create_team_slugifies_and_stores_members_and_agents(db):
    resp = await governance_routes.handle_create_team(
        Request({"name": "Customer Success", "members": ["a@example.com", "a@example.com"], "agents": ["ag1"]}))
    assert resp.status == 201
    team = _body(resp)["team"]
    assert team["team_id"] == "customer-success"
    assert team["members"] == ["a@example.com"] and team["agents"] == ["ag1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body,error", [
    ({"name": "X", "members": ["nobody@example.com"]}, "Unknown users"),
    ({"name": "X", "agents": ["ag-gone"]}, "Unknown agents"),
    ({"name": "   "}, "Team name"),
    ({"name": "X", "team_id": "Bad ID!"}, "Team ID"),
])
async def test_create_team_rejects_invalid_input(db, body, error):
    resp = await governance_routes.handle_create_team(Request(body))
    assert resp.status == 400 and error in _body(resp)["error"]


@pytest.mark.asyncio
async def test_update_team_agents_and_non_maintainer_is_blocked(db):
    await governance_routes.handle_create_team(Request({"name": "Sales"}))
    resp = await governance_routes.handle_update_team(
        Request({"agents": ["ag1"], "members": ["b@example.com"]}, {"team_id": "sales"}))
    assert _body(resp)["team"]["agents"] == ["ag1"]
    with pytest.raises(web.HTTPForbidden):
        await governance_routes.handle_update_team(Request({"agents": []}, {"team_id": "sales"}, role="operator"))


@pytest.mark.asyncio
async def test_directory_is_readable_by_any_user(db):
    await governance_routes.handle_create_team(Request({"name": "Sales", "members": ["a@example.com"]}))
    resp = await governance_routes.handle_team_directory(Request(role="chatter"))
    assert _body(resp)["teams"] == [{"team_id": "sales", "name": "Sales", "color": "#6B7280",
                                     "bg_color": "#F3F4F6", "members": ["a@example.com"], "agents": []}]


@pytest.mark.asyncio
async def test_skill_team_assign_clear_and_unknown_team(db):
    await governance_routes.handle_create_team(Request({"name": "Sales"}))
    await db.skills.insert_one({"slug": "pricing", "name": "pricing", "scope": "workspace", "created_by": "x@example.com"})
    with patch.object(routes, "_skill_error_response", side_effect=lambda e: web.json_response({"error": str(e)}, status=e.status)):
        resp = await routes.handle_update_skill_team(Request({"team_id": "sales"}, {"name": "pricing"}))
        assert _body(resp)["team_id"] == "sales"
        assert (await skill_service.list_skills(db))[0]["team_id"] == "sales"
        resp = await routes.handle_update_skill_team(Request({"team_id": "nope"}, {"name": "pricing"}))
        assert resp.status == 404
        resp = await routes.handle_update_skill_team(Request({"team_id": None}, {"name": "pricing"}))
        assert _body(resp)["team_id"] is None
        with pytest.raises(web.HTTPForbidden):
            await routes.handle_update_skill_team(Request({"team_id": "sales"}, {"name": "pricing"}, role="analyst"))


@pytest.mark.asyncio
async def test_owner_can_assign_own_personal_skill(db):
    await governance_routes.handle_create_team(Request({"name": "Sales"}))
    await db.skills.insert_one({"slug": "mine", "name": "mine", "scope": "personal", "created_by": "me@example.com"})
    resp = await routes.handle_update_skill_team(
        Request({"team_id": "sales"}, {"name": "mine"}, email="me@example.com", role="analyst"))
    assert _body(resp)["team_id"] == "sales"


@pytest.mark.asyncio
async def test_flow_organize_sets_team_and_folder(db):
    await governance_routes.handle_create_team(Request({"name": "Sales"}))
    await db.flows.insert_one({"flow_id": "f1", "name": "Digest", "visibility": "shared",
                               "created_by": {"source": "owner@example.com"}, "run_as": "owner@example.com"})
    resp = await flow_routes.handle_organize_flow(
        Request({"team_id": "sales", "folder": "  Reports "}, {"flow_id": "f1"}, role="maintainer"))
    flow = _body(resp)["flow"]
    assert flow["team_id"] == "sales" and flow["folder"] == "Reports"
    resp = await flow_routes.handle_organize_flow(Request({"team_id": "nope"}, {"flow_id": "f1"}))
    assert resp.status == 404
    resp = await flow_routes.handle_organize_flow(Request({}, {"flow_id": "f1"}))
    assert resp.status == 400
    with pytest.raises(web.HTTPForbidden):
        await flow_routes.handle_organize_flow(
            Request({"folder": "x"}, {"flow_id": "f1"}, email="other@example.com", role="operator"))


@pytest.mark.asyncio
async def test_deleting_team_unassigns_skills_and_flows(db):
    await governance_routes.handle_create_team(Request({"name": "Sales"}))
    await db.skills.insert_one({"slug": "s", "team_id": "sales"})
    await db.flows.insert_one({"flow_id": "f", "team_id": "sales"})
    resp = await governance_routes.handle_delete_team(Request(match={"team_id": "sales"}))
    assert resp.status == 200
    assert (await db.skills.find_one({"slug": "s"}))["team_id"] is None
    assert (await db.flows.find_one({"flow_id": "f"}))["team_id"] is None
