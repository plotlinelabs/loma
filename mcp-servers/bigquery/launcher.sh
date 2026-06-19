#!/usr/bin/env bash
# BigQuery MCP launcher.
#
# Reads a Google Cloud service account JSON key from the
# GOOGLE_APPLICATION_CREDENTIALS_JSON env var (set by agent/client.py from the
# encrypted MongoDB integration record), writes it to a per-process temp file,
# and execs @ergut/mcp-bigquery-server pointing at that file.
#
# This wrapper exists because the underlying MCP server only accepts a file
# path via --key-file, while the agent's integration framework injects secrets
# as env vars (no on-disk artifacts). The temp file is removed on process exit.
set -euo pipefail

if [[ -z "${GOOGLE_APPLICATION_CREDENTIALS_JSON:-}" ]]; then
  echo "ERROR: GOOGLE_APPLICATION_CREDENTIALS_JSON env var is empty." >&2
  echo "Set the BigQuery integration's API_KEY field to a service account JSON." >&2
  exit 1
fi

if [[ -z "${BIGQUERY_PROJECT:-}" ]]; then
  echo "ERROR: BIGQUERY_PROJECT env var is empty (set the project_id field)." >&2
  exit 1
fi

LOCATION="${BIGQUERY_LOCATION:-asia-south1}"

# Write SA JSON to a per-process temp file, restricted permissions, removed on exit.
TMP_KEY="$(mktemp -t bq-mcp-sa.XXXXXX.json)"
chmod 600 "$TMP_KEY"
trap 'rm -f "$TMP_KEY"' EXIT INT TERM

printf '%s' "$GOOGLE_APPLICATION_CREDENTIALS_JSON" > "$TMP_KEY"

# Sanity-check the file parses as JSON before launching the server, so the
# subprocess fails fast with a clear message rather than a cryptic SDK error.
if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$TMP_KEY" 2>/dev/null; then
  echo "ERROR: GOOGLE_APPLICATION_CREDENTIALS_JSON is not valid JSON." >&2
  exit 1
fi

# Version pinned: bumping requires re-running the validation in mcp-servers/bigquery/README
# (or the procedure documented in the original PR) before merging.
exec npx -y @ergut/mcp-bigquery-server@1.0.4 \
  --project-id "$BIGQUERY_PROJECT" \
  --location "$LOCATION" \
  --key-file "$TMP_KEY"
