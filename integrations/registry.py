"""
Provider catalog — defines what Loma knows how to integrate with.

Each entry describes how to configure the MCP server, verify webhooks,
and ingest events for a given provider. Lives in code (not DB) because
it includes MCP config templates and handler references.

To add a new provider, add an entry here and implement its ingestion module.
"""

PROVIDER_CATALOG = {
    "gitbook": {
        "display_name": "GitBook",
        "description": "Search and read product documentation",
        "auth_type": "api_key",
        "auth_label": "Visitor Token Cookie",
        "auth_help_url": "https://developer.gitbook.com/",
        "extra_fields": [
            {"key": "url", "label": "GitBook MCP URL", "placeholder": "https://docs.example.com/~gitbook/mcp", "required": True},
        ],
        "mcp_config_template": {
            "type": "http",
            "url_template": "{{url}}",
            "headers_template": {"Cookie": "{{API_KEY}}"},
        },
        "mcp_server_name": "docs",
        "webhook": None,
        "ingestion_module": None,
    },
    "clickhouse": {
        "display_name": "ClickHouse",
        "description": "Query analytics data, events, and campaign metrics",
        "auth_type": "api_key",
        "auth_label": "Password",
        "auth_help_url": "https://clickhouse.com/docs/en/getting-started",
        "extra_fields": [
            {"key": "host", "label": "Host", "placeholder": "your-instance.clickhouse.cloud", "required": True},
            {"key": "user", "label": "Username", "placeholder": "default", "required": True},
            {"key": "database", "label": "Database", "placeholder": "default", "required": True},
        ],
        "mcp_config_template": {
            "type": "stdio",
            "command": "uv",
            "args": ["run", "--with", "mcp-clickhouse", "--python", "3.10", "mcp-clickhouse"],
            "env_template": {
                "CLICKHOUSE_HOST": "{{host}}",
                "CLICKHOUSE_USER": "{{user}}",
                "CLICKHOUSE_PASSWORD": "{{API_KEY}}",
                "CLICKHOUSE_DATABASE": "{{database}}",
                "CLICKHOUSE_PORT": "8123",
                "CLICKHOUSE_SECURE": "false",
                "CLICKHOUSE_VERIFY": "false",
            },
        },
        "mcp_server_name": "clickhouse",
        "webhook": None,
        "ingestion_module": None,
    },
    "mongodb": {
        "display_name": "MongoDB",
        "description": "Query production database for debugging customer data",
        "auth_type": "api_key",
        "auth_label": "Connection String",
        "auth_help_url": "https://www.mongodb.com/docs/manual/reference/connection-string/",
        "mcp_config_template": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "mongodb-mcp-server"],
            "env_template": {"MDB_MCP_CONNECTION_STRING": "{{API_KEY}}"},
        },
        "mcp_server_name": "mongodb",
        "webhook": None,
        "ingestion_module": None,
    },
    "notion": {
        "display_name": "Notion",
        "description": "Workspace pages, databases, and internal runbooks",
        "auth_type": "api_key",
        "auth_label": "Integration Token",
        "auth_help_url": "https://www.notion.so/my-integrations",
        "mcp_config_template": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@notionhq/notion-mcp-server"],
            "env_template": {"NOTION_TOKEN": "{{API_KEY}}"},
        },
        "mcp_server_name": "notion",
        "webhook": None,
        "ingestion_module": None,
    },
    "bigquery": {
        "display_name": "Google BigQuery",
        "description": "Query API request/response logs from production (primary source post-2026-04-30 cutover)",
        "auth_type": "api_key",
        "auth_label": "Service Account JSON",
        "auth_help_url": "https://cloud.google.com/iam/docs/keys-create-delete",
        "extra_fields": [
            {"key": "project_id", "label": "GCP Project ID", "placeholder": "loma-b4b22", "required": True},
            {"key": "location", "label": "BigQuery Location", "placeholder": "asia-south1", "required": False},
        ],
        "mcp_config_template": {
            "type": "stdio",
            "command": "mcp-servers/bigquery/launcher.sh",
            "args": [],
            "env_template": {
                "GOOGLE_APPLICATION_CREDENTIALS_JSON": "{{API_KEY}}",
                "BIGQUERY_PROJECT": "{{project_id}}",
                "BIGQUERY_LOCATION": "{{location}}",
            },
        },
        "mcp_server_name": "bigquery",
        "webhook": None,
        "ingestion_module": None,
    },
    "athena": {
        "display_name": "AWS Athena",
        "description": "Historical API request/response logs (pre-2026-04-30 cutover only — use BigQuery for current data)",
        "auth_type": "api_key",
        "auth_label": "Secret Access Key",
        "auth_help_url": "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html",
        "extra_fields": [
            {"key": "access_key_id", "label": "Access Key ID", "placeholder": "AKIA...", "required": True},
            {"key": "output_s3_path", "label": "S3 Output Path", "placeholder": "s3://your-bucket/athena-results/", "required": True},
            {"key": "region", "label": "AWS Region", "placeholder": "us-east-1", "required": True},
        ],
        "mcp_config_template": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@lishenxydlgzs/aws-athena-mcp"],
            "env_template": {
                "AWS_ACCESS_KEY_ID": "{{access_key_id}}",
                "AWS_SECRET_ACCESS_KEY": "{{API_KEY}}",
                "OUTPUT_S3_PATH": "{{output_s3_path}}",
                "AWS_REGION": "{{region}}",
            },
        },
        "mcp_server_name": "athena",
        "webhook": None,
        "ingestion_module": None,
    },
    "github": {
        "display_name": "GitHub",
        "description": "Search and read code from your GitHub repositories",
        "auth_type": "api_key",
        "auth_label": "Personal Access Token",
        "auth_help_url": "https://github.com/settings/tokens",
        "mcp_config_template": {
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp",
            "headers_template": {"Authorization": "Bearer {{API_KEY}}"},
        },
        "mcp_server_name": "github",
        "webhook": {
            "signature_header": "x-hub-signature-256",
            "auth_method": "hmac_sha256",
            "secret_label": "Webhook Secret",
        },
        "ingestion_module": "webhooks.github_ingestion",
    },
    "hubspot": {
        "display_name": "HubSpot",
        "description": "CRM contacts, companies, deals, tickets, and engagement data",
        "auth_type": "api_key",
        "auth_label": "Private App Access Token",
        "auth_help_url": "https://developers.hubspot.com/docs/api/private-apps",
        "mcp_config_template": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@hubspot/mcp-server"],
            "env_template": {"PRIVATE_APP_ACCESS_TOKEN": "{{API_KEY}}"},
        },
        "mcp_server_name": "hubspot",
        "webhook": None,
        "ingestion_module": None,
    },
    "linear": {
        "display_name": "Linear",
        "description": "Issue tracking and project management",
        "auth_type": "api_key",
        "auth_label": "API Key",
        "auth_help_url": "https://linear.app/settings/api",
        "mcp_config_template": {
            "type": "http",
            "url": "https://mcp.linear.app/mcp",
            "headers_template": {"Authorization": "Bearer {{API_KEY}}"},
        },
        "mcp_server_name": "linear",
        "webhook": {
            "signature_header": "linear-signature",
            "auth_method": "hmac_sha256",
            "secret_label": "Webhook Signing Secret",
        },
        "ingestion_module": "webhooks.linear_ingestion",
    },
    # Sentry: loaded via config.yaml, not via DB integrations (auth_type="none").
    # The mcp_config_template below is for documentation only — actual loading
    # happens through config.yaml which references env vars directly.
    "sentry": {
        "display_name": "Sentry",
        "description": "Performance monitoring — p50/p75/p95/p99 page load times, transaction durations, throughput, and Web Vitals (LCP, FCP, CLS)",
        "auth_type": "none",
        "auth_label": None,
        "auth_help_url": None,
        "mcp_config_template": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@sentry/mcp-server"],
            "env_from_machine": True,
            "note": "Env vars (SENTRY_ACCESS_TOKEN, ANTHROPIC_API_KEY, EMBEDDED_AGENT_PROVIDER) are set directly on the machine, not via the integrations page.",
        },
        "mcp_server_name": "sentry",
        "webhook": None,
        "ingestion_module": None,
    },
}


def get_provider(provider: str) -> dict | None:
    """Look up a provider in the catalog. Returns None if not found."""
    return PROVIDER_CATALOG.get(provider)


def list_providers() -> list[dict]:
    """Return all providers with their keys."""
    return [
        {"provider": key, **value}
        for key, value in PROVIDER_CATALOG.items()
    ]
