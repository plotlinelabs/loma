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
    assert "Do not use the built-in `Skill` tool" in prompt


def test_loma_skill_index_cache_appears_in_prompts():
    set_loma_skill_index_cache("- code-review: Review GitHub pull requests")

    pooled_prompt = build_pooled_system_prompt()
    dashboard_prompt = build_system_prompt(source="dashboard")

    assert "- code-review: Review GitHub pull requests" in pooled_prompt
    assert "- code-review: Review GitHub pull requests" in dashboard_prompt
