"""Catalog regressions without starting provider pools or database connections."""
import ast
from pathlib import Path

from agent.codex_runtime import DEFAULT_CODEX_MODEL, supported_codex_model_ids, normalize_codex_model

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ("anthropic/claude-opus-5-5", "codex/gpt-6-sol", "codex/gpt-6-astra")


def catalog_helpers():
    tree = ast.parse((ROOT / "api/routes.py").read_text())
    names = {"SUPPORTED_CLAUDE_MODEL_IDS", "FAVORITE_MODEL_IDS", "_recommended_model_rank", "_order_agent_models", "_catalog_default_model"}
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
    assert scope["SUPPORTED_CLAUDE_MODEL_IDS"] == ("claude-opus-5-5", "claude-fable-5-1")
    assert supported_codex_model_ids([]) == ("gpt-6-sol", "gpt-6-luna", "gpt-6-astra", "gpt-5.6-sol")
    assert "gpt-6-sol" in supported_codex_model_ids([])
    assert "gpt-5.6-sol" in supported_codex_model_ids([])
    assert DEFAULT_CODEX_MODEL == "gpt-5.6-sol"
    assert normalize_codex_model("codex/gpt-6-sol") == "gpt-6-sol"


def test_live_discovery_and_explicit_override_remain_authoritative(monkeypatch):
    monkeypatch.delenv("CODEX_MODELS", raising=False)
    assert supported_codex_model_ids(["gpt-5.6-sol"]) == ("gpt-5.6-sol",)
    monkeypatch.setenv("CODEX_MODELS", "gpt-5.5,gpt-6-sol")
    assert supported_codex_model_ids(["gpt-6-astra"]) == ("gpt-6-sol",)


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


def test_live_catalog_cannot_reintroduce_removed_models(monkeypatch):
    monkeypatch.delenv("CODEX_MODELS", raising=False)
    assert supported_codex_model_ids(["gpt-5.5", "gpt-6-luna", "gpt-6-luna"]) == ("gpt-6-luna",)
    assert supported_codex_model_ids(["gpt-5.5"]) == ()
    monkeypatch.setenv("CODEX_MODELS", "gpt-5.5")
    assert supported_codex_model_ids(["gpt-6-sol"]) == ()


def test_removed_default_falls_back_to_available_model():
    choose = catalog_helpers()["_catalog_default_model"]
    models = [{"id": "anthropic/claude-opus-5-5"}, {"id": "codex/gpt-5.6-sol"}]
    assert choose("anthropic/claude-opus-5", models) == models[0]["id"]
    assert choose("codex/gpt-5.6-sol", models) == "codex/gpt-5.6-sol"
    assert choose("removed", []) == ""


def test_local_catalog_keeps_all_discovered_opencode_models(monkeypatch):
    import asyncio
    import logging
    import os
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    tree = ast.parse((ROOT / "api/routes.py").read_text())
    handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_agent_models")
    discovered = [
        {"id": f"{provider}/{model}", "provider_id": provider, "model_id": model}
        for provider, model in [
            ("opencode", "latest-model"), ("opencode-go", "another-new-model"),
            ("openai", "gpt-5.5"), ("anthropic", "claude-opus-5"),
        ]
    ]
    monkeypatch.setitem(sys.modules, "isolation.deployment", SimpleNamespace(remote_workers_enabled=lambda: False))
    monkeypatch.setitem(sys.modules, "agent.codex_pool", SimpleNamespace(
        get_codex_pool=lambda: SimpleNamespace(status=lambda: {
            "accounts": ["test"], "models": [{"id": "gpt-5.5"}, {"id": "gpt-6-luna"}],
        })))
    monkeypatch.delenv("CODEX_MODELS", raising=False)
    monkeypatch.setenv("OPENCODE_API_KEY", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("AGENT_DEFAULT_MODEL", "anthropic/claude-opus-5")
    scope = catalog_helpers()
    scope.update(os=os, logger=logging.getLogger(__name__), ClientPool=SimpleNamespace(default_model=lambda: "claude-opus-5"),
                 web=SimpleNamespace(Request=object, Response=object, json_response=lambda value: value),
                 get_agent_models=AsyncMock(return_value={"models": discovered}))
    exec(compile(ast.Module(body=[handler], type_ignores=[]), "api/routes.py", "exec"), scope)
    result = asyncio.run(scope["handle_agent_models"](None))
    assert {m["id"] for m in result["models"]} == {
        "anthropic/claude-opus-5-5", "anthropic/claude-fable-5-1",
        "codex/gpt-6-luna", "opencode/latest-model", "opencode-go/another-new-model",
    }
    assert result["default_model"] == "anthropic/claude-opus-5-5"
    # Discovery failure must not inject stale OpenCode or removed OpenAI aliases.
    scope["get_agent_models"] = AsyncMock(side_effect=RuntimeError("offline"))
    result = asyncio.run(scope["handle_agent_models"](None))
    assert {m["provider_id"] for m in result["models"]} == {"anthropic", "codex"}
