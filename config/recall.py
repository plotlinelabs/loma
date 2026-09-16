"""Recall endpoint availability; authentication is always enforced separately."""
import os


def recall_enabled() -> bool:
    # Missing means enabled. Explicit empty/invalid values fail closed.
    return os.environ.get("LOMA_RECALL_ENABLED", "true").strip().lower() == "true"
