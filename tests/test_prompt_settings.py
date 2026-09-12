import pytest

from agent.prompt import (
    build_pooled_system_prompt,
    build_reply_format_reminder,
    build_system_prompt,
    is_slack_source,
    set_loma_skill_index_cache,
    set_prompt_settings_cache,
)


def test_pooled_prompt_uses_mongo_backed_prompt_settings_cache():
    set_prompt_settings_cache({
        "identity_guidelines": "Be concise and useful.",
        "company_information": "Example builds workflow software.",
    })

    prompt = build_pooled_system_prompt()

    assert "# Identity & Guidelines" in prompt
    assert "Be concise and useful." in prompt
    assert "# Company Information" in prompt
    assert "Example builds workflow software." in prompt


def test_pooled_prompt_has_generic_fallback_without_prompt_settings():
    set_prompt_settings_cache({})

    prompt = build_pooled_system_prompt()

    assert "You are Loma, a helpful company assistant." in prompt


def test_pooled_prompt_includes_loma_skill_discovery_commands():
    set_loma_skill_index_cache("No Loma skills are configured yet.")

    prompt = build_pooled_system_prompt()

    assert "## Loma Skills" in prompt
    assert "python3 tools/loma_skills.py search --query QUERY" in prompt
    assert "python3 tools/loma_skills.py dump --slug SLUG" in prompt
    assert "use `dump --slug` instead of repeatedly calling `file`" in prompt
    assert "Do not use the built-in `Skill` tool" in prompt


def test_loma_skill_index_cache_appears_in_prompts():
    set_loma_skill_index_cache("- code-review: Review GitHub pull requests")

    pooled_prompt = build_pooled_system_prompt()
    dashboard_prompt = build_system_prompt(source="dashboard")

    assert "- code-review: Review GitHub pull requests" in pooled_prompt
    assert "- code-review: Review GitHub pull requests" in dashboard_prompt


def test_slack_reply_policy_applies_to_direct_and_pooled_prompts_only():
    for prompt in (build_system_prompt("slack"), build_pooled_system_prompt()):
        assert "Default to 2-3 short lines" in prompt
        assert "Keep investigations thorough internally" in prompt
        assert "when explicitly requested" in prompt
        assert "Never hide important information" in prompt
        assert "use the existing thread context" in prompt
        assert "take precedence over generic instructions" in prompt
    assert "Default to 2-3 short lines" not in build_system_prompt("dashboard")


def test_pooled_prompt_maps_every_slack_source_variant_to_slack_rules():
    prompt = build_pooled_system_prompt()

    assert "Any source starting with `slack`" in prompt
    for variant in ("`slack_mention`", "`slack_dm`", "`slack_flow`", "`slack_channel_*`"):
        assert variant in prompt
    assert "capped at about 5 short lines" in prompt
    assert "Do not copy their length, headers, or closing offers" in prompt


@pytest.mark.parametrize("source", ["slack", "slack_mention", "slack_dm", "slack_flow", "slack_channel_bugs", "slack_bugs"])
def test_slack_family_sources_get_reply_format_reminder(source):
    assert is_slack_source(source)
    reminder = build_reply_format_reminder(source)
    assert reminder.startswith("[Reply format: this is a Slack thread.")
    assert "at most 3 short lines" in reminder
    assert "Want me to...?" in reminder
    assert "follow-up" not in reminder


@pytest.mark.parametrize("source", ["dashboard", "telegram", "github_webhook", "draft_with_loma", "", None])
def test_non_slack_sources_get_no_reply_format_reminder(source):
    assert not is_slack_source(source)
    assert build_reply_format_reminder(source) == ""
    assert build_reply_format_reminder(source, has_thread_context=True) == ""


def test_followup_reminder_caps_explain_and_discourages_copying_earlier_replies():
    reminder = build_reply_format_reminder("slack_flow", has_thread_context=True)

    assert "follow-up in an existing thread" in reminder
    assert "capped at about 5 short lines" in reminder
    assert "do not copy their length or format" in reminder
    assert len(reminder) < 900
