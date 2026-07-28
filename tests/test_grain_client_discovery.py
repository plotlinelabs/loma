import pytest

from tools import grain


@pytest.mark.asyncio
async def test_discovery_excludes_unrelated_grain_results(monkeypatch):
    async def search(_query):
        return {
            "recordings": [
                {
                    "id": "matching-title",
                    "title": "Okencasa onboarding kickoff",
                    "date": "2026-07-28T10:00:00Z",
                    "participants": [],
                },
                {
                    "id": "matching-contact",
                    "title": "Weekly implementation call",
                    "date": "2026-07-27T10:00:00Z",
                    "participants": [{"email": "owner@okencasa.com"}],
                },
                {
                    "id": "unrelated",
                    "title": "Another customer onboarding",
                    "date": "2026-07-29T10:00:00Z",
                    "participants": [{"email": "someone@other-client.com"}],
                },
            ]
        }

    monkeypatch.setattr(grain, "search_recordings", search)

    result = await grain.discover_client_recordings(
        "Okencasa", ["owner@okencasa.com", "csm@plotline.so"]
    )

    assert [row["id"] for row in result["recordings"]] == [
        "matching-title",
        "matching-contact",
    ]


@pytest.mark.asyncio
async def test_plotline_participants_do_not_match_every_client(monkeypatch):
    async def search(_query):
        return {
            "recordings": [{
                "id": "internal",
                "title": "Internal weekly review",
                "date": "2026-07-28T10:00:00Z",
                "participants": [{"email": "csm@plotline.so"}],
            }]
        }

    monkeypatch.setattr(grain, "search_recordings", search)

    result = await grain.discover_client_recordings(
        "Okencasa", ["csm@plotline.so"]
    )

    assert result == {"recordings": []}
