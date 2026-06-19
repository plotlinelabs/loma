import os
import logging

from motor.motor_asyncio import AsyncIOMotorClient
from config.app_config import OBSERVABILITY_DB_NAME

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None
_db = None


async def init_observability():
    """Initialize the observability MongoDB connection. Call once at startup."""
    global _client, _db
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri or not uri.startswith("mongodb"):
        logger.warning("OBSERVABILITY_MONGODB_URI not set or invalid — observability disabled")
        return

    _client = AsyncIOMotorClient(uri)
    _db = _client[OBSERVABILITY_DB_NAME]

    # Create indexes
    await _db.conversations.create_index([("started_at", -1)])
    await _db.conversations.create_index("source")
    await _db.conversations.create_index("status")
    await _db.conversations.create_index("confidence.category")
    await _db.conversations.create_index("learnings.has_learning")
    await _db.turns.create_index([("conversation_id", 1), ("turn_number", 1)])
    await _db.turns.create_index("conversation_id")
    await _db.conversations.create_index("cost.total_cost_usd")

    # Flow indexes
    await _db.flows.create_index("flow_id", unique=True)
    await _db.flows.create_index("status")
    await _db.flows.create_index("channel_id")
    await _db.flows.create_index([("created_at", -1)])
    await _db.flows.create_index([("next_run_at", 1)])

    # Chat isolation — index for filtering conversations by user
    await _db.conversations.create_index("metadata.user_name")

    # Governance: users collection
    await _db.users.create_index("email", unique=True)

    # Governance: teams collection
    await _db.teams.create_index("team_id", unique=True)
    await _db.teams.create_index("members")

    # Governance: tool_configs collection
    await _db.tool_configs.create_index("tool_key", unique=True)

    # OAuth tokens (per-user encrypted tokens for personal integrations)
    # Compound index: one token doc per user per provider (google, slack, etc.)
    await _db.oauth_tokens.create_index(
        [("user_email", 1), ("provider", 1)], unique=True,
    )

    # Env audit log (tracks .env file changes)
    await _db.env_audit_log.create_index([("timestamp", -1)])
    await _db.env_audit_log.create_index("user_email")

    # Draft with Loma indexes
    await _db.drafts.create_index("draft_id", unique=True)
    await _db.drafts.create_index([("user_email", 1), ("status", 1)])

    # PR review comments (for slash command tracking)
    await _db.pr_review_comments.create_index("comment_id", unique=True)
    await _db.pr_review_comments.create_index([("pr_number", 1), ("repo_full_name", 1)])
    await _db.pr_review_comments.create_index("status")

    # PR review quality (for self-improvement loop)
    await _db.pr_review_quality.create_index("quality_id", unique=True)
    await _db.pr_review_quality.create_index("conversation_id")
    await _db.pr_review_quality.create_index([("pr_number", 1), ("repo_full_name", 1)])

    # PR Slack threads (for GitHub-Slack sync)
    await _db.pr_slack_threads.create_index("thread_id", unique=True)
    await _db.pr_slack_threads.create_index(
        [("pr_number", 1), ("repo_full_name", 1)], unique=True,
    )

    # Preexisting issues (for maintenance flow)
    await _db.preexisting_issues.create_index("issue_id", unique=True)
    await _db.preexisting_issues.create_index([("repo_full_name", 1), ("file_path", 1)])
    await _db.preexisting_issues.create_index("status")

    # PR review threads (for thread resolution tracking)
    await _db.pr_review_threads.create_index("thread_id", unique=True)
    await _db.pr_review_threads.create_index("conversation_id")
    await _db.pr_review_threads.create_index([("pr_number", 1), ("repo_full_name", 1)])

    # Chat management: soft-delete and project organization
    await _db.conversations.create_index("deleted")
    await _db.conversations.create_index("project_id")

    # Projects collection (chat organization)
    await _db.projects.create_index("project_id", unique=True)
    await _db.projects.create_index("created_by")
    await _db.projects.create_index([("created_at", -1)])

    # Org integrations (dynamic MCP config)
    await _db.integrations.create_index("provider", unique=True)
    await _db.integrations.create_index("status")

    # Core prompt settings edited from the dashboard
    await _db.prompt_settings.create_index("setting_key", unique=True)

    # DB-native skills
    await _db.skills.create_index("slug", unique=True)
    await _db.skills.create_index("enabled")
    await _db.skills.create_index([("updated_at", -1)])
    await _db.skill_files.create_index([("skill_slug", 1), ("path", 1)], unique=True)
    await _db.skill_files.create_index("content_hash")
    await _db.skill_versions.create_index([("skill_slug", 1), ("created_at", -1)])
    await _db.skill_versions.create_index("version_id", unique=True)

    # Event ingestion (Pass 1) indexes
    await _db.events.create_index("event_id", unique=True)
    await _db.events.create_index("source_event_id", unique=True)
    await _db.events.create_index([("timestamp", -1)])
    await _db.events.create_index("channel_id")
    await _db.events.create_index("user_id")
    await _db.events.create_index("event_type")
    await _db.events.create_index([("channel_id", 1), ("timestamp", -1)])
    await _db.events.create_index([("source", 1), ("timestamp", -1)])
    await _db.events.create_index("processed")
    await _db.events.create_index("thread_ts")

    # Change streams (new home for ingested events — replaces `events`)
    await _db.changestreams.create_index("event_id", unique=True)
    await _db.changestreams.create_index("source_event_id", unique=True)
    await _db.changestreams.create_index([("timestamp", -1)])
    await _db.changestreams.create_index("channel_id")
    await _db.changestreams.create_index("user_id")
    await _db.changestreams.create_index("event_type")
    await _db.changestreams.create_index([("channel_id", 1), ("timestamp", -1)])
    await _db.changestreams.create_index([("source", 1), ("timestamp", -1)])
    await _db.changestreams.create_index("processed")
    await _db.changestreams.create_index("thread_ts")
    await _db.changestreams.create_index("thread_refs.slack_thread_ts")
    await _db.changestreams.create_index("thread_refs.conversation_id")
    await _db.changestreams.create_index("thread_refs.pylon_issue_id")
    await _db.changestreams.create_index("thread_refs.linear_issue")
    await _db.changestreams.create_index("thread_refs.hubspot_deal_id")

    # Learnings (deduplicated, with embeddings)
    # NOTE: Atlas vector search index "learning_embedding_index" must be created
    # via Atlas UI/CLI on the "learnings" collection:
    #   field: embedding, type: vector, dims: 1024, similarity: cosine
    await _db.learnings.create_index("learning_id", unique=True)
    await _db.learnings.create_index("improvement_target")
    await _db.learnings.create_index("status")
    await _db.learnings.create_index([("created_at", -1)])

    # Tool learnings (deduplicated, with embeddings)
    # NOTE: Atlas vector search index "tool_learning_embedding_index" must be created
    # via Atlas UI/CLI on the "tool_learnings" collection:
    #   field: embedding, type: vector, dims: 1024, similarity: cosine
    await _db.tool_learnings.create_index("tool_learning_id", unique=True)
    await _db.tool_learnings.create_index("tool_name")
    await _db.tool_learnings.create_index("status")
    await _db.tool_learnings.create_index([("created_at", -1)])

    # Work thread reviews
    await _db.work_thread_reviews.create_index("conversation_id", unique=True)
    await _db.work_thread_reviews.create_index("outcome")
    await _db.work_thread_reviews.create_index([("reviewed_at", -1)])
    await _db.work_thread_reviews.create_index("improvement_target")

    # Engineering-health metrics (ISSUE-3422)
    await _db.eng_release_events.create_index("identifier", unique=True)
    await _db.eng_release_events.create_index([("released_at", 1)])
    await _db.eng_bucket_snapshots.create_index("month", unique=True)

    # Pylon ticket mirror (ISSUE-3424 bugs, ISSUE-3427 MTTR)
    await _db.pylon_tickets.create_index("identifier", unique=True)
    await _db.pylon_tickets.create_index([("created_at", -1)])
    await _db.pylon_tickets.create_index([("closed_at", -1)])
    await _db.pylon_tickets.create_index("state")
    await _db.pylon_tickets.create_index("bucket")
    await _db.pylon_tickets.create_index("is_open")
    await _db.pylon_tickets.create_index("is_bug")
    await _db.pylon_tickets.create_index("modules")
    await _db.pylon_tickets.create_index("priority")
    await _db.pylon_tickets.create_index("type")
    await _db.pylon_classifications.create_index("ticket_id")
    await _db.pylon_classifications.create_index([("ran_at", -1)])
    await _db.pylon_classifications.create_index("stage")

    # Resolution attribution (Gogo / Gogo+Support / Support) — set at on-close.
    # Indexed for the grouped reads from /metrics/pylon/resolution.
    await _db.pylon_tickets.create_index("resolution_attribution")
    await _db.pylon_tickets.create_index("team.id")

    # Sentry daily metrics (ISSUE-3426)
    await _db.eng_sentry_daily.create_index(
        [("project_id", 1), ("environment", 1), ("day", 1)], unique=True,
    )
    await _db.eng_sentry_daily.create_index([("day", -1)])
    await _db.eng_sentry_daily.create_index("project_slug")
    await _db.eng_sentry_slow_endpoints.create_index(
        [("project_id", 1), ("environment", 1)], unique=True,
    )

    logger.info("Observability MongoDB connected and indexed")


def get_db():
    """Get the observability database. Returns None if not initialized."""
    return _db
