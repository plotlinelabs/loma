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


def mode_of(summary: dict[str, Any]) -> str:
    return summary.get("mode") or "offboard"


# (headline verb, confirm button, dialog title, dialog body, done headline, undo title)
_COPY = {
    "offboard": ("Offboard {name}?", "Confirm offboarding", "Offboard this customer?",
                 "This completes live campaigns, removes access and revokes SDK keys for *{name}*. It can be undone from the next message.",
                 "{name} offboarded", "Undo offboarding?"),
    "read_only": ("Make {name} view-only?", "Confirm view-only", "Turn off edit access?",
                  "Everyone in *{name}* keeps dashboard login but can only view. Campaigns keep running. It can be undone from the next message.",
                  "{name} is now view-only", "Undo view-only?"),
    "read_write": ("Restore edit access for {name}?", "Confirm edit access", "Turn edit access back on?",
                   "Lifts the view-only restriction for everyone in *{name}*.",
                   "Edit access restored for {name}", "Undo (make view-only again)?"),
}


_DIALOG_VERB = {"offboard": "Offboard", "read_only": "Make view-only", "read_write": "Restore edit access"}


def _copy(summary: dict[str, Any], index: int) -> str:
    return _COPY.get(mode_of(summary), _COPY["offboard"])[index].format(name=target_name(summary))


def proposal_title(summary: dict[str, Any]) -> str:
    return _copy(summary, 0)


def summary_text(summary: dict[str, Any]) -> str:
    access = summary.get("dashboardAccess")
    if mode_of(summary) != "offboard" and access:
        before = "view-only" if access.get("readOnlyBefore") else "editable"
        after = "view-only" if access.get("readOnlyAfter") else "editable"
        change = f"Dashboard: *{before}* → *{after}*." if before != after else f"Dashboard is already *{after}*; nothing will change."
        return f"{change} Campaigns, users and SDK keys are not touched. This only changes what the dashboard lets people edit."
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
    rid = request["request_id"]
    return [
        _section(f":warning: *{_copy(summary, 0)}*\n{summary_text(summary)}"),
        _context(f"Requested by {request['requested_by']} · plan `{request['plan_id']}` · expires {request.get('plan_expires_at') or 'in 30 min'} · only approvers can confirm"),
        {
            "type": "actions",
            "block_id": f"offboard_{rid}",
            "elements": [
                {
                    "type": "button",
                    "action_id": f"offboard_confirm_{rid}",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": _copy(summary, 1)},
                    "confirm": {
                        "title": {"type": "plain_text", "text": _copy(summary, 2)},
                        "text": {"type": "mrkdwn", "text": _copy(summary, 3)},
                        "confirm": {"type": "plain_text", "text": _DIALOG_VERB.get(mode_of(summary), "Offboard")},
                        "deny": {"type": "plain_text", "text": "Back"},
                        "style": "danger",
                    },
                },
                {"type": "button", "action_id": f"offboard_cancel_{rid}", "text": {"type": "plain_text", "text": "Cancel"}},
            ],
        },
    ]


def _result_text(result: dict[str, Any]) -> str:
    if "readOnlyAfter" in result:
        return "Dashboard is now *" + ("view-only" if result["readOnlyAfter"] else "editable") + "* for everyone in the org."
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
    summary = request["summary"]
    name = target_name(summary)
    rid = request["request_id"]
    is_offboard = mode_of(summary) == "offboard"
    note = "SDK keys stop working within ~2 minutes · " if is_offboard else "Dashboard users see the change on their next page load · "
    undo_body = (f"Restores campaigns, access and keys for *{name}* to how they were before." if is_offboard
                 else f"Puts dashboard edit access for *{name}* back to how it was before.")
    return [
        _section(f":white_check_mark: *{_copy(summary, 4)}* by {applied_by}\n{_result_text(result)}"),
        _context(f"Plan `{request['plan_id']}` · {note}Undo restores everything in this plan"),
        {
            "type": "actions",
            "block_id": f"offboard_done_{rid}",
            "elements": [{
                "type": "button",
                "action_id": f"offboard_undo_{rid}",
                "text": {"type": "plain_text", "text": "Undo"},
                "confirm": {
                    "title": {"type": "plain_text", "text": _copy(request["summary"], 5)},
                    "text": {"type": "mrkdwn", "text": undo_body},
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
