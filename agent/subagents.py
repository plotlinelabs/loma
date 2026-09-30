"""Named sub-agents offered to Claude runs through the Agent tool.

The run's own model is picked per conversation. These sub-agents can run on a
different (cheaper) model, so repetitive work like driving a device does not
bill every step at the main model's price.
"""

import os

from claude_agent_sdk import AgentDefinition

DEVICE_TESTER = "device-tester"
DEFAULT_DEVICE_TESTER_MODEL = "haiku"
DEFAULT_DEVICE_TESTER_MAX_TURNS = 80

DEVICE_TESTER_PROMPT = """You run mobile test scenarios on a leased Loma device and report the result. You do not plan tests, fix code or debug the app.

Input: the caller gives you the device CLI prefix (`python3 tools/device.py --user-email ... --auth-token ... --scope ...`), the device id, the app id, and the scenarios with the exact assertions to check. If any of these is missing, stop and report what is missing.

Rules:
- Use only `tools/device.py` through Bash, and Read to open screenshots it saves. Do not edit files or call other tools.
- Decide pass or fail only from tool output: `wait-for`, `tap-text`, `ui-tree` text/ids, or a Maestro `run-flow` result. Never mark a step as passed because it looks right or because an earlier step succeeded.
- Use the element tree to find things. Take a screenshot only when a step fails, plus one at the key moment of each scenario if the caller asked for evidence.
- Take coordinates and refs only from the latest `ui-tree`. Refs expire after any tap, key, typing, launch or install.
- If the device is offline, the lease is lost, the install fails, or the app crashes on launch, stop immediately and report it as a setup failure. Do not retry more than once.
- Do not release the device; the caller owns the lease.

Reply with only this report, nothing else:
SCENARIO: <name> | PASS or FAIL or BLOCKED
  failed_step: <step and the exact assertion that failed, or ->
  evidence: <the tool output line that decided it; screenshot path if taken>
(one block per scenario)
SETUP: <ok, or the exact error>"""


def device_tester_model() -> str | None:
    """Model for the device-tester sub-agent, or None to inherit the run's model.

    LOMA_DEVICE_TESTER_MODEL accepts an alias (haiku, sonnet, opus) or a full
    Claude model id. Set it to "inherit" to use the run's own model.
    """
    value = os.environ.get("LOMA_DEVICE_TESTER_MODEL", "").strip()
    if not value:
        return DEFAULT_DEVICE_TESTER_MODEL
    if value.lower() == "inherit":
        return None
    return value


def _max_turns() -> int:
    try:
        value = int(os.environ.get("LOMA_DEVICE_TESTER_MAX_TURNS", ""))
    except ValueError:
        return DEFAULT_DEVICE_TESTER_MAX_TURNS
    return value if value > 0 else DEFAULT_DEVICE_TESTER_MAX_TURNS


def build_subagents() -> dict[str, AgentDefinition]:
    """Sub-agents registered on every Claude run."""
    return {
        DEVICE_TESTER: AgentDefinition(
            description=(
                "Runs already-planned mobile test scenarios on a leased Loma device through "
                "tools/device.py and returns only PASS/FAIL/BLOCKED per scenario with evidence. "
                "Use it for the tap/check loop; keep planning and failure analysis yourself."
            ),
            prompt=DEVICE_TESTER_PROMPT,
            tools=["Bash", "Read"],
            model=device_tester_model(),
            maxTurns=_max_turns(),
        ),
    }
