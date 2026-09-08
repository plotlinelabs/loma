"""Own-profile pet persistence and strict validation, without a live database."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.governance_routes import VALID_PETS, handle_update_my_pet


class Request(dict):
    def __init__(self, body, email="alice@example.com"):
        super().__init__(user_email=email)
        self.body = body

    async def json(self):
        return self.body


@pytest.mark.asyncio
@pytest.mark.parametrize("pet", VALID_PETS)
async def test_save_only_authenticated_user(pet):
    db = MagicMock()
    db.users.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    body = {"pet_id": pet, "visible": False, "animated": False}
    with patch("api.governance_routes.get_db", return_value=db):
        response = await handle_update_my_pet(Request(body))
    assert response.status == 200
    assert json.loads(response.body) == body
    args = db.users.update_one.call_args.args
    assert args[0] == {"email": "alice@example.com"}
    assert args[1]["$set"]["pet_preference"] == body
    assert set(args[1]["$set"]) == {"pet_preference", "updated_at"}


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, [], {}, {"pet_id": "cat"},
    {"pet_id": [], "visible": True, "animated": True},
    {"pet_id": "unknown", "visible": True, "animated": True},
    {"pet_id": "tabby", "visible": 1, "animated": True},
    {"pet_id": "tabby", "visible": True, "animated": "false"},
    {"pet_id": "tabby", "visible": True, "animated": True, "email": "bob@example.com"},
])
async def test_invalid_preferences_never_write(body):
    with patch("api.governance_routes.get_db") as get_db:
        response = await handle_update_my_pet(Request(body))
    assert response.status == 400
    get_db.assert_not_called()


@pytest.mark.asyncio
async def test_unauthenticated():
    with patch("api.governance_routes.get_db") as get_db:
        response = await handle_update_my_pet(Request({}, email=""))
    assert response.status == 401
    get_db.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_json():
    request = Request(None)
    request.json = AsyncMock(side_effect=ValueError("Invalid JSON"))
    assert (await handle_update_my_pet(request)).status == 400


@pytest.mark.asyncio
async def test_missing_user_and_unavailable_db():
    body = {"pet_id": "tabby", "visible": True, "animated": True}
    with patch("api.governance_routes.get_db", return_value=None):
        assert (await handle_update_my_pet(Request(body))).status == 503
    db = MagicMock()
    db.users.update_one = AsyncMock(return_value=MagicMock(matched_count=0))
    with patch("api.governance_routes.get_db", return_value=db):
        assert (await handle_update_my_pet(Request(body))).status == 404


def test_frontend_pet_catalog_matches_api():
    from pathlib import Path
    import re

    source = (Path(__file__).parents[1] / "dashboard/src/components/PetCompanion.tsx").read_text()
    assert set(re.findall(r'\{ id: "([a-z-]+)", name:', source)) == set(VALID_PETS)
