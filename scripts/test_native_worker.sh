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
exec "${PYTHON:-python3}" -m pytest tests/test_codex_worker.py tests/test_worker_models.py \
  tests/test_worker_boundary.py tests/test_worker_artifacts.py -q
