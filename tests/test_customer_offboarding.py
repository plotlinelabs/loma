from unittest.mock import AsyncMock, patch

import pytest

from customer_offboarding import actions, blocks, client
from tools import customer_admin

SUMMARY = {
    "org": {"id": "o1", "name": "Acme", "membersToRemove": ["a@acme.com"], "membersToKeep": ["ops@example.com"]},
    "products": [{
        "productId": "p1", "name": "Acme App", "campaignsByKind": {"flow": 2, "widget": 1},
        "membersToRemove": ["a@acme.com"], "sdkKeysToRevoke": 1,
    }],
    "totals": {"products": 1, "campaigns": 3, "memberships": 2, "sdkKeys": 1, "apiSecrets": 0},
}
REQUEST = {
    "request_id": "r1", "plan_id": "plan1", "status": "pending", "requested_by": "cs@example.com",
    "thread_ts": "1.0", "summary": SUMMARY, "plan_expires_at": "soon",
}


@pytest.fixture
def slack():
    bot = AsyncMock()
    return bot


@pytest.fixture
def fake_store():
    with patch.object(actions.store, "get_request", AsyncMock(return_value=dict(REQUEST))) as get_request, \
         patch.object(actions.store, "transition", AsyncMock(return_value=dict(REQUEST))) as transition, \
         patch.object(actions.store, "audit", AsyncMock()) as audit:
        yield {"get_request": get_request, "transition": transition, "audit": audit}


@pytest.fixture(autouse=True)
def approvers(monkeypatch):
    monkeypatch.setenv("CUSTOMER_ADMIN_APPROVERS", "Lead@example.com; boss@example.com")


def test_approver_emails_are_normalised():
    assert client.approver_emails() == {"lead@example.com", "boss@example.com"}


def test_proposal_has_confirm_dialog_and_cancel():
    built = blocks.proposal_blocks(REQUEST)
    buttons = built[-1]["elements"]
    assert [b["action_id"] for b in buttons] == ["offboard_confirm_r1", "offboard_cancel_r1"]
    assert buttons[0]["confirm"]["confirm"]["text"] == "Offboard"
    assert "3* campaigns" in built[0]["text"]["text"]
    assert "Acme App" in built[0]["text"]["text"]


@pytest.mark.asyncio
async def test_non_approver_cannot_confirm(slack, fake_store):
    with patch.object(actions.client, "apply_plan", AsyncMock()) as apply_plan:
        await actions.handle_confirm(None, slack, "r1", "U1", "cs@example.com", "C1", "9.9")
    apply_plan.assert_not_awaited()
    fake_store["transition"].assert_not_awaited()
    assert fake_store["audit"].await_args.args[2] == "confirm_denied"
    slack.chat_postEphemeral.assert_awaited_once()


@pytest.mark.asyncio
async def test_approver_confirm_applies_once_and_shows_undo(slack, fake_store):
    outcome = {"products": [{"name": "Acme App", "campaignsCompleted": 3, "membersRemoved": 1, "sdkKeysRevoked": 1}]}
    with patch.object(actions.client, "apply_plan", AsyncMock(return_value={"status": "APPLIED", "result": outcome})) as apply_plan:
        await actions.handle_confirm(None, slack, "r1", "U2", "LEAD@example.com", "C1", "9.9")
    apply_plan.assert_awaited_once_with("plan1", "lead@example.com")
    first, last = fake_store["transition"].await_args_list[0], fake_store["transition"].await_args_list[-1]
    assert first.args[2:4] == (["pending"], "applying")
    assert last.args[2:4] == (["applying"], "applied")
    final = slack.chat_update.await_args.kwargs["blocks"]
    assert final[-1]["elements"][0]["action_id"] == "offboard_undo_r1"


@pytest.mark.asyncio
async def test_double_click_does_not_apply_twice(slack, fake_store):
    fake_store["transition"].return_value = None
    with patch.object(actions.client, "apply_plan", AsyncMock()) as apply_plan:
        await actions.handle_confirm(None, slack, "r1", "U2", "lead@example.com", "C1", "9.9")
    apply_plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_plan_fails_without_undo(slack, fake_store):
    with patch.object(actions.client, "apply_plan", AsyncMock(return_value={"error": "plan expired; create a new plan", "status": 409})):
        await actions.handle_confirm(None, slack, "r1", "U2", "lead@example.com", "C1", "9.9")
    assert fake_store["transition"].await_args.args[3] == "failed"
    assert fake_store["transition"].await_args.kwargs["restorable"] is False
    final = slack.chat_update.await_args.kwargs["blocks"]
    assert all(block["type"] != "actions" for block in final)


@pytest.mark.asyncio
async def test_timeout_reconciles_with_service_state(slack, fake_store):
    with patch.object(actions.client, "apply_plan", AsyncMock(return_value={"error": "Failed to reach the customer-admin API: timeout"})), \
         patch.object(actions.client, "get_plan", AsyncMock(return_value={"status": "APPLIED", "result": {"products": []}})):
        await actions.handle_confirm(None, slack, "r1", "U2", "lead@example.com", "C1", "9.9")
    assert fake_store["transition"].await_args.args[3] == "applied"


@pytest.mark.asyncio
async def test_partial_failure_offers_undo(slack, fake_store):
    with patch.object(actions.client, "apply_plan", AsyncMock(return_value={"error": "apply failed after 1 product(s)", "status": 500})):
        await actions.handle_confirm(None, slack, "r1", "U2", "lead@example.com", "C1", "9.9")
    final = slack.chat_update.await_args.kwargs["blocks"]
    assert final[-1]["elements"][0]["action_id"] == "offboard_undo_r1"
    assert "failed part-way" in final[0]["text"]["text"]


@pytest.mark.asyncio
async def test_cancel_by_requester_or_approver_only(slack, fake_store):
    await actions.handle_cancel(None, slack, "r1", "U3", "someone@example.com", "C1", "9.9")
    fake_store["transition"].assert_not_awaited()
    await actions.handle_cancel(None, slack, "r1", "U4", "CS@example.com", "C1", "9.9")
    assert fake_store["transition"].await_args.args[2:4] == (["pending"], "cancelled")


@pytest.mark.asyncio
async def test_undo_restores_and_failed_restore_can_retry(slack, fake_store):
    fake_store["get_request"].return_value = {**REQUEST, "status": "applied"}
    with patch.object(actions.client, "restore_plan", AsyncMock(return_value={"status": "RESTORED", "restored": {"campaigns": 3}})) as restore:
        await actions.handle_undo(None, slack, "r1", "U2", "boss@example.com", "C1", "9.9")
    restore.assert_awaited_once_with("plan1", "boss@example.com")
    assert fake_store["transition"].await_args.args[3] == "restored"

    with patch.object(actions.client, "restore_plan", AsyncMock(return_value={"error": "boom", "status": 500})):
        await actions.handle_undo(None, slack, "r1", "U2", "boss@example.com", "C1", "9.9")
    assert fake_store["transition"].await_args.args[2:4] == (["restoring"], "applied")

    fake_store["transition"].reset_mock()
    await actions.handle_undo(None, slack, "r1", "U3", "cs@example.com", "C1", "9.9")
    fake_store["transition"].assert_not_awaited()


@pytest.mark.asyncio
async def test_client_sends_plan_requests(monkeypatch):
    monkeypatch.setattr(client, "_request", AsyncMock(return_value={"ok": True}))
    await client.create_plan("cs@example.com", org_id="o1", reason="churned")
    client._request.assert_awaited_with("POST", "/plans", {"requestedBy": "cs@example.com", "orgId": "o1", "reason": "churned"})
    await client.apply_plan("p/1", "lead@example.com")
    client._request.assert_awaited_with("POST", "/plans/p%2F1/apply", {"appliedBy": "lead@example.com"}, timeout=300)
    await client.search("acme pay")
    client._request.assert_awaited_with("GET", "/search?query=acme%20pay")


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def json(self, content_type=None):
        return self._body

    async def text(self):
        return str(self._body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, resp):
        self._resp = resp

    def request(self, *args, **kwargs):
        return self._resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_plan_error_field_is_not_a_request_error(monkeypatch):
    monkeypatch.setenv("CUSTOMER_ADMIN_API_SECRET", "s")
    monkeypatch.setenv("CUSTOMER_ADMIN_BASE_URL", "https://admin.example.com")
    body = {"planId": "p1", "status": "PLANNED", "error": None, "summary": SUMMARY}
    with patch.object(client.aiohttp, "ClientSession", lambda: _Session(_Resp(200, body))):
        plan = await client.get_plan("p1")
    assert "error" not in plan
    assert plan["run_error"] is None and plan["status"] == "PLANNED"

    with patch.object(client.aiohttp, "ClientSession", lambda: _Session(_Resp(409, {"success": False, "message": "plan is APPLIED"}))):
        failed = await client.apply_plan("p1", "lead@example.com")
    assert failed == {"error": "plan is APPLIED", "status": 409}


@pytest.mark.asyncio
async def test_client_reports_missing_integration(monkeypatch):
    monkeypatch.delenv("CUSTOMER_ADMIN_API_SECRET", raising=False)
    monkeypatch.delenv("CUSTOMER_ADMIN_BASE_URL", raising=False)
    with patch("tools._integration_key.get_integration_key", return_value=""), \
         patch("tools._integration_key.get_integration_extra", return_value=""):
        result = await client.get_plan("p1")
    assert "not connected" in result["error"]


@pytest.mark.asyncio
async def test_propose_refuses_stale_or_empty_plans():
    with patch.object(customer_admin.client, "get_plan", AsyncMock(return_value={"status": "APPLIED", "summary": SUMMARY})):
        assert "run preview again" in (await customer_admin.propose("p1", "cs@example.com", "C1", None))["error"]
    empty = {**SUMMARY, "totals": {"products": 1, "campaigns": 0, "memberships": 0, "sdkKeys": 0, "apiSecrets": 0}}
    with patch.object(customer_admin.client, "get_plan", AsyncMock(return_value={"status": "PLANNED", "summary": empty})):
        assert "nothing to offboard" in (await customer_admin.propose("p1", "cs@example.com", "C1", None))["error"]


@pytest.mark.asyncio
async def test_propose_needs_a_channel(monkeypatch):
    monkeypatch.setenv("CUSTOMER_ADMIN_APPROVAL_CHANNEL", "")
    with patch("tools._integration_key.get_integration_extra", return_value=""):
        assert "approval_channel" in (await customer_admin.propose("p1", "cs@example.com", None, None))["error"]


def test_cli_validates_arguments():
    assert customer_admin.main(["preview", "--org-id", "o1"]) == {"error": "--requested-by is required"}
    assert "--plan-id" in customer_admin.main(["propose", "--channel", "C1"])["error"]
    assert customer_admin.main(["status"]) == {"error": "--plan-id is required"}


def test_tool_exposes_no_apply_command():
    assert not hasattr(customer_admin, "apply")
    assert "apply" not in [line.split()[2] for line in customer_admin.__doc__.splitlines() if line.strip().startswith("python3 tools/")]
