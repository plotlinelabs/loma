"""Catalog regressions without starting provider pools or database connections."""
import ast
from pathlib import Path

from agent.codex_runtime import DEFAULT_CODEX_MODEL, supported_codex_model_ids, normalize_codex_model

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ("anthropic/claude-opus-5-5", "codex/gpt-6-sol", "codex/gpt-6-astra")


def catalog_helpers():
    tree = ast.parse((ROOT / "api/routes.py").read_text())
    names = {"SUPPORTED_CLAUDE_MODEL_IDS", "FAVORITE_MODEL_IDS", "_recommended_model_rank", "_order_agent_models"}
    nodes = [n for n in tree.body if
             isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)
             or isinstance(n, ast.FunctionDef) and n.name in names]
    scope = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "api/routes.py", "exec"), scope)
    return scope


def test_catalog_support_and_existing_defaults(monkeypatch):
    monkeypatch.delenv("CODEX_MODELS", raising=False)
    scope = catalog_helpers()
    assert "claude-opus-5-5" in scope["SUPPORTED_CLAUDE_MODEL_IDS"]
    assert scope["SUPPORTED_CLAUDE_MODEL_IDS"][0] == "claude-opus-5"
    assert "gpt-6-sol" in supported_codex_model_ids([])
    assert "gpt-5.6-sol" in supported_codex_model_ids([])
    assert DEFAULT_CODEX_MODEL == "gpt-5.6-sol"
    assert normalize_codex_model("codex/gpt-6-sol") == "gpt-6-sol"


def test_live_discovery_and_explicit_override_remain_authoritative(monkeypatch):
    monkeypatch.delenv("CODEX_MODELS", raising=False)
    assert supported_codex_model_ids(["gpt-5.6-sol"]) == ("gpt-5.6-sol",)
    monkeypatch.setenv("CODEX_MODELS", "gpt-5.5,gpt-6-sol")
    assert supported_codex_model_ids(["gpt-6-astra"]) == ("gpt-5.5", "gpt-6-sol")


def test_favorites_order_and_no_invented_models():
    scope = catalog_helpers()
    assert scope["FAVORITE_MODEL_IDS"] == EXPECTED
    ids = ["opencode-go/glm-5.3-flash", EXPECTED[2], "codex/gpt-5.6-sol", EXPECTED[1], EXPECTED[0]]
    original = [{"id": mid} for mid in ids]
    ordered = scope["_order_agent_models"](original)
    assert [m["id"] for m in ordered] == list(EXPECTED) + [ids[0], ids[2]]
    assert [m["recommended"] for m in ordered] == [True, True, True, False, False]
    assert all("recommended" not in m for m in original)
    assert scope["_order_agent_models"]([]) == []
    assert scope["_order_agent_models"]([{"id": EXPECTED[0]}]) == [{"id": EXPECTED[0], "recommended": True}]
