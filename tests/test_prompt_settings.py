from agent.prompt import build_pooled_system_prompt, set_prompt_settings_cache


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
