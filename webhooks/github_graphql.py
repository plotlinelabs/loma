"""GitHub GraphQL API client for operations not available in REST API.

Provides functions for PR review thread resolution, fetching thread IDs,
and other GraphQL-only operations.
"""

import logging
import os
import ssl

import aiohttp
import certifi
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
GITHUB_API_KEY = os.environ.get("GITHUB_API_KEY", "")

# SSL context using certifi CA bundle (fixes macOS certificate issues)
_ssl_context = ssl.create_default_context(cafile=certifi.where())


async def _graphql_request(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL request against the GitHub API."""
    if not GITHUB_API_KEY:
        logger.error("[GITHUB-GRAPHQL] GITHUB_API_KEY not set")
        return {"errors": [{"message": "GITHUB_API_KEY not configured"}]}

    headers = {
        "Authorization": f"Bearer {GITHUB_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                GITHUB_GRAPHQL_URL, json=payload, headers=headers
            ) as resp:
                data = await resp.json()
                if "errors" in data:
                    logger.error("[GITHUB-GRAPHQL] GraphQL errors: %s", data["errors"])
                return data
    except Exception as e:
        logger.error("[GITHUB-GRAPHQL] Request failed: %s", e)
        return {"errors": [{"message": str(e)}]}


async def resolve_review_thread(thread_id: str) -> bool:
    """Resolve a PR review thread.

    Args:
        thread_id: The GraphQL node ID of the review thread.

    Returns:
        True if the thread was resolved successfully, False otherwise.
    """
    query = """
    mutation ResolveReviewThread($threadId: ID!) {
        resolveReviewThread(input: {threadId: $threadId}) {
            thread {
                isResolved
            }
        }
    }
    """
    variables = {"threadId": thread_id}

    result = await _graphql_request(query, variables)
    thread_data = (result.get("data") or {}).get("resolveReviewThread") or {}
    thread = thread_data.get("thread") or {}

    if thread.get("isResolved"):
        logger.info("[GITHUB-GRAPHQL] Resolved thread %s", thread_id)
        return True
    else:
        logger.error("[GITHUB-GRAPHQL] Failed to resolve thread %s: %s", thread_id, result)
        return False


async def unresolve_review_thread(thread_id: str) -> bool:
    """Unresolve a PR review thread.

    Args:
        thread_id: The GraphQL node ID of the review thread.

    Returns:
        True if the thread was unresolved successfully, False otherwise.
    """
    query = """
    mutation UnresolveReviewThread($threadId: ID!) {
        unresolveReviewThread(input: {threadId: $threadId}) {
            thread {
                isResolved
            }
        }
    }
    """
    variables = {"threadId": thread_id}

    result = await _graphql_request(query, variables)
    thread_data = (result.get("data") or {}).get("unresolveReviewThread") or {}
    thread = thread_data.get("thread") or {}

    if thread.get("isResolved") is False:
        logger.info("[GITHUB-GRAPHQL] Unresolved thread %s", thread_id)
        return True
    else:
        logger.error("[GITHUB-GRAPHQL] Failed to unresolve thread %s: %s", thread_id, result)
        return False


async def get_review_threads(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
) -> list[dict]:
    """Get all review threads for a PR with their GraphQL node IDs.

    Args:
        repo_owner: Repository owner (e.g., "example-org").
        repo_name: Repository name (e.g., "loma-services").
        pr_number: Pull request number.

    Returns:
        List of thread dicts with keys:
        - id: GraphQL node ID
        - isResolved: bool
        - path: file path
        - line: line number (may be None for outdated comments)
        - comments: list of comment bodies
    """
    query = """
    query GetPRReviewThreads($owner: String!, $repo: String!, $prNumber: Int!) {
        repository(owner: $owner, name: $repo) {
            pullRequest(number: $prNumber) {
                reviewThreads(first: 100) {
                    nodes {
                        id
                        isResolved
                        path
                        line
                        comments(first: 10) {
                            nodes {
                                id
                                body
                                author {
                                    login
                                }
                                createdAt
                            }
                        }
                    }
                }
            }
        }
    }
    """
    variables = {
        "owner": repo_owner,
        "repo": repo_name,
        "prNumber": pr_number,
    }

    result = await _graphql_request(query, variables)

    threads_data = (
        ((((result.get("data") or {})
        .get("repository") or {})
        .get("pullRequest") or {})
        .get("reviewThreads") or {})
        .get("nodes") or []
    )

    threads = []
    for t in threads_data:
        comments = [
            {
                "id": c.get("id"),
                "body": c.get("body", ""),
                "author": c.get("author", {}).get("login", ""),
                "created_at": c.get("createdAt"),
            }
            for c in t.get("comments", {}).get("nodes", [])
        ]
        threads.append({
            "id": t.get("id"),
            "is_resolved": t.get("isResolved", False),
            "path": t.get("path"),
            "line": t.get("line"),
            "comments": comments,
        })

    logger.info(
        "[GITHUB-GRAPHQL] Fetched %d review threads for %s/%s#%d",
        len(threads), repo_owner, repo_name, pr_number,
    )
    return threads


_DEFAULT_AGENT_LOGIN = os.environ.get("AGENT_GITHUB_LOGIN", "loma-insights")


async def get_agent_review_threads(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    agent_login: str | None = None,
) -> list[dict]:
    """Get review threads where the agent posted the first comment.

    Filters the full thread list to only include threads started by the agent.

    Args:
        repo_owner: Repository owner.
        repo_name: Repository name.
        pr_number: Pull request number.
        agent_login: GitHub login of the agent. Defaults to the
            AGENT_GITHUB_LOGIN env var, then "loma-insights".

    Returns:
        List of thread dicts (same format as get_review_threads).
    """
    effective_login = agent_login or _DEFAULT_AGENT_LOGIN
    all_threads = await get_review_threads(repo_owner, repo_name, pr_number)

    agent_threads = []
    for thread in all_threads:
        comments = thread.get("comments", [])
        if comments and comments[0].get("author") == effective_login:
            agent_threads.append(thread)

    logger.info(
        "[GITHUB-GRAPHQL] Found %d agent threads out of %d total for %s/%s#%d",
        len(agent_threads), len(all_threads), repo_owner, repo_name, pr_number,
    )
    return agent_threads


async def dismiss_review(
    pull_request_review_id: str,
    message: str = "Superseded by updated review",
) -> bool:
    """Dismiss a PR review.

    Args:
        pull_request_review_id: The GraphQL node ID of the review.
        message: Dismissal message.

    Returns:
        True if the review was dismissed successfully, False otherwise.
    """
    query = """
    mutation DismissReview($reviewId: ID!, $message: String!) {
        dismissPullRequestReview(input: {pullRequestReviewId: $reviewId, message: $message}) {
            pullRequestReview {
                state
            }
        }
    }
    """
    variables = {
        "reviewId": pull_request_review_id,
        "message": message,
    }

    result = await _graphql_request(query, variables)
    review_data = (result.get("data") or {}).get("dismissPullRequestReview") or {}
    review = review_data.get("pullRequestReview") or {}

    if review.get("state") == "DISMISSED":
        logger.info("[GITHUB-GRAPHQL] Dismissed review %s", pull_request_review_id)
        return True
    else:
        logger.error("[GITHUB-GRAPHQL] Failed to dismiss review %s: %s", pull_request_review_id, result)
        return False

async def minimize_comment(comment_node_id: str, reason: str = "OUTDATED") -> bool:
    """Minimize (hide) a comment on a PR or issue.

    Uses the GitHub GraphQL minimizeComment mutation to collapse a comment
    with a "hidden as <reason>" label.

    Args:
        comment_node_id: The GraphQL node ID of the comment to minimize.
        reason: Reason for minimizing. One of: ABUSE, OFF_TOPIC, OUTDATED, RESOLVED, DUPLICATE, SPAM.

    Returns:
        True if the comment was minimized successfully, False otherwise.
    """
    query = """
    mutation MinimizeComment($subjectId: ID!, $classifier: ReportedContentClassifiers!) {
        minimizeComment(input: {subjectId: $subjectId, classifier: $classifier}) {
            minimizedComment {
                isMinimized
                minimizedReason
            }
        }
    }
    """
    variables = {
        "subjectId": comment_node_id,
        "classifier": reason,
    }

    result = await _graphql_request(query, variables)
    comment_data = (result.get("data") or {}).get("minimizeComment") or {}
    minimized = comment_data.get("minimizedComment") or {}

    if minimized.get("isMinimized"):
        logger.info("[GITHUB-GRAPHQL] Minimized comment %s (reason=%s)", comment_node_id, reason)
        return True
    else:
        logger.error("[GITHUB-GRAPHQL] Failed to minimize comment %s: %s", comment_node_id, result)
        return False


async def get_pr_comments(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
) -> list[dict]:
    """Get all issue/PR conversation comments (not review comments) via GraphQL.

    Returns comments with their GraphQL node IDs, which are needed for
    the minimizeComment mutation.

    Args:
        repo_owner: Repository owner.
        repo_name: Repository name.
        pr_number: Pull request number.

    Returns:
        List of comment dicts with keys:
        - id: GraphQL node ID (for use with minimizeComment)
        - database_id: REST API comment ID
        - body: comment text
        - author: GitHub login
        - created_at: ISO timestamp
        - is_minimized: bool
    """
    query = """
    query GetPRComments($owner: String!, $repo: String!, $prNumber: Int!) {
        repository(owner: $owner, name: $repo) {
            pullRequest(number: $prNumber) {
                comments(first: 100) {
                    nodes {
                        id
                        databaseId
                        body
                        isMinimized
                        author {
                            login
                        }
                        createdAt
                    }
                }
            }
        }
    }
    """
    variables = {
        "owner": repo_owner,
        "repo": repo_name,
        "prNumber": pr_number,
    }

    result = await _graphql_request(query, variables)

    comments_data = (
        ((((result.get("data") or {})
        .get("repository") or {})
        .get("pullRequest") or {})
        .get("comments") or {})
        .get("nodes") or []
    )

    comments = []
    for c in comments_data:
        comments.append({
            "id": c.get("id"),
            "database_id": c.get("databaseId"),
            "body": c.get("body", ""),
            "author": (c.get("author") or {}).get("login", ""),
            "created_at": c.get("createdAt"),
            "is_minimized": c.get("isMinimized", False),
        })

    logger.info(
        "[GITHUB-GRAPHQL] Fetched %d PR comments for %s/%s#%d",
        len(comments), repo_owner, repo_name, pr_number,
    )
    return comments


async def get_pr_reviews(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
) -> list[dict]:
    """Get all reviews for a PR with their GraphQL node IDs.

    Returns reviews with node IDs needed for minimizing review body comments.

    Args:
        repo_owner: Repository owner.
        repo_name: Repository name.
        pr_number: Pull request number.

    Returns:
        List of review dicts with keys:
        - id: GraphQL node ID (for use with minimizeComment — reviews are comments)
        - state: review state (APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED)
        - body: review summary text
        - author: GitHub login
        - created_at: ISO timestamp
    """
    query = """
    query GetPRReviews($owner: String!, $repo: String!, $prNumber: Int!) {
        repository(owner: $owner, name: $repo) {
            pullRequest(number: $prNumber) {
                reviews(first: 50) {
                    nodes {
                        id
                        state
                        body
                        author {
                            login
                        }
                        createdAt
                    }
                }
            }
        }
    }
    """
    variables = {
        "owner": repo_owner,
        "repo": repo_name,
        "prNumber": pr_number,
    }

    result = await _graphql_request(query, variables)

    reviews_data = (
        ((((result.get("data") or {})
        .get("repository") or {})
        .get("pullRequest") or {})
        .get("reviews") or {})
        .get("nodes") or []
    )

    reviews = []
    for r in reviews_data:
        reviews.append({
            "id": r.get("id"),
            "state": r.get("state", ""),
            "body": r.get("body", ""),
            "author": (r.get("author") or {}).get("login", ""),
            "created_at": r.get("createdAt"),
        })

    logger.info(
        "[GITHUB-GRAPHQL] Fetched %d reviews for %s/%s#%d",
        len(reviews), repo_owner, repo_name, pr_number,
    )
    return reviews
