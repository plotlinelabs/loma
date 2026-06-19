"""Built-in defaults for Mongo-backed prompt settings."""

DEFAULT_PROMPT_SETTINGS = {
    "identity_guidelines": (
        "You are Loma, a practical AI assistant for a company's internal team. "
        "Be concise, careful, and transparent about what you know. Ask clarifying "
        "questions when requirements are ambiguous, and prefer using connected tools "
        "over guessing."
    ),
    "company_information": (
        "Add your company's product context, terminology, repositories, support "
        "processes, and operating guidelines here from the Loma dashboard."
    ),
}


def get_default_prompt_setting(key: str) -> str:
    return DEFAULT_PROMPT_SETTINGS.get(key, "")
