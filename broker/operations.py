"""Broker operations for isolated workers.

Workers hold no credentials and no database access. Every tool invocation is
forwarded to the broker, which authenticates the run capability, enforces the
grant, and executes the real tool **server-side** with the run owner's
identity. Credentials (OAuth tokens, integration API keys, the encryption
key, the database URI) never enter a worker process.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path

from broker.service import Denied

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Tools that authenticate the acting user via --user-email/--auth-token.
# The broker always injects server-minted values for these; worker-supplied
# identity flags are stripped unconditionally for every tool.
AUTH_TOOLS = frozenset({
    "gmail", "google_drive", "google_calendar", "google_sheets",
    "google_slides", "google_docs_personal", "google_apps_script",
    "slack_user", "telegram", "notify", "loma_skills",
})

# Personal tools: always grantable to an active user (each tool fails closed
# if that user has no connection for the underlying account).
PERSONAL_TOOLS = frozenset(AUTH_TOOLS)

# Integration-backed CLI tools -> provider key in db.integrations. Grants are
# filtered by integration sharing rules at issue time; the tool itself loads
# the org credential server-side (never in the worker).
INTEGRATION_TOOLS = {
    "apollo": "apollo",
    "ashby": "ashby",
    "cdn_upload": "cdn_r2",
    "dataroom": "dataroom",
    "github_pr_resolve": "github",
    "grafana": "grafana",
    "grain": "grain",
    "linear": "linear",
    "monetize_now": "monetize_now",
    "phantombuster": "phantombuster",
    "posthog": "posthog",
    "pylon": "pylon",
    "sentry": "sentry",
    "slack_reader": "slack_bot",
    "stitch": "stitch",
    "zoho_books": "zoho_books",
}

# Credential-free utility tools (run server-side for consistency; their file
# outputs land in backend temp paths that file delivery already serves).
UTILITY_TOOLS = frozenset({"diagrams", "pptx_creator", "agreement_review"})

ALL_TOOLS = frozenset(PERSONAL_TOOLS) | frozenset(INTEGRATION_TOOLS) | UTILITY_TOOLS

_MAX_ARGV_ITEMS = 64
_MAX_ARG_CHARS = 20_000
_MAX_ARGV_CHARS = 200_000
_MAX_FILES = 8
_MAX_FILES_CHARS = 1_500_000
_MAX_OUTPUT_BYTES = 1_000_000
_TOOL_TIMEOUT_SECONDS = 180


def _strip_identity_flags(argv: list[str]) -> list[str]:
    """Remove any worker-supplied --user-email/--auth-token pairs."""
    cleaned: list[str] = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg in ("--user-email", "--auth-token"):
            skip = True
            continue
        cleaned.append(arg)
    return cleaned


class ToolInvoke:
    """Execute a first-party Loma CLI tool server-side for the run owner."""

    accepts_params = True

    @staticmethod
    def valid_resource(value) -> bool:
        return isinstance(value, str) and value in ALL_TOOLS

    @staticmethod
    def _validate_params(params) -> tuple[list[str], dict[str, str]]:
        if not isinstance(params, dict) or set(params) - {"argv", "files"}:
            raise Denied()
        argv = params.get("argv") or []
        files = params.get("files") or {}
        if (not isinstance(argv, list) or len(argv) > _MAX_ARGV_ITEMS
                or not all(isinstance(a, str) and len(a) <= _MAX_ARG_CHARS for a in argv)
                or sum(len(a) for a in argv) > _MAX_ARGV_CHARS):
            raise Denied()
        if (not isinstance(files, dict) or len(files) > _MAX_FILES
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items())
                or sum(len(v) for v in files.values()) > _MAX_FILES_CHARS):
            raise Denied()
        return argv, files

    async def execute(self, db, email, tool_name, params=None):
        if not self.valid_resource(tool_name):
            raise Denied()
        argv, files = self._validate_params(params)
        argv = _strip_identity_flags(argv)

        script = PROJECT_ROOT / "tools" / f"{tool_name}.py"
        if not script.is_file():
            return {"exit_code": 1, "stdout": "",
                    "stderr": f"Tool {tool_name} is not available on this deployment."}

        # Materialize worker-uploaded file contents into a private temp dir and
        # rewrite matching argv entries to the server-side copies.
        tmp_dir = None
        if files:
            tmp_dir = tempfile.mkdtemp(prefix="loma-broker-files-")
            os.chmod(tmp_dir, 0o700)
            mapping: dict[str, str] = {}
            for index, (worker_path, content) in enumerate(files.items()):
                name = os.path.basename(worker_path)[-128:] or f"file{index}"
                local = os.path.join(tmp_dir, f"{index}-{name}")
                with open(local, "w", encoding="utf-8") as fh:
                    fh.write(content)
                mapping[worker_path] = local
            argv = [mapping.get(a, a) for a in argv]

        if tool_name in AUTH_TOOLS:
            # Server-minted, short-lived identity token consumed by the tool's
            # existing verification path. Never returned to the worker.
            from tools._auth_token import create_user_auth_token
            argv = [*argv, "--user-email", email, "--auth-token", create_user_auth_token(email)]

        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(script), *argv,
                cwd=str(PROJECT_ROOT),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=_TOOL_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return {"exit_code": 124, "stdout": "",
                        "stderr": f"Tool {tool_name} timed out after {_TOOL_TIMEOUT_SECONDS}s."}
        finally:
            if tmp_dir:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)

        return {
            "exit_code": process.returncode,
            "stdout": stdout[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
            "stderr": stderr[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
        }


class ModelRequest:
    """Admission-only operation for the model gateway.

    The gateway calls the broker with this operation before proxying a model
    request; the operation itself performs no I/O. Providers must be granted
    at issue time and configured with a server-side credential.
    """

    def __init__(self, providers: set[str] | None = None):
        self._providers = set(providers or [])

    def valid_resource(self, value) -> bool:
        return isinstance(value, str) and value in self._providers

    async def execute(self, db, email, provider):
        if not self.valid_resource(provider):
            raise Denied()
        return {"ok": True, "provider": provider}
