"""Human cost estimation for savings analytics.

Estimates how long a human employee would take to perform the same task
the agent completed, and calculates the equivalent cost using BLS median
US hourly wages by expertise category.
"""

import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)

# Flat $5/hour rate for all categories (Loma pricing comparison)
WAGE_TABLE: dict[str, float] = {
    "Software Engineer": 5.00,
    "Data Analyst": 5.00,
    "DevOps Engineer": 5.00,
    "Support Engineer": 5.00,
    "Technical Writer": 5.00,
    "QA Engineer": 5.00,
    "Product Manager": 5.00,
    "Designer": 5.00,
    "Security Engineer": 5.00,
    "Database Administrator": 5.00,
    "General": 5.00,  # fallback
}

# Default category when classification fails
DEFAULT_CATEGORY = "General"


def _classify_expertise(prompt: str, final_response: str) -> str:
    """Classify the expertise category based on prompt and response content.

    Uses keyword heuristics — intentionally simple and fast.
    """
    text = f"{prompt} {final_response}".lower()

    # Order matters — more specific categories first
    keyword_map: list[tuple[str, list[str]]] = [
        ("Security Engineer", ["infosec", "security questionnaire", "tpra", "soc 2", "compliance", "vulnerability", "penetration"]),
        ("DevOps Engineer", ["deploy", "ci/cd", "pipeline", "docker", "kubernetes", "infrastructure", "terraform"]),
        ("Database Administrator", ["mongodb", "clickhouse", "database", "query", "aggregation", "collection", "schema"]),
        ("Data Analyst", ["analytics", "metrics", "dashboard", "chart", "report", "data", "funnel", "cohort"]),
        ("QA Engineer", ["test", "bug", "regression", "qa", "reproduce", "failing"]),
        ("Technical Writer", ["documentation", "docs", "readme", "guide", "tutorial", "gitbook", "notion page"]),
        ("Designer", ["design", "figma", "ui", "ux", "mockup", "wireframe", "layout"]),
        ("Product Manager", ["feature request", "roadmap", "priorit", "requirement", "user story", "spec"]),
        ("Support Engineer", ["support", "ticket", "pylon", "customer", "issue", "not working", "not showing", "debug", "troubleshoot"]),
        ("Software Engineer", ["code", "implement", "refactor", "api", "endpoint", "function", "class", "sdk", "integration", "pr", "pull request", "commit", "branch"]),
    ]

    for category, keywords in keyword_map:
        if any(kw in text for kw in keywords):
            return category

    return DEFAULT_CATEGORY


def _estimate_duration_minutes(prompt: str, total_turns: int, duration_ms: int | None) -> float:
    """Estimate how many minutes a human would take for this task.

    Heuristic based on:
    - Agent turn count (proxy for task complexity)
    - Agent wall-clock duration (humans are slower)
    - Prompt length (longer prompts = more complex asks)

    A human typically takes 5-20x longer than an AI agent for the same task,
    depending on complexity. We use a conservative multiplier.
    """
    # Base: scale from agent turns — each turn ≈ 3-5 min of human work
    base_minutes = total_turns * 4.0

    # Prompt complexity bonus: long prompts suggest more complex tasks
    prompt_len = len(prompt)
    if prompt_len > 2000:
        base_minutes *= 1.5
    elif prompt_len > 500:
        base_minutes *= 1.2

    # Floor: even the simplest task takes a human at least 5 minutes
    # (context switching, reading, understanding, responding)
    base_minutes = max(base_minutes, 5.0)

    # Cap at 480 minutes (8 hours) — anything beyond is unrealistic for
    # a single task comparison
    base_minutes = min(base_minutes, 480.0)

    return round(base_minutes, 1)


def estimate_human_cost(
    prompt: str,
    final_response: str,
    total_turns: int,
    duration_ms: int | None,
    api_cost_usd: float,
) -> dict:
    """Estimate the human equivalent cost for a completed conversation.

    Returns a dict with all savings fields ready to persist to MongoDB.
    """
    expertise = _classify_expertise(prompt, final_response)
    hourly_wage = WAGE_TABLE.get(expertise, WAGE_TABLE[DEFAULT_CATEGORY])
    duration_minutes = _estimate_duration_minutes(prompt, total_turns, duration_ms)
    human_cost = round((duration_minutes / 60.0) * hourly_wage, 4)
    savings = round(human_cost - api_cost_usd, 4)

    return {
        "estimated_human_duration_minutes": duration_minutes,
        "expertise_category": expertise,
        "median_hourly_wage_usd": hourly_wage,
        "estimated_human_cost_usd": human_cost,
        "savings_usd": savings,
    }
