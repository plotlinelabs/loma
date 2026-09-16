#!/usr/bin/env bash
# Run explicitly on a developer/CI host with the pinned native CLI installed.
# All provider responses are synthetic local HTTP; no account or API key needed.
set -euo pipefail
if ! command -v codex >/dev/null 2>&1; then
  echo 'Install @openai/codex@0.153.3 before running native worker tests.' >&2
  exit 1
fi
if ! codex --version 2>/dev/null | grep -qx 'codex-cli 0.153.3'; then
  echo 'Native worker tests require codex-cli 0.153.3.' >&2
  exit 1
fi
for native in claude opencode; do
  if ! command -v "$native" >/dev/null 2>&1; then
    echo "Install the pinned $native CLI before running native worker tests." >&2
    exit 1
  fi
done
if ! claude --version 2>/dev/null | grep -qx '2.1.261 (Claude Code)'; then
  echo 'Native worker tests require Claude Code 2.1.261.' >&2
  exit 1
fi
if ! opencode --version 2>/dev/null | grep -qx '1.18.28'; then
  echo 'Native worker tests require OpenCode 1.18.28.' >&2
  exit 1
fi
exec "${PYTHON:-python3}" -m pytest tests/test_codex_worker.py tests/test_worker_models.py \
  tests/test_worker_usage.py tests/test_worker_accounts.py tests/test_worker_oauth.py tests/test_worker_run.py tests/test_worker_accounting.py tests/test_worker_account_reporting.py tests/test_worker_knowledge.py \
  tests/test_worker_downloads.py tests/test_worker_connectors.py tests/test_worker_proposals.py tests/test_connector_lifecycle.py tests/test_workspace_tools.py tests/test_worker_boundary.py tests/test_worker_artifacts.py tests/test_claude_worker.py tests/test_remote_entrypoint.py tests/test_remote_utility.py tests/test_remote_utility_remaining.py tests/test_worker_automation.py tests/test_worker_build.py \
  tests/test_opencode_worker.py tests/test_native_gateway_runtimes.py tests/test_mcp_bridge.py -q "$@"
