"""Nested chat folders: unlimited nesting, move, cascade delete and link sharing."""

import json
from unittest.mock import patch

import pytest

from api.project_routes import (
    conversation_in_shared_project,
    handle_create_project,
    handle_delete_project,
    handle_get_project,
    handle_list_projects,
    handle_share_project,
    handle_update_project,
)

OWNER = "owner@example.com"
OTHER = "teammate@example.com"


def _matches(doc, query):
    for key, cond in query.items():
        value = doc.get(key)
        if isinstance(cond, dict):
            if "$ne" in cond and value == cond["$ne"]:
                return False
            if "$in" in cond and value not in cond["$in"]:
                return False
        elif value != cond:
            return False
    return True


class Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction):
        self.docs.sort(key=lambda d: str(d.get(key) or ""), reverse=direction < 0)
        return self

    async def to_list(self, _limit):
        return self.docs


class Collection:
    def __init__(self, docs=()):
        self.docs = [dict(d) for d in docs]

    async def insert_one(self, doc):
        self.docs.append(doc)

    async def find_one(self, query, _projection=None):
        return next((d for d in self.docs if _matches(d, query)), None)

    def find(self, query, _projection=None):
        return Cursor([d for d in self.docs if _matches(d, query)])

    async def update_one(self, query, update):
        doc = await self.find_one(query)
        if doc:
            doc.update(update["$set"])

    async def update_many(self, query, update):
        for doc in self.docs:
            if _matches(doc, query):
                doc.update(update["$set"])

    def aggregate(self, _pipeline):
        return Cursor([])


class FakeDB:
    def __init__(self, projects=(), conversations=()):
        self.projects = Collection(projects)
        self.conversations = Collection(conversations)


class FakeRequest(dict):
    def __init__(self, *, user_email=OWNER, role="chatter", body=None, project_id=None):
        super().__init__(user_email=user_email, system_role=role)
        self.match_info = {"project_id": project_id}
        self._body = body

    async def json(self):
        return self._body


def folder(pid, parent=None, owner=OWNER, visibility="private"):
    return {"project_id": pid, "name": pid, "parent_id": parent, "created_by": owner,
            "visibility": visibility, "deleted": False}


def chain(depth):
    """f0 > f1 > ... > f{depth-1}"""
    return [folder(f"f{i}", f"f{i - 1}" if i else None) for i in range(depth)]


async def call(handler, db, **kwargs):
    with patch("api.project_routes.get_db", return_value=db):
        response = await handler(FakeRequest(**kwargs))
    return response.status, json.loads(response.body)


@pytest.mark.asyncio
async def test_folders_nest_without_a_depth_limit():
    db = FakeDB()
    parent_id = None
    for level in range(12):
        status, data = await call(
            handle_create_project, db, body={"name": f"Level {level}", "parent_id": parent_id})
        assert status == 201
        assert data["project"]["parent_id"] == parent_id
        parent_id = data["project"]["project_id"]

    status, data = await call(handle_get_project, db, project_id=parent_id)
    assert status == 200
    assert [b["name"] for b in data["breadcrumbs"]] == [f"Level {i}" for i in range(11)]


@pytest.mark.asyncio
async def test_cannot_create_a_sub_folder_under_someone_elses_folder():
    db = FakeDB([folder("theirs", owner=OTHER, visibility="shared")])
    status, _ = await call(handle_create_project, db, body={"name": "Mine", "parent_id": "theirs"})
    assert status == 404


@pytest.mark.asyncio
async def test_list_returns_only_own_folders_even_for_admin():
    db = FakeDB([folder("mine"), folder("theirs", owner=OTHER)])
    _, data = await call(handle_list_projects, db, role="admin")
    assert [p["project_id"] for p in data["projects"]] == ["mine"]


@pytest.mark.asyncio
async def test_move_folder_and_reject_cycles():
    db = FakeDB(chain(3) + [folder("other")])

    status, data = await call(handle_update_project, db, project_id="f2", body={"parent_id": "other"})
    assert status == 200 and data["project"]["parent_id"] == "other"

    # Into itself, and into its own descendant
    assert (await call(handle_update_project, db, project_id="f0", body={"parent_id": "f0"}))[0] == 400
    assert (await call(handle_update_project, db, project_id="f0", body={"parent_id": "f1"}))[0] == 400

    status, data = await call(handle_update_project, db, project_id="f1", body={"parent_id": None})
    assert status == 200 and data["project"]["parent_id"] is None


@pytest.mark.asyncio
async def test_delete_cascades_to_sub_folders_and_keeps_chats():
    db = FakeDB(chain(3) + [folder("keep")], [
        {"conversation_id": "c0", "project_id": "f0"},
        {"conversation_id": "c2", "project_id": "f2"},
        {"conversation_id": "ck", "project_id": "keep"},
    ])
    status, _ = await call(handle_delete_project, db, project_id="f0")
    assert status == 200
    assert {p["project_id"] for p in db.projects.docs if p["deleted"]} == {"f0", "f1", "f2"}
    assert {c["conversation_id"]: c["project_id"] for c in db.conversations.docs} == {
        "c0": None, "c2": None, "ck": "keep"}


@pytest.mark.asyncio
async def test_private_folder_is_hidden_from_other_users():
    db = FakeDB(chain(2))
    status, _ = await call(handle_get_project, db, user_email=OTHER, project_id="f1")
    assert status == 404


@pytest.mark.asyncio
async def test_only_owner_can_share_not_even_admin():
    db = FakeDB(chain(1))
    status, _ = await call(
        handle_share_project, db, user_email=OTHER, role="admin", project_id="f0", body={"shared": True})
    assert status == 403
    assert db.projects.docs[0]["visibility"] == "private"


@pytest.mark.asyncio
async def test_sharing_a_folder_gives_read_only_access_to_its_whole_subtree():
    # f0 (private) > f1 (shared) > f2 > f3
    db = FakeDB(chain(4), [{"conversation_id": "deep", "project_id": "f3"},
                           {"conversation_id": "above", "project_id": "f0"}])
    status, data = await call(handle_share_project, db, project_id="f1", body={"shared": True})
    assert status == 200 and data["shared"] is True

    status, data = await call(handle_get_project, db, user_email=OTHER, project_id="f3")
    assert status == 200
    assert data["can_manage"] is False
    assert [c["conversation_id"] for c in data["conversations"]] == ["deep"]
    # Breadcrumbs stop at the shared folder; the private parent f0 is not disclosed.
    assert [b["project_id"] for b in data["breadcrumbs"]] == ["f1", "f2"]

    assert (await call(handle_get_project, db, user_email=OTHER, project_id="f0"))[0] == 404
    assert (await call(handle_update_project, db, user_email=OTHER, project_id="f3",
                       body={"name": "hijack"}))[0] == 404
    assert (await call(handle_delete_project, db, user_email=OTHER, project_id="f3"))[0] == 404

    assert await conversation_in_shared_project(db, db.conversations.docs[0]) is True
    assert await conversation_in_shared_project(db, db.conversations.docs[1]) is False

    await call(handle_share_project, db, project_id="f1", body={"shared": False})
    assert (await call(handle_get_project, db, user_email=OTHER, project_id="f3"))[0] == 404
    assert await conversation_in_shared_project(db, db.conversations.docs[0]) is False
