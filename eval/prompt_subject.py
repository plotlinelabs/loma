"""The "Loma subject" adapter — the deep path.

Turns a Mongo-backed prompt-settings key + a draft string into the same two
prompt strings eval/runner.run_eval() takes for any subject. This is the
only file that knows Loma's system prompt is assembled from
agent.prompt.build_pooled_system_prompt(); the generic/custom subject
(dashboard/src/components) needs no backend adapter at all — the caller
just supplies two raw strings directly.
"""

from __future__ import annotations

from agent.prompt import RULEBOOK_KEYS, build_pooled_system_prompt


def loma_current_and_draft(setting_key: str, draft_text: str) -> tuple[str, str]:
    """current = the live assembled prompt; draft = the same assembly with
    ``setting_key`` swapped for ``draft_text``. Read-only — never touches the
    shared prompt cache (see agent.prompt.build_pooled_system_prompt's
    ``overrides`` param and DESIGN.md for why).

    Only RULEBOOK_KEYS (not the full PROMPT_SETTING_KEYS) are eval-able here:
    "dictation_vocabulary" is edited on the same Settings screen but never
    enters the system prompt (it only feeds /api/transcribe), so swapping it
    via overrides would silently produce identical current/draft prompts —
    a meaningless comparison, not a working eval.
    """
    if setting_key not in RULEBOOK_KEYS:
        raise ValueError(
            f"'{setting_key}' isn't part of the system prompt (only {RULEBOOK_KEYS} are) — "
            "nothing would differ between current and draft."
        )
    current = build_pooled_system_prompt()
    draft = build_pooled_system_prompt(overrides={setting_key: draft_text})
    return current, draft
