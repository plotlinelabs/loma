"""Linear API helpers for direct GraphQL operations.

Provides functions for operations not covered by the Linear MCP tools,
such as posting comments and adding emoji reactions to comments.
"""

import logging
import os
import ssl

import aiohttp
import certifi
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"
LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY", "")

# SSL context using certifi CA bundle (fixes macOS certificate issues)
_ssl_context = ssl.create_default_context(cafile=certifi.where())

# Marker to identify comments posted by this agent (prevents webhook loop)
AGENT_COMMENT_MARKER = "<!-- loma -->"

CONVERSATION_TRACKER_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:3001").rstrip("/") + "/conversations"


async def _graphql_request(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL request against the Linear API."""
    if not LINEAR_API_KEY:
        logger.error("[LINEAR-API] LINEAR_API_KEY not set")
        return {"errors": [{"message": "LINEAR_API_KEY not configured"}]}

    headers = {
        "Authorization": LINEAR_API_KEY,
        "Content-Type": "application/json",
    }
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                LINEAR_API_URL, json=payload, headers=headers
            ) as resp:
                data = await resp.json()
                if "errors" in data:
                    logger.error("[LINEAR-API] GraphQL errors: %s", data["errors"])
                return data
    except Exception as e:
        logger.error("[LINEAR-API] Request failed: %s", e)
        return {"errors": [{"message": str(e)}]}


async def post_comment(issue_id: str, body: str) -> str | None:
    """Post a comment on a Linear issue.

    Args:
        issue_id: The Linear issue UUID.
        body: Markdown body of the comment.

    Returns:
        The created comment ID, or None on failure.
    """
    query = """
    mutation CommentCreate($input: CommentCreateInput!) {
        commentCreate(input: $input) {
            success
            comment {
                id
            }
        }
    }
    """
    variables = {"input": {"issueId": issue_id, "body": body}}

    result = await _graphql_request(query, variables)
    comment_data = result.get("data", {}).get("commentCreate", {})
    if comment_data.get("success"):
        comment_id = comment_data.get("comment", {}).get("id", "")
        logger.info("[LINEAR-API] Comment posted on issue %s (comment_id=%s)", issue_id, comment_id)
        return comment_id
    else:
        logger.error("[LINEAR-API] Failed to post comment on issue %s: %s", issue_id, result)
        return None


async def react_to_comment(comment_id: str, emoji: str = "\U0001f440") -> bool:
    """Add an emoji reaction to a Linear comment.

    Args:
        comment_id: The Linear comment UUID.
        emoji: The emoji to react with (default: \U0001f440 eyes).

    Returns:
        True if the reaction was created successfully, False otherwise.
    """
    query = """
    mutation CreateReaction($input: ReactionCreateInput!) {
        reactionCreate(input: $input) {
            success
            reaction {
                id
                emoji
            }
        }
    }
    """
    variables = {"input": {"commentId": comment_id, "emoji": emoji}}

    result = await _graphql_request(query, variables)
    reaction_data = result.get("data", {}).get("reactionCreate", {})
    if reaction_data.get("success"):
        logger.info(
            "[LINEAR-API] Reacted with %s to comment %s", emoji, comment_id
        )
        return True
    else:
        logger.error(
            "[LINEAR-API] Failed to react to comment %s: %s", comment_id, result
        )
        return False


async def post_acknowledgment_comment(
    issue_id: str, conversation_id: str | None = None,
) -> str | None:
    """Post a standard acknowledgment comment when the agent picks up an issue.

    Args:
        issue_id: The Linear issue UUID.
        conversation_id: Optional conversation ID for tracking link.

    Returns:
        The created comment ID, or None on failure.
    """
    body = (
        f"{AGENT_COMMENT_MARKER}\n"
        "\U0001f44b I've picked this up and am looking into it. "
        "I'll update this ticket shortly."
    )
    if conversation_id:
        tracking_url = f"{CONVERSATION_TRACKER_BASE_URL}/{conversation_id}"
        body += f"\n\nFollow progress \u2192 [{tracking_url}]({tracking_url})"
    return await post_comment(issue_id, body)


async def post_comment_tracking_acknowledgment(
    issue_id: str, conversation_id: str,
) -> str | None:
    """Post a tracking acknowledgment comment for comment-triggered processing.

    Used when a comment containing the trigger phrase is detected on an issue.

    Args:
        issue_id: The Linear issue UUID.
        conversation_id: The conversation ID for the tracking link.

    Returns:
        The created comment ID, or None on failure.
    """
    tracking_url = f"{CONVERSATION_TRACKER_BASE_URL}/{conversation_id}"
    body = (
        f"{AGENT_COMMENT_MARKER}\n"
        f"\U0001f440 On it! Follow progress \u2192 [{tracking_url}]({tracking_url})"
    )
    return await post_comment(issue_id, body)
