"""
Example custom tool template.

To add a new in-process tool to the bot:

1. Create a new file in this directory (e.g., tools/my_tool.py)
2. Define your tool function using the @tool decorator
3. Register it in tools/__init__.py
4. The agent will automatically have access to it

See: https://github.com/anthropics/claude-agent-sdk-python for full docs
"""

from claude_agent_sdk import tool


@tool(
    name="example_echo",
    description="Echo back the input text. This is a template — replace with your own tool.",
    input_schema={"text": str},
)
async def echo_tool(args: dict) -> dict:
    """A simple example tool that echoes input."""
    text = args.get("text", "")
    return {
        "content": [
            {"type": "text", "text": f"Echo: {text}"}
        ]
    }


# Export tools to be registered
TOOLS = [echo_tool]
