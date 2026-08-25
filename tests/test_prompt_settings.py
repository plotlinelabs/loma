from agent.prompt import (
    build_pooled_system_prompt,
    build_system_prompt,
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


# --- prompt eval engine: the `overrides` param used by eval/prompt_subject.py ---

def test_overrides_replaces_one_section_without_touching_the_other():
    set_prompt_settings_cache({
        "identity_guidelines": "LIVE identity.",
        "company_information": "LIVE company info.",
    })

    draft = build_pooled_system_prompt(overrides={"identity_guidelines": "DRAFT identity."})

    assert "DRAFT identity." in draft
    assert "LIVE identity." not in draft
    assert "LIVE company info." in draft  # untouched section still comes from the cache


def test_overrides_never_mutates_the_shared_cache():
    set_prompt_settings_cache({"identity_guidelines": "LIVE identity.", "company_information": ""})

    build_pooled_system_prompt(overrides={"identity_guidelines": "DRAFT identity."})
    live_prompt_after = build_pooled_system_prompt()  # no overrides — reads the cache directly

    assert "LIVE identity." in live_prompt_after
    assert "DRAFT identity." not in live_prompt_after


def test_overrides_key_outside_rulebook_has_no_effect():
    # dictation_vocabulary is a PROMPT_SETTING_KEYS entry but not a
    # RULEBOOK_KEYS one (see agent/prompt.py) — it never enters the system
    # prompt at all, so overriding it must be a no-op here. This is the
    # exact bug eval/prompt_subject.py guards against by validating against
    # RULEBOOK_KEYS before ever calling this function.
    set_prompt_settings_cache({"identity_guidelines": "LIVE identity.", "company_information": ""})

    prompt = build_pooled_system_prompt(overrides={"dictation_vocabulary": "should not appear"})

    assert "should not appear" not in prompt
    assert "LIVE identity." in prompt
