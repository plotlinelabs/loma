"""Fixture script seam (Asset Library ticket 05)."""
import asyncio

import pytest
from mongomock_motor import AsyncMongoMockClient

from api import routes
from observability import assets as asset_store
from observability.assets import list_assets, resolve_asset_availability
from scripts.asset_library_fixture import (
    CONVERSATION_ID,
    DUAL_FILE_PROMPT,
    EXPECTED_NAMES,
    UNAVAILABLE_NAME,
    populate,
)


OWNER = "alice@example.test"


@pytest.fixture
def asset_db(monkeypatch):
    db = AsyncMongoMockClient()["loma_test"]
    monkeypatch.setattr(asset_store, "get_db", lambda: db)
    monkeypatch.setattr("scripts.asset_library_fixture.get_db", lambda: db)
    return db


@pytest.fixture
def served_dir(tmp_path, monkeypatch):
    served = tmp_path / "served"
    served.mkdir()
    monkeypatch.setattr(routes, "SERVED_FILES_DIR", served)
    monkeypatch.setattr(routes, "_served_files", {})
    return tmp_path


async def _await_persist_tasks():
    current = asyncio.current_task()
    pending = [
        task for task in asyncio.all_tasks()
        if task is not current and not task.done()
        and getattr(task.get_coro(), "cr_code", None)
        and task.get_coro().cr_code.co_name == "_persist_asset"
    ]
    if pending:
        await asyncio.gather(*pending)


@pytest.mark.asyncio
async def test_fixture_populates_expected_assets_including_one_unavailable(
    asset_db, served_dir,
):
    await populate(owner_email=OWNER)
    await _await_persist_tasks()

    rows = await list_assets(OWNER)
    assert {row["name"] for row in rows} == set(EXPECTED_NAMES)
    assert {row["conversation_id"] for row in rows} == {CONVERSATION_ID}
    assert all(row["owner_email"] == OWNER for row in rows)
    assert all(row["source"] == "opencode" for row in rows)

    conversation = await asset_db.conversations.find_one(
        {"conversation_id": CONVERSATION_ID},
    )
    assert conversation is not None
    assert conversation["prompt"] == DUAL_FILE_PROMPT
    assert conversation["metadata"]["user_name"] == OWNER

    statuses = {
        row["name"]: (await resolve_asset_availability(row["file_id"]))[0]
        for row in rows
    }
    unavailable = [name for name, status in statuses.items() if status != "available"]
    assert unavailable == [UNAVAILABLE_NAME]
    available = [name for name, status in statuses.items() if status == "available"]
    assert set(available) == set(EXPECTED_NAMES) - {UNAVAILABLE_NAME}


@pytest.mark.asyncio
async def test_fixture_second_run_leaves_the_set_unchanged(asset_db, served_dir):
    await populate(owner_email=OWNER)
    await _await_persist_tasks()
    first = await list_assets(OWNER)
    first_ids = [row["file_id"] for row in first]

    await populate(owner_email=OWNER)
    await _await_persist_tasks()
    second = await list_assets(OWNER)

    assert [row["file_id"] for row in second] == first_ids
    assert {row["name"] for row in second} == set(EXPECTED_NAMES)
    unavailable = [
        row["name"] for row in second
        if (await resolve_asset_availability(row["file_id"]))[0] != "available"
    ]
    assert unavailable == [UNAVAILABLE_NAME]
