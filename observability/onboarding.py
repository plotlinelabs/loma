"""Onboarding tracker: one record per customer app integration.

Collections:
  onboarding_records          - one doc per app/engagement, grouped by `account`
  onboarding_events           - append-only change log (who/what changed which field)
  onboarding_settings         - optional `{_id: "config"}` doc: the editable template
                                (stages, fields, module catalogue, rules)
  onboarding_settings_history - one snapshot per template save

Every field write records its source (`human` for dashboard edits, or a
connector name such as `hubspot` / `mongodb` for agent syncs). An automated
write never overwrites a value a human set unless `force=True`.

The template is data, not code: stage names, field lists, the module
catalogue and the rule thresholds are all edited from the dashboard. Code
only knows three stage *milestones* (`sdk_live`, `first_campaign`,
`adopting`), which the template attaches to whichever stages it wants.
"""

from __future__ import annotations

import copy
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

HUMAN = "human"

FIELD_TYPES = ("text", "longtext", "number", "date", "select", "multiselect", "link", "person")
# Who is expected to fill a field. Informational: drives the "Filled by" hint
# in the UI and tells sync agents which fields are theirs.
FIELD_SOURCES = ("human", "contract", "product", "hubspot", "billing", "grain")
MILESTONES = ("sdk_live", "first_campaign", "adopting")
# The four module layers. Their options always come from the module catalogue.
MODULE_LAYERS = ("modules_paid", "modules_enabled", "modules_integrated", "modules_in_use")
# Catalogue lists a multi-select field can take its options from.
OPTION_SOURCES = {"modules": "module", "integration_items": "integration item"}


def _m(key, label, group, enabled="", integrated="", usage="", requires=()) -> dict:
    """A sellable module: tracked as Paid / Enabled / Integrated / In use."""
    return {"key": key, "label": label, "group": group, "enabled_signal": enabled,
            "integrated_signal": integrated, "usage_signal": usage, "requires": list(requires)}


def _i(key, label, group, signal="", required=False) -> dict:
    """An integration item: work the client's team does. Tracked as In scope / Done."""
    return {"key": key, "label": label, "group": group, "signal": signal, "required": required}


def _f(key, label, ftype, section, source="human", **extra) -> dict:
    return {"key": key, "label": label, "type": ftype, "section": section, "source": source,
            **extra}


DEFAULT_CONFIG: dict[str, Any] = {
    "stages": [
        {"key": "signed", "label": "Signed - Not Started",
         "description": "Deal closed-won, nothing started yet"},
        {"key": "kickoff", "label": "Kickoff Done",
         "description": "Kickoff call held, dev dashboard provisioned, paid modules ticked"},
        {"key": "sdk", "label": "SDK Integration",
         "description": "Client is coding: basic SDK, user ID, events"},
        {"key": "advanced", "label": "Advanced Modules",
         "description": "PN, backend API, widgets, listeners in progress"},
        {"key": "qa", "label": "QA / UAT", "description": "Testing on the dev build"},
        {"key": "live", "label": "SDK Live on Prod", "milestone": "sdk_live",
         "description": "SDK sending data from the production app"},
        {"key": "first_campaign", "label": "First Campaign Live", "milestone": "first_campaign",
         "description": "First real campaign active on prod and reached the minimum users"},
        {"key": "pilot", "label": "Adopting / Pilot", "milestone": "adopting",
         "description": "Several campaigns live, or the pilot is running"},
        {"key": "handed_over", "label": "Handed Over to CSM", "terminal": True,
         "description": "Pilot review done, CSM owns the account"},
        {"key": "closed", "label": "Closed - Lost / Churned", "terminal": True,
         "description": "Exited at any stage"},
    ],
    # Catalogue, three levels: group -> module -> integration items.
    # A group that is not sellable (Core SDK) only holds integration items.
    "module_groups": [
        {"key": "core", "label": "Core SDK", "sellable": False},
        {"key": "in_app", "label": "In-app campaigns", "sellable": True},
        {"key": "outside_app", "label": "Outside-app campaigns", "sellable": True},
        {"key": "gamification", "label": "Gamification", "sellable": True},
        {"key": "analytics", "label": "Analytics", "sellable": True},
        {"key": "journeys", "label": "Journeys", "sellable": True},
        {"key": "voice_ai", "label": "Voice AI / AI Agents", "sellable": True},
        {"key": "ai_director", "label": "AI Director", "sellable": True},
    ],
    "modules": [
        # In-app campaigns
        _m("nudges", "Nudges", "in_app", "shouldEnableFlows", "A flow shown on prod",
           "flowsTable Type=flow"),
        _m("widgets", "Widgets", "in_app", "shouldEnableWidgets",
           "Widget placeholder renders on prod", "flowsTable Type=widget",
           requires=["widget_placeholder"]),
        _m("stories", "Stories", "in_app", "shouldEnableStories", "Story strip placed on prod",
           "flowsTable Type=story", requires=["story_placeholder"]),
        _m("surveys", "Surveys", "in_app", "Always on (no switch)", "A study shown on prod",
           "Study responses"),
        _m("feature_flags", "Feature Flags", "in_app", "shouldEnableFeatureFlags",
           "App reads a flag value", "Active flag"),
        _m("ai_decisioning", "AI Decisioning", "in_app", "shouldEnableSmartCampaigns", "",
           "Active smart campaign"),
        # Outside-app campaigns: one master switch, then one entry per channel
        _m("push", "Push", "outside_app",
           "shouldEnableOutsideAppCampaigns + PUSH in outsideAppChannelsEnabled",
           "PN tokens registered on prod", "Push sent", requires=["push_credentials"]),
        _m("web_push", "Web Push", "outside_app", "shouldEnableWebPush",
           "WEB_PUSH config with VAPID keys", "Web push sent", requires=["vapid_keys"]),
        _m("notification_center", "Notification Center", "outside_app",
           "Beta switch + channel enabled", "Inbox rendered in the app", "Inbox messages sent"),
        _m("email", "Email", "outside_app", "EMAIL in outsideAppChannelsEnabled",
           "Sender domain verified", "Email sent", requires=["email_sender"]),
        _m("whatsapp", "WhatsApp", "outside_app", "WHATSAPP in outsideAppChannelsEnabled",
           "Provider connected", "WhatsApp sent", requires=["whatsapp_provider"]),
        _m("sms", "SMS", "outside_app", "SMS in outsideAppChannelsEnabled",
           "Provider connected", "SMS sent", requires=["sms_provider"]),
        _m("rcs", "RCS", "outside_app", "RCS in outsideAppChannelsEnabled",
           "Provider connected", "RCS sent", requires=["rcs_provider"]),
        _m("webhook", "Webhook", "outside_app", "WEBHOOK in outsideAppChannelsEnabled",
           "Endpoint configured", "Webhook sent"),
        # Gamification: one switch for the whole group
        _m("luck_games", "Luck games", "gamification", "shouldEnableGamification",
           "A game shown on prod", "Gameplays"),
        _m("skill_games", "Skill games", "gamification",
           "shouldEnableGamification + beta switch", "A game shown on prod", "Gameplays"),
        _m("quizzes", "Quizzes", "gamification", "shouldEnableGamification",
           "A quiz shown on prod", "Quiz responses"),
        _m("streaks", "Streaks", "gamification", "shouldEnableGamification",
           "Streak widget renders on prod", "Active streak"),
        _m("milestones", "Milestones", "gamification", "shouldEnableGamification",
           "Milestone widget renders on prod", "Active milestone"),
        _m("rewards", "Rewards", "gamification", "shouldEnableGamification",
           "Published reward revealed on prod", "Rewards revealed",
           requires=["reward_fulfilment"]),
        # Analytics
        _m("campaign_analytics", "Campaign analytics", "analytics", "Always on (no switch)",
           "Metric events received", "Campaign reports viewed"),
        _m("product_analytics", "Product Analytics", "analytics", "shouldEnableProductAnalytics",
           "Events flowing to analytics", "Insights, funnels or boards created"),
        _m("session_replays", "Session Replays", "analytics", "shouldEnableProductAnalytics",
           "Replays recorded on prod", "Replays viewed"),
        # Journeys
        _m("journeys", "Journeys", "journeys", "shouldEnableJourneys", "",
           "Active journey with entries"),
        # Voice AI / AI Agents
        _m("voice_agents", "Voice agents", "voice_ai",
           "shouldEnableAIAgent + VOICE in outsideAppChannelsEnabled", "Agent published",
           "Calls placed"),
        _m("in_app_agents", "In-app AI agents", "voice_ai", "shouldEnableAIAgent",
           "Agent published", "Conversations"),
        # AI Director
        _m("ai_director", "AI Director", "ai_director", "shouldEnableAIDirector",
           "Objective configured", "AI runs"),
    ],
    "integration_items": [
        # Core SDK: needed before any module works
        _i("sdk_init", "SDK init", "core", "SDK calls from the prod app", required=True),
        _i("user_identify", "User identify", "core", "Users carry the client's user ID",
           required=True),
        _i("fe_events", "Front-end events", "core", "Track calls from the app", required=True),
        _i("user_attributes", "User attributes", "core", "Attributes set on users",
           required=True),
        _i("backend_events", "Backend events API", "core", "Server-side events received"),
        _i("cohort_sync", "Cohort sync", "core", "Cohorts imported through the API"),
        _i("event_forwarding", "Event forwarding", "core",
           "Events forwarded from the client's existing analytics or engagement tool"),
        _i("deeplink_listener", "Deeplink / redirect listener", "core",
           "Campaign button redirects open the right screen"),
        _i("event_listener", "Event listener", "core",
           "Client receives campaign events in the app"),
        _i("page_capture", "Page capture", "core", "Screens captured for tooltips and spotlights"),
        # Per-module setup
        _i("widget_placeholder", "Widget placeholders", "in_app",
           "A placeholder is in the app for each placement"),
        _i("story_placeholder", "Story placeholder", "in_app", "Story strip placed in the app"),
        _i("push_credentials", "Push credentials (FCM / APNs)", "outside_app",
           "Keys uploaded and tokens registering"),
        _i("ios_notification_ext", "iOS notification extension", "outside_app",
           "Rich push renders on iOS"),
        _i("vapid_keys", "Web push VAPID keys", "outside_app", "VAPID keys configured"),
        _i("email_sender", "Email domain / SMTP", "outside_app", "Sender domain verified"),
        _i("whatsapp_provider", "WhatsApp provider", "outside_app", "Provider credentials added"),
        _i("sms_provider", "SMS provider", "outside_app", "Provider credentials added"),
        _i("rcs_provider", "RCS provider", "outside_app", "Provider credentials added"),
        _i("reward_fulfilment", "Reward fulfilment", "gamification",
           "Client backend applies the reward to the user"),
    ],
    # Contract bundles: one click ticks these modules as Paid. A starting point
    # only, a person still confirms against the contract.
    "bundles": [
        {"key": "in_app_platform", "label": "In-App Platform",
         "modules": ["nudges", "widgets", "stories", "surveys", "campaign_analytics"]},
        {"key": "gamification_platform", "label": "In-app Platform (Gamification)",
         "modules": ["luck_games", "quizzes", "streaks", "milestones", "rewards"]},
        {"key": "surveys_only", "label": "Surveys", "modules": ["surveys"]},
        {"key": "off_app_platform", "label": "Off-App Platform",
         "modules": ["push", "web_push", "email", "whatsapp", "sms", "rcs"]},
        {"key": "engagement_platform", "label": "Engagement Platform",
         "modules": ["nudges", "widgets", "stories", "surveys", "campaign_analytics", "push",
                     "web_push", "email", "whatsapp", "sms", "rcs", "journeys"]},
    ],
    "fields": [
        # Status
        _f("owner", "Owner", "person", "Status", on_card=True),
        _f("health", "Account Health", "select", "Status",
           options=["Healthy", "At risk", "Paused", "Churned pre-live", "Churned post-live"]),
        _f("blocker_owner", "Blocker Owner", "select", "Status", on_card=True,
           options=["None", "Client eng", "Client business", "Internal eng", "Internal SA",
                    "Infosec-Legal"]),
        _f("next_step", "Next Step", "text", "Status", on_card=True),
        _f("next_step_due", "Next Step Due", "date", "Status", on_card=True),
        _f("target_go_live", "Target Go-Live", "date", "Status", on_card=True,
           required_from="kickoff"),
        # People
        _f("tech_contact", "Client Tech Lead", "text", "People", required_from="kickoff"),
        _f("business_owner", "Client Business Owner", "text", "People",
           required_from="kickoff"),
        # Success plan
        _f("success_criteria", "Success Criteria", "longtext", "Success plan",
           required_from="kickoff"),
        _f("pilot_start", "Pilot Start", "date", "Success plan"),
        _f("pilot_end", "Pilot End", "date", "Success plan"),
        _f("pilot_review_date", "Pilot Review", "date", "Success plan"),
        _f("pilot_outcome", "Pilot Outcome", "select", "Success plan",
           options=["Converted", "Extended", "Lost"]),
        # Timeline
        _f("contract_start", "Contract Start", "date", "Timeline", "contract"),
        _f("kickoff_date", "Kickoff", "date", "Timeline", required_from="kickoff"),
        _f("dev_provisioned", "Dev Provisioned", "date", "Timeline", "product"),
        _f("first_live", "SDK Live on Prod", "date", "Timeline", "product", on_card=True),
        _f("client_blocked_days", "Client-Blocked Days", "number", "Timeline"),
        # Adoption
        _f("first_campaign_live", "First Campaign Live", "date", "Adoption", "product",
           on_card=True),
        _f("first_campaign_name", "First Campaign", "text", "Adoption", "product"),
        _f("first_campaign_users", "First Campaign Users", "number", "Adoption", "product"),
        _f("live_campaigns", "Campaigns Live (30d)", "number", "Adoption", "product"),
        _f("dashboard_users_30d", "Client Dashboard Users (30d)", "number", "Adoption",
           "product"),
        _f("sdk_version", "SDK Version", "text", "Adoption", "product"),
        # Modules: four layers, options from the catalogue
        _f("modules_paid", "Paid", "multiselect", "Modules", "human", options_from="modules",
           required_from="kickoff"),
        _f("modules_enabled", "Enabled", "multiselect", "Modules", "product",
           options_from="modules"),
        _f("modules_integrated", "Integrated", "multiselect", "Modules", "product",
           options_from="modules"),
        _f("modules_in_use", "In use", "multiselect", "Modules", "product",
           options_from="modules"),
        # Integration scope
        _f("engagement_type", "Engagement Type", "select", "Integration",
           options=["New logo", "Existing - new app", "PN upsell", "Module upsell"]),
        _f("platforms", "Platforms", "multiselect", "Integration", on_card=True,
           options=["Android XML", "Android Compose", "iOS UIKit", "iOS SwiftUI",
                    "React Native", "Flutter", "Flutter Web", "Web", "Unity", "KMP"]),
        # Integration items: what this client agreed to build, and what is done.
        # "Pending" is worked out from these two, it is not stored.
        _f("integration_scope", "Integration Scope", "multiselect", "Integration",
           options_from="integration_items"),
        _f("integration_done", "Integration Done", "multiselect", "Integration", "product",
           options_from="integration_items"),
        _f("go_live_checklist", "Go-Live Checklist", "multiselect", "Integration",
           options=["Invoice paid", "Contract linked", "Metric events = ALL",
                    "PN verified on prod", "UAT sign-off"]),
        # Commercial
        _f("arr", "ARR ($)", "number", "Commercial", "hubspot"),
        _f("billing_status", "Billing Status", "select", "Commercial", "billing", on_card=True,
           options=["Not invoiced", "Invoiced", "Paid", "Overdue"]),
        _f("contract_mtu", "Contract MTU", "number", "Commercial", "contract"),
        _f("current_mtu", "Current MTU", "number", "Commercial", "product"),
        _f("renewal_date", "Renewal Date", "date", "Commercial", "contract"),
        _f("loss_reason", "Loss Reason", "select", "Commercial",
           options=["Infosec", "Priorities changed", "No POC", "Regulatory", "Not legit",
                    "Other"]),
        # Links
        _f("org_id", "Org ID", "text", "Links"),
        _f("product_ids", "Product IDs", "text", "Links"),
        _f("hubspot_url", "HubSpot", "link", "Links"),
        _f("notes", "Notes", "longtext", "Notes"),
    ],
    "rules": {
        # A campaign only counts as "first campaign live" once it reached this many users.
        "first_campaign_min_users": 100,
        # Campaigns live in the last 30 days needed to suggest "Adopting".
        "adopting_min_campaigns": 3,
        # Days after SDK go-live with no qualifying campaign before the card is flagged idle.
        "idle_days": 14,
        "stale_days": 7,
        "pilot_ending_days": 14,
    },
    # Minimum system role allowed to edit records / the template. "chatter" = everyone.
    "edit_min_role": "chatter",
    "template_min_role": "chatter",
}

TEMPLATE_KEYS = ("stages", "fields", "module_groups", "modules", "integration_items", "bundles",
                 "rules", "edit_min_role", "template_min_role")
CATALOGUE_KEYS = ("module_groups", "modules", "integration_items", "bundles")
# Columns stored on the record itself rather than under `fields`.
CORE_KEYS = ("name", "account", "stage")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_options(cfg: dict) -> dict:
    groups = [g["key"] for g in cfg.get("module_groups") or []]
    sellable = [g["key"] for g in cfg.get("module_groups") or [] if g.get("sellable", True)]
    for coll in ("modules", "integration_items"):
        for item in cfg.get(coll) or []:
            # A template saved before groups existed: park its rows in a real group.
            if item.get("group") not in groups and groups:
                item["group"] = (sellable or groups)[0]
    labels = {src: [x["label"] for x in cfg.get(src) or []] for src in OPTION_SOURCES}
    for field in cfg["fields"]:
        if field.get("options_from") in labels:
            field["options"] = labels[field["options_from"]]
    return cfg


async def get_config(db) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    override = await db.onboarding_settings.find_one({"_id": "config"})
    if override:
        for key in TEMPLATE_KEYS:
            # Bundles may be saved empty on purpose; everything else needs a value.
            if override.get(key) or (key == "bundles" and isinstance(override.get(key), list)):
                cfg[key] = copy.deepcopy(override[key])
        cfg["rules"] = {**DEFAULT_CONFIG["rules"], **(override.get("rules") or {})}
        cfg["version"] = override.get("version")
        cfg["updated_at"] = _iso(override.get("updated_at"))
        cfg["updated_by"] = override.get("updated_by")
    return _resolve_options(cfg)


def _field_map(cfg: dict) -> dict[str, dict]:
    return {f["key"]: f for f in cfg["fields"]}


# --------------------------------------------------------------------------- template

def _clean_options(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = raw.split(",")
    seen, out = set(), []
    for o in raw or []:
        text = str(o).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def validate_template(body: dict, stage_counts: dict[str, int]) -> dict:
    """Validate and normalise a template payload. Raises ValueError listing every problem."""
    from api.auth_helpers import ROLE_HIERARCHY  # local import keeps the service import-light

    errors: list[str] = []

    stages_in = body.get("stages")
    stages: list[dict] = []
    if not isinstance(stages_in, list) or not stages_in:
        errors.append("At least one stage is required")
        stages_in = []
    seen: set[str] = set()
    milestones_seen: set[str] = set()
    for i, s in enumerate(stages_in):
        key = str(s.get("key") or "").strip()
        label = str(s.get("label") or "").strip()
        if not _KEY_RE.match(key):
            errors.append(f"Stage {i + 1}: key must be lowercase letters, digits or _")
        elif key in seen:
            errors.append(f"Stage key '{key}' is used twice")
        if not label:
            errors.append(f"Stage {i + 1}: label is required")
        milestone = s.get("milestone") or None
        if milestone and milestone not in MILESTONES:
            errors.append(f"Stage '{label}': unknown milestone '{milestone}'")
        elif milestone and milestone in milestones_seen:
            errors.append(f"Milestone '{milestone}' is on more than one stage")
        if milestone:
            milestones_seen.add(milestone)
        seen.add(key)
        stage = {"key": key, "label": label,
                 "description": str(s.get("description") or "").strip()}
        if s.get("terminal"):
            stage["terminal"] = True
        if milestone:
            stage["milestone"] = milestone
        stages.append(stage)
    if stages and all(s.get("terminal") for s in stages):
        errors.append("At least one stage must be non-terminal")
    for key, count in stage_counts.items():
        if count and key not in seen:
            errors.append(f"Stage '{key}' still has {count} record(s). Move them before removing it")

    def _named(noun: str, i: int, raw: dict, keys: set[str], labels: set[str]) -> tuple[str, str]:
        key = str(raw.get("key") or "").strip()
        label = str(raw.get("label") or "").strip()
        if not _KEY_RE.match(key):
            errors.append(f"{noun} {i + 1}: key must be lowercase letters, digits or _")
        elif key in keys:
            errors.append(f"{noun} key '{key}' is used twice")
        if not label:
            errors.append(f"{noun} {i + 1}: label is required")
        elif label in labels:
            errors.append(f"{noun} label '{label}' is used twice")
        elif "," in label:
            # Values are stored as labels and lists are comma separated.
            errors.append(f"{noun} '{label}': label cannot contain a comma")
        keys.add(key)
        labels.add(label)
        return key, label

    groups: list[dict] = []
    gkeys: set[str] = set()
    glabels: set[str] = set()
    for i, g in enumerate(body.get("module_groups") or []):
        key, label = _named("Module group", i, g, gkeys, glabels)
        groups.append({"key": key, "label": label, "sellable": bool(g.get("sellable", True))})
    if not groups:
        errors.append("At least one module group is required")
    sellable = {g["key"] for g in groups if g["sellable"]}

    items: list[dict] = []
    ikeys: set[str] = set()
    ilabels: set[str] = set()
    for i, it in enumerate(body.get("integration_items") or []):
        key, label = _named("Integration item", i, it, ikeys, ilabels)
        group = str(it.get("group") or "").strip()
        if groups and group not in gkeys:
            errors.append(f"Integration item '{label or key}': unknown group '{group}'")
        items.append({"key": key, "label": label, "group": group,
                      "signal": str(it.get("signal") or "").strip(),
                      "required": bool(it.get("required"))})

    modules: list[dict] = []
    mkeys: set[str] = set()
    mlabels: set[str] = set()
    for i, m in enumerate(body.get("modules") or []):
        key, label = _named("Module", i, m, mkeys, mlabels)
        group = str(m.get("group") or "").strip()
        if groups and group not in gkeys:
            errors.append(f"Module '{label or key}': unknown group '{group}'")
        elif groups and group not in sellable:
            errors.append(f"Module '{label or key}': group '{group}' only holds integration "
                          "items. Pick a sellable group")
        requires = _clean_options(m.get("requires"))
        unknown = [r for r in requires if r not in ikeys]
        if unknown:
            errors.append(f"Module '{label or key}': unknown integration item(s) "
                          f"{', '.join(unknown)}")
        modules.append({"key": key, "label": label, "group": group,
                        **{k: str(m.get(k) or "").strip()
                           for k in ("enabled_signal", "integrated_signal", "usage_signal")},
                        "requires": requires})

    bundles: list[dict] = []
    bkeys: set[str] = set()
    blabels: set[str] = set()
    for i, b in enumerate(body.get("bundles") or []):
        key, label = _named("Bundle", i, b, bkeys, blabels)
        mods = _clean_options(b.get("modules"))
        unknown = [x for x in mods if x not in mkeys]
        if unknown:
            errors.append(f"Bundle '{label or key}': unknown module(s) {', '.join(unknown)}")
        bundles.append({"key": key, "label": label, "modules": mods})

    fields: list[dict] = []
    fkeys: set[str] = set()
    fields_in = body.get("fields")
    if not isinstance(fields_in, list) or not fields_in:
        errors.append("At least one field is required")
        fields_in = []
    for i, f in enumerate(fields_in):
        key = str(f.get("key") or "").strip()
        label = str(f.get("label") or "").strip()
        ftype = f.get("type")
        name = label or key or f"#{i + 1}"
        if not _KEY_RE.match(key):
            errors.append(f"Field {i + 1}: key must be lowercase letters, digits or _")
        elif key in fkeys or key in CORE_KEYS:
            errors.append(f"Field key '{key}' is used twice or reserved")
        if not label:
            errors.append(f"Field {i + 1}: label is required")
        if ftype not in FIELD_TYPES:
            errors.append(f"Field '{name}': unknown type '{ftype}'")
        source = f.get("source") or "human"
        if source not in FIELD_SOURCES:
            errors.append(f"Field '{name}': unknown source '{source}'")
        required_from = f.get("required_from") or None
        if required_from and required_from not in seen:
            errors.append(f"Field '{name}': required-from stage '{required_from}' does not exist")
        fkeys.add(key)
        field = {"key": key, "label": label, "type": ftype,
                 "section": str(f.get("section") or "Other").strip() or "Other",
                 "source": source}
        if f.get("on_card"):
            field["on_card"] = True
        if required_from:
            field["required_from"] = required_from
        options_from = "modules" if key in MODULE_LAYERS else f.get("options_from")
        if options_from in OPTION_SOURCES:
            if ftype != "multiselect":
                errors.append(f"Field '{name}': catalogue fields must be multi-select")
            field["options_from"] = options_from
        elif ftype in ("select", "multiselect"):
            field["options"] = _clean_options(f.get("options"))
            if not field["options"]:
                errors.append(f"Field '{name}': add at least one option")
        fields.append(field)

    rules = {}
    for key, default in DEFAULT_CONFIG["rules"].items():
        raw = (body.get("rules") or {}).get(key, default)
        try:
            value = int(raw)
            if value < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"Rule '{key}' must be a whole number of 0 or more")
            value = default
        rules[key] = value

    out = {"stages": stages, "module_groups": groups, "modules": modules,
           "integration_items": items, "bundles": bundles, "fields": fields, "rules": rules}
    for key in ("edit_min_role", "template_min_role"):
        role = body.get(key) or DEFAULT_CONFIG[key]
        if role not in ROLE_HIERARCHY:
            errors.append(f"Unknown role '{role}' for {key}")
        out[key] = role

    if errors:
        raise ValueError("; ".join(errors))
    return out


def _comparable(item: dict) -> dict:
    out = dict(item)
    if out.get("options_from"):
        out.pop("options", None)  # derived from the catalogue, not part of the template
    return out


def _template_diff(old: dict, new: dict) -> list[str]:
    """Short human summary of what a template save changed."""
    out = []
    for coll, noun in (("stages", "stage"), ("fields", "field"), ("module_groups", "module group"),
                       ("modules", "module"), ("integration_items", "integration item"),
                       ("bundles", "bundle")):
        before = {x["key"]: x for x in old.get(coll) or []}
        after = {x["key"]: x for x in new.get(coll) or []}
        for x in new.get(coll) or []:
            if x["key"] not in before:
                out.append(f"Added {noun} '{x['label']}'")
        for x in old.get(coll) or []:
            if x["key"] not in after:
                out.append(f"Removed {noun} '{x['label']}'")
        for x in new.get(coll) or []:
            if x["key"] in before and _comparable(x) != _comparable(before[x["key"]]):
                out.append(f"Edited {noun} '{x['label']}'")
        kept_old = [x["key"] for x in old.get(coll) or [] if x["key"] in after]
        kept_new = [x["key"] for x in new.get(coll) or [] if x["key"] in before]
        if kept_old != kept_new:
            out.append(f"Reordered {noun}s")
    for key, value in (new.get("rules") or {}).items():
        if (old.get("rules") or {}).get(key) != value:
            out.append(f"Rule {key}: {(old.get('rules') or {}).get(key)} -> {value}")
    for key in ("edit_min_role", "template_min_role"):
        if old.get(key) != new.get(key):
            out.append(f"{key}: {old.get(key)} -> {new.get(key)}")
    return out


async def save_template(db, body: dict, *, actor: str) -> tuple[dict, list[str]]:
    """Validate, store and snapshot a new template. Returns (config, change summary)."""
    counts: dict[str, int] = {}
    for rec in await list_records(db):
        counts[rec.get("stage")] = counts.get(rec.get("stage"), 0) + 1
    clean = validate_template(body, counts)
    old = await get_config(db)
    changes = _template_diff(old, clean)
    if not changes:
        return old, []
    version = int(old.get("version") or 0) + 1
    ts = now()
    doc = {**clean, "version": version, "updated_at": ts, "updated_by": actor}
    await db.onboarding_settings.update_one({"_id": "config"}, {"$set": doc}, upsert=True)
    await db.onboarding_settings_history.insert_one(
        {"version": version, "at": ts, "by": actor, "changes": changes, "snapshot": clean})
    return await get_config(db), changes


async def list_template_history(db, limit: int = 30) -> list[dict]:
    rows = await db.onboarding_settings_history.find({}, {"snapshot": 0}).sort(
        "version", -1).to_list(limit)
    return [{k: _iso(v) for k, v in r.items() if k not in ("_id", "snapshot")} for r in rows]


# --------------------------------------------------------------------------- values

def coerce(field: dict, value: Any) -> Any:
    """Normalise an incoming value to the field's type. Empty -> None."""
    if value is None or value == "" or value == []:
        return None
    ftype = field.get("type")
    if ftype == "number":
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field['key']}: expected a number")
        return int(num) if num.is_integer() else num
    if ftype == "date":
        text = str(value)[:10]
        try:
            date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{field['key']}: expected YYYY-MM-DD")
        return text
    if ftype == "multiselect":
        if isinstance(value, str):
            value = value.split(",")
        cleaned = _clean_options(value)
        noun = OPTION_SOURCES.get(field.get("options_from"))
        if noun:
            unknown = [v for v in cleaned if v not in (field.get("options") or [])]
            if unknown:
                raise ValueError(f"{field['key']}: unknown {noun}(s) {', '.join(unknown)}. "
                                 "Add them to the catalogue on the Template page first")
        return cleaned or None
    return str(value).strip() or None


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _aware(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _int(value: Any) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def module_gaps(fields: dict, catalogue: list[str]) -> dict[str, list[str]]:
    """Differences between the four module layers.

    Paid-based gaps are only reported once someone ticked the paid modules,
    so an empty "Paid" never reads as "nothing was bought".
    """
    layer = {k: set(fields.get(k) or []) for k in MODULE_LAYERS}
    paid, enabled = layer["modules_paid"], layer["modules_enabled"]
    integrated, in_use = layer["modules_integrated"], layer["modules_in_use"]
    order = {m: i for i, m in enumerate(catalogue)}

    def srt(items: set[str]) -> list[str]:
        return sorted(items, key=lambda m: (order.get(m, 999), m))

    return {
        "paid_not_integrated": srt(paid - integrated) if paid else [],
        "enabled_not_paid": srt(enabled - paid) if paid else [],
        "integrated_not_used": srt(integrated - in_use),
        "upsell": srt(set(catalogue) - paid) if paid else [],
    }


def upsell_groups(fields: dict, cfg: dict) -> list[str]:
    """Sellable groups with no paid module. Empty until Paid is ticked."""
    paid = set(fields.get("modules_paid") or [])
    if not paid:
        return []
    out = []
    for group in cfg.get("module_groups") or []:
        if not group.get("sellable", True):
            continue
        labels = {m["label"] for m in cfg.get("modules") or [] if m.get("group") == group["key"]}
        if labels and not labels & paid:
            out.append(group["label"])
    return out


def integration_status(fields: dict, cfg: dict) -> dict:
    """What the client still has to build.

    Needed = items marked required in the template + items ticked In scope on
    the record + the setup items of every paid module. Pending = needed - done.
    Nothing is reported until someone recorded scope or done items, so an
    untouched record never reads as "everything is pending".
    """
    items = cfg.get("integration_items") or []
    label = {i["key"]: i["label"] for i in items}
    order = {i["label"]: n for n, i in enumerate(items)}
    scope = set(fields.get("integration_scope") or [])
    done = set(fields.get("integration_done") or [])
    paid = set(fields.get("modules_paid") or [])
    tracked = bool(scope or done)

    needed = scope | {i["label"] for i in items if i.get("required")}
    module_setup: dict[str, list[str]] = {}
    for module in cfg.get("modules") or []:
        if module["label"] not in paid:
            continue
        setup = [label[k] for k in module.get("requires") or [] if k in label]
        needed.update(setup)
        missing = [x for x in setup if x not in done]
        if missing and tracked:
            module_setup[module["label"]] = missing

    def srt(values: set[str]) -> list[str]:
        return sorted(values, key=lambda v: (order.get(v, 999), v))

    return {
        "tracked": tracked,
        "needed": srt(needed),
        "pending": srt(needed - done) if tracked else [],
        "done_count": len(needed & done),
        "module_setup_pending": module_setup,
    }


def derive(record: dict, cfg: dict, today: date | None = None) -> dict:
    """Computed columns: days to live, first-campaign status, module gaps, flags, suggestion."""
    today = today or now().date()
    rules = {**DEFAULT_CONFIG["rules"], **(cfg.get("rules") or {})}
    f = record.get("fields") or {}
    stages = cfg["stages"]
    index = {s["key"]: i for i, s in enumerate(stages)}
    by_milestone = {s["milestone"]: s["key"] for s in stages if s.get("milestone")}
    terminal = {s["key"] for s in stages if s.get("terminal")}
    stage = record.get("stage")
    pos = index.get(stage, -1)
    active = stage not in terminal

    start = _parse_date(f.get("kickoff_date")) or _parse_date(f.get("dev_provisioned"))
    live = _parse_date(f.get("first_live"))
    gross = (live - start).days if start and live else None
    blocked = _int(f.get("client_blocked_days")) or 0
    net = max(gross - blocked, 0) if gross is not None else None

    # A first campaign only counts once it reached the minimum number of users,
    # so a test campaign shown to a handful of internal users does not qualify.
    first_campaign = _parse_date(f.get("first_campaign_live"))
    fc_users = _int(f.get("first_campaign_users"))
    fc_qualified = (bool(first_campaign) and fc_users is not None
                    and fc_users >= rules["first_campaign_min_users"])
    days_to_campaign = ((first_campaign - live).days
                        if fc_qualified and live and first_campaign >= live else None)
    days_live_idle = (today - live).days if live and not fc_qualified else None

    pilot_end = _parse_date(f.get("pilot_end"))
    due = _parse_date(f.get("next_step_due"))
    target = _parse_date(f.get("target_go_live"))
    updated = _aware(record.get("updated_at"))
    catalogue = [m["label"] for m in cfg.get("modules") or []]
    gaps = module_gaps(f, catalogue)
    gaps["upsell_groups"] = upsell_groups(f, cfg)
    integration = integration_status(f, cfg)
    sdk_live_pos = index.get(by_milestone.get("sdk_live", ""), 10**6)

    flags = []
    if active:
        if due and due < today:
            flags.append("overdue")
        if isinstance(updated, datetime) and (now() - updated).days >= rules["stale_days"]:
            flags.append("stale")
        if f.get("blocker_owner") not in (None, "", "None"):
            flags.append("blocked")
        if pilot_end and 0 <= (pilot_end - today).days <= rules["pilot_ending_days"]:
            flags.append("pilot_ending")
        if target and not live and target < today:
            flags.append("late")
        if days_live_idle is not None and days_live_idle > rules["idle_days"]:
            flags.append("idle")
        if gaps["paid_not_integrated"] and pos >= sdk_live_pos:
            flags.append("paid_gap")
        if gaps["enabled_not_paid"]:
            flags.append("unpaid_enabled")
        # Live on prod while agreed integration work is still open.
        if integration["pending"] and pos >= sdk_live_pos:
            flags.append("integration_gap")

    # Fields the template says must be filled by the current stage.
    missing = []
    if active:
        for field in cfg["fields"]:
            req = field.get("required_from")
            if req in index and pos >= index[req] and f.get(field["key"]) in (None, "", []):
                missing.append(field["key"])

    # Suggest the furthest milestone stage the data supports, never a step back.
    suggested = None
    if active:
        live_campaigns = _int(f.get("live_campaigns")) or 0
        checks = (("adopting", fc_qualified and live_campaigns >= rules["adopting_min_campaigns"]),
                  ("first_campaign", fc_qualified),
                  ("sdk_live", bool(live)))
        for milestone, ok in checks:
            key = by_milestone.get(milestone)
            if ok and key and index[key] > pos:
                suggested = key
                break

    contract_mtu, current_mtu = _int(f.get("contract_mtu")), _int(f.get("current_mtu"))
    return {
        "days_to_live_gross": gross,
        "days_to_live_net": net,
        "pilot_days_left": (pilot_end - today).days if pilot_end else None,
        "first_campaign_qualified": fc_qualified,
        "days_live_to_first_campaign": days_to_campaign,
        "days_live_without_campaign": days_live_idle,
        "mtu_usage_pct": (round(current_mtu * 100 / contract_mtu)
                          if contract_mtu and current_mtu is not None else None),
        "module_gaps": gaps,
        "integration": integration,
        "missing_required": missing,
        "suggested_stage": suggested,
        "flags": flags,
    }


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def serialize(record: dict, cfg: dict) -> dict:
    out = {k: v for k, v in record.items() if k != "_id"}
    for key in ("created_at", "updated_at"):
        out[key] = _iso(out.get(key))
    out["meta"] = {k: {**m, "at": _iso(m.get("at"))} for k, m in (out.get("meta") or {}).items()}
    out["derived"] = derive(record, cfg)
    return out


def serialize_event(event: dict) -> dict:
    out = {k: v for k, v in event.items() if k != "_id"}
    out["at"] = _iso(out.get("at"))
    return out


async def list_records(db) -> list[dict]:
    return await db.onboarding_records.find({"archived": {"$ne": True}}).sort(
        [("account_lower", 1), ("name_lower", 1)]).to_list(2000)


async def get_record(db, record_id: str) -> dict | None:
    return await db.onboarding_records.find_one({"record_id": record_id})


async def find_record(db, *, org_id: str | None = None, name: str | None = None) -> dict | None:
    """Match by app name (case-insensitive) first, then by org ID when unique.

    One org can own several app records, so an org-only match is used only
    when exactly one record carries that org ID.
    """
    if name:
        doc = await db.onboarding_records.find_one({"name_lower": name.strip().lower()})
        if doc:
            return doc
    if org_id:
        docs = await db.onboarding_records.find({"fields.org_id": org_id}).to_list(2)
        if len(docs) == 1:
            return docs[0]
    return None


async def apply_changes(db, record: dict, changes: dict, *, actor: str, source: str = HUMAN,
                        note: str | None = None, force: bool = False) -> tuple[dict, list, list]:
    """Write field/core changes onto a record and log one event.

    Returns (updated_record, applied_changes, skipped_fields). Raises
    ValueError on unknown fields/stages or bad values (nothing is written).
    """
    cfg = await get_config(db)
    fmap = _field_map(cfg)
    stage_keys = {s["key"] for s in cfg["stages"]}
    ts = now()
    fields = record.get("fields") or {}
    meta = record.get("meta") or {}
    sets: dict[str, Any] = {}
    applied: list[dict] = []
    skipped: list[str] = []

    for key, raw in changes.items():
        if key in CORE_KEYS:
            value = str(raw).strip() if raw is not None else ""
            if key == "stage" and value not in stage_keys:
                raise ValueError(f"Unknown stage '{value}'")
            if key in ("name", "account") and not value:
                raise ValueError(f"{key} cannot be empty")
            old = record.get(key)
            target = key
        elif key in fmap:
            value = coerce(fmap[key], raw)
            old = fields.get(key)
            target = f"fields.{key}"
        else:
            raise ValueError(f"Unknown field '{key}'")

        prev_source = (meta.get(key) or {}).get("source")
        if source != HUMAN and prev_source == HUMAN and not force and old not in (None, "", []):
            skipped.append(key)
            continue
        if value == old:
            continue
        sets[target] = value
        sets[f"meta.{key}"] = {"source": source, "by": actor, "at": ts}
        if key in ("name", "account"):
            sets[f"{key}_lower"] = value.lower()
        applied.append({"field": key, "old": old, "new": value})

    # Only real changes bump updated_at (drives the "stale" flag). A human note
    # with no changes is still logged as a comment; an automated no-op is not
    # logged at all, so a daily sync cannot keep every record looking fresh.
    log_note_only = bool(note) and source == HUMAN
    if applied or log_note_only:
        if applied:
            sets["updated_at"] = ts
            sets["updated_by"] = actor
            await db.onboarding_records.update_one({"record_id": record["record_id"]}, {"$set": sets})
        await db.onboarding_events.insert_one({
            "event_id": uuid.uuid4().hex[:12],
            "record_id": record["record_id"],
            "at": ts,
            "actor": actor,
            "source": source,
            "changes": applied,
            "note": note or None,
        })
    updated = await get_record(db, record["record_id"]) or record
    return updated, applied, skipped


async def create_record(db, data: dict, *, actor: str, source: str = HUMAN) -> dict:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    cfg = await get_config(db)
    stage = data.get("stage") or cfg["stages"][0]["key"]
    if stage not in {s["key"] for s in cfg["stages"]}:
        raise ValueError(f"Unknown stage '{stage}'")
    fmap = _field_map(cfg)
    unknown = [k for k in (data.get("fields") or {}) if k not in fmap]
    if unknown:
        raise ValueError(f"Unknown field(s): {', '.join(unknown)}")
    account = str(data.get("account") or name).strip()
    ts = now()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "record"
    doc = {
        "record_id": f"{slug}-{uuid.uuid4().hex[:6]}",
        "name": name, "name_lower": name.lower(),
        "account": account, "account_lower": account.lower(),
        "stage": stage,
        "fields": {}, "meta": {},
        "created_at": ts, "created_by": actor,
        "updated_at": ts, "updated_by": actor,
    }
    await db.onboarding_records.insert_one(doc)
    doc.pop("_id", None)
    updated, _, _ = await apply_changes(db, doc, dict(data.get("fields") or {}), actor=actor,
                                        source=source, note=data.get("note") or "Record created")
    return updated


async def list_events(db, record_id: str, limit: int = 200) -> list[dict]:
    return await db.onboarding_events.find({"record_id": record_id}).sort("at", -1).to_list(limit)
