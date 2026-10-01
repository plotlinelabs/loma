"""Slack Block Kit messages for offboarding proposals and their outcomes."""

from __future__ import annotations

from typing import Any

MAX_SECTION = 2900


def _section(text: str) -> dict[str, Any]:
    if len(text) > MAX_SECTION:
        text = text[: MAX_SECTION - 1] + "…"
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _emails(emails: list[str | None], limit: int = 8) -> str:
    shown = [e or "(no email)" for e in emails[:limit]]
    more = len(emails) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def target_name(summary: dict[str, Any]) -> str:
    if summary.get("org"):
        return summary["org"]["name"]
    return ", ".join(p["name"] for p in summary.get("products", [])) or "selected products"


def summary_text(summary: dict[str, Any]) -> str:
    totals = summary.get("totals", {})
    lines = [
        f"*{totals.get('campaigns', 0)}* campaigns to complete, *{totals.get('memberships', 0)}* memberships to remove, "
        f"*{totals.get('sdkKeys', 0)}* SDK keys and *{totals.get('apiSecrets', 0)}* API secrets to revoke "
        f"across *{totals.get('products', 0)}* products. Drafts are not touched.",
    ]
    for product in summary.get("products", []):
        kinds = ", ".join(f"{n} {kind}" for kind, n in sorted(product.get("campaignsByKind", {}).items())) or "no live campaigns"
        removing = product.get("membersToRemove", [])
        lines.append(
            f"• *{product['name']}*: {kinds}; remove {len(removing)} member(s)"
            + (f" ({_emails(removing)})" if removing else "")
            + f"; revoke {product.get('sdkKeysToRevoke', 0)} SDK key(s)"
        )
    org = summary.get("org")
    if org:
        lines.append(f"• *Org {org['name']}*: remove {len(org.get('membersToRemove', []))} member(s); keeping {_emails(org.get('membersToKeep', []))}")
    return "\n".join(lines)


def proposal_blocks(request: dict[str, Any]) -> list[dict[str, Any]]:
    summary = request["summary"]
    name = target_name(summary)
    rid = request["request_id"]
    return [
        _section(f":warning: *Offboard {name}?*\n{summary_text(summary)}"),
        _context(f"Requested by {request['requested_by']} · plan `{request['plan_id']}` · expires {request.get('plan_expires_at') or 'in 30 min'} · only approvers can confirm"),
        {
            "type": "actions",
            "block_id": f"offboard_{rid}",
            "elements": [
                {
                    "type": "button",
                    "action_id": f"offboard_confirm_{rid}",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "Confirm offboarding"},
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Offboard this customer?"},
                        "text": {"type": "mrkdwn", "text": f"This completes live campaigns, removes access and revokes SDK keys for *{name}*. It can be undone from the next message."},
                        "confirm": {"type": "plain_text", "text": "Offboard"},
                        "deny": {"type": "plain_text", "text": "Back"},
                        "style": "danger",
                    },
                },
                {"type": "button", "action_id": f"offboard_cancel_{rid}", "text": {"type": "plain_text", "text": "Cancel"}},
            ],
        },
    ]


def _result_text(result: dict[str, Any]) -> str:
    lines = []
    for product in result.get("products", []):
        skipped = product.get("campaignsSkipped", 0)
        lines.append(
            f"• *{product['name']}*: completed {product.get('campaignsCompleted', 0)} campaign(s)"
            + (f" ({skipped} skipped, changed since the plan)" if skipped else "")
            + f", removed {product.get('membersRemoved', 0)} member(s), revoked {product.get('sdkKeysRevoked', 0)} SDK key(s)"
            + f" and {product.get('apiSecretsRevoked', 0)} API secret(s)"
        )
    if result.get("orgMembersRemoved"):
        lines.append(f"• Org: removed {result['orgMembersRemoved']} member(s)")
    return "\n".join(lines) or "Nothing needed changing."


def applied_blocks(request: dict[str, Any], result: dict[str, Any], applied_by: str) -> list[dict[str, Any]]:
    name = target_name(request["summary"])
    rid = request["request_id"]
    return [
        _section(f":white_check_mark: *{name} offboarded* by {applied_by}\n{_result_text(result)}"),
        _context(f"Plan `{request['plan_id']}` · SDK keys stop working within ~2 minutes · Undo restores everything in this plan"),
        {
            "type": "actions",
            "block_id": f"offboard_done_{rid}",
            "elements": [{
                "type": "button",
                "action_id": f"offboard_undo_{rid}",
                "text": {"type": "plain_text", "text": "Undo"},
                "confirm": {
                    "title": {"type": "plain_text", "text": "Undo offboarding?"},
                    "text": {"type": "mrkdwn", "text": f"Restores campaigns, access and keys for *{name}* to how they were before."},
                    "confirm": {"type": "plain_text", "text": "Restore"},
                    "deny": {"type": "plain_text", "text": "Back"},
                },
            }],
        },
    ]


def status_blocks(request: dict[str, Any], headline: str, detail: str = "") -> list[dict[str, Any]]:
    blocks = [_section(f"{headline}\n{detail}".strip())]
    blocks.append(_context(f"{target_name(request['summary'])} · plan `{request['plan_id']}` · requested by {request['requested_by']}"))
    return blocks
