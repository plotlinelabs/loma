"""Onboarding tracker tool - read and update the Loma Onboarding section.

Use this to keep customer integration records current from any source the
agent can already reach (HubSpot, product databases, billing tools,
Zoho, Grain). Pass --source with the connector name so the dashboard shows
where each value came from. Automated sources never overwrite a value a
human typed unless --force is given.

Commands:
  onboarding.py --auth-token T --user-email E config
  onboarding.py --auth-token T --user-email E list [--stage KEY] [--flag overdue|stale|blocked|late|pilot_ending|idle|paid_gap|unpaid_enabled]
  onboarding.py --auth-token T --user-email E get (--record-id ID | --name NAME | --org-id ORG)
  onboarding.py --auth-token T --user-email E update (--record-id ID | --name NAME | --org-id ORG)
      --set key=value [--set key=value ...] [--source SRC] [--note TEXT] [--force]
  onboarding.py --auth-token T --user-email E create --name NAME [--account ACCOUNT]
      [--stage KEY] [--set key=value ...] [--source SRC]

Field keys, stage keys, the module catalogue and rule thresholds come from
`config` (edited by people on the Onboarding > Template page). Each field's
`source` says who owns it; only fill fields whose source matches yours.

Module layers: modules_paid is ticked by a person from the contract. Sync
modules_enabled (product shouldEnable* switches), modules_integrated and
modules_in_use using the module catalogue labels and signals.

First campaign: set first_campaign_live (campaign start date),
first_campaign_users and live_campaigns. It only counts once users reach
rules.first_campaign_min_users. For upsell records (engagement_type
"PN upsell" / "Module upsell") fill these for the upsold module only, not
the app's first campaign.

Field keys and stage keys come from `config`. Multiselect values are
comma-separated; dates are YYYY-MM-DD; an empty value clears a field.
"""

import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _verify_auth(auth_token: str, user_email: str) -> bool:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _auth_token import verify_user_auth_token
    return verify_user_auth_token(auth_token, user_email)


def _get_client():
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        raise ValueError("OBSERVABILITY_MONGODB_URI environment variable is not set")
    db_name = os.environ.get("OBSERVABILITY_DB_NAME", "loma_observability").strip()
    client = AsyncIOMotorClient(uri)
    return client, client[db_name]


def _values(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == flag and i + 1 < len(args)]


def _single(args: list[str], flag: str, default=None):
    vals = _values(args, flag)
    return vals[0] if vals else default


def _parse_sets(args: list[str]) -> dict:
    changes = {}
    for item in _values(args, "--set"):
        if "=" not in item:
            raise ValueError(f"--set expects key=value, got '{item}'")
        key, value = item.split("=", 1)
        changes[key.strip()] = value
    return changes


async def _resolve(db, args):
    from observability import onboarding as svc
    record_id = _single(args, "--record-id")
    if record_id:
        return await svc.get_record(db, record_id)
    return await svc.find_record(db, org_id=_single(args, "--org-id"), name=_single(args, "--name"))


async def _run(user_email: str, command: str, args: list[str]) -> dict:
    from observability import onboarding as svc

    client, db = _get_client()
    try:
        cfg = await svc.get_config(db)
        if command == "config":
            return cfg
        if command == "list":
            stage, flag = _single(args, "--stage"), _single(args, "--flag")
            rows = []
            for r in await svc.list_records(db):
                s = svc.serialize(r, cfg)
                if stage and s["stage"] != stage:
                    continue
                if flag and flag not in s["derived"]["flags"]:
                    continue
                rows.append({k: s.get(k) for k in ("record_id", "name", "account", "stage",
                                                   "fields", "derived", "updated_at")})
            return {"count": len(rows), "records": rows}
        if command == "get":
            record = await _resolve(db, args)
            if not record:
                return {"error": "Record not found"}
            events = await svc.list_events(db, record["record_id"], 20)
            return {"record": svc.serialize(record, cfg),
                    "recent_events": [svc.serialize_event(e) for e in events]}
        if command == "update":
            record = await _resolve(db, args)
            if not record:
                return {"error": "Record not found"}
            updated, applied, skipped = await svc.apply_changes(
                db, record, _parse_sets(args), actor=user_email,
                source=_single(args, "--source", "agent"), note=_single(args, "--note"),
                force="--force" in args)
            return {"record_id": updated["record_id"], "applied": applied,
                    "skipped_human_values": skipped}
        if command == "create":
            data = {"name": _single(args, "--name"), "account": _single(args, "--account"),
                    "stage": _single(args, "--stage"), "fields": _parse_sets(args)}
            record = await svc.create_record(db, data, actor=user_email,
                                             source=_single(args, "--source", "agent"))
            return {"created": True, "record_id": record["record_id"]}
        return {"error": f"Unknown command: {command}"}
    finally:
        client.close()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    argv = sys.argv[1:]
    auth_token = _single(argv, "--auth-token")
    user_email = _single(argv, "--user-email")
    if not auth_token or not user_email:
        print(json.dumps({"error": "Missing required --auth-token and --user-email arguments"}))
        sys.exit(1)
    if not _verify_auth(auth_token, user_email):
        print(json.dumps({"error": "Authentication failed. The auth token is invalid, expired, or "
                                   "doesn't match the user email."}))
        sys.exit(1)

    rest, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in ("--auth-token", "--user-email"):
            skip = True
            continue
        rest.append(a)
    if not rest:
        print(__doc__)
        sys.exit(1)
    try:
        result = asyncio.run(_run(user_email, rest[0], rest[1:]))
    except ValueError as exc:
        result = {"error": str(exc)}
    print(json.dumps(result, indent=2, default=str))
    if isinstance(result, dict) and "error" in result:
        sys.exit(1)
