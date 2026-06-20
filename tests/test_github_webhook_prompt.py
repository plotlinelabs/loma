from pathlib import Path


def test_pr_review_prompt_uses_loma_skill_cli():
    source = Path("webhooks/github.py").read_text()
    old_skill_tool_instruction = "Use the " + "`Skill` tool"

    assert old_skill_tool_instruction not in source
    assert "python3 tools/loma_skills.py get --slug code-review" in source
    assert "After reading the code-review skill" in source
