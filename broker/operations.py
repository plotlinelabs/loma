"""Broker operations for isolated workers.

Workers hold no credentials and no database access. Every tool invocation is
forwarded to the broker, which authenticates the run capability, enforces the
grant, and executes the real tool **server-side** with the run owner's
identity. Credentials (OAuth tokens, integration API keys, the encryption
key, the database URI) never enter a worker process.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import sys
import signal
import tempfile
from pathlib import Path

from broker.service import Denied
from broker.tool_policy import prepare_argv

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Tools that authenticate the acting user via --user-email/--auth-token.
# The broker always injects server-minted values for these; worker-supplied
# identity flags are stripped unconditionally for every tool.
AUTH_TOOLS = frozenset({
    "gmail", "google_drive", "google_calendar", "google_sheets",
    "google_slides", "google_docs_personal", "google_apps_script",
    "slack_user", "telegram", "notify", "loma_skills", "grain",
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


class ToolOutputLimit(RuntimeError):
    pass


async def _collect_tool_output(process):
    async def bounded_read(stream):
        data = bytearray()
        while chunk := await stream.read(65536):
            data.extend(chunk)
            if len(data) > _MAX_OUTPUT_BYTES:
                raise ToolOutputLimit()
        return bytes(data)

    tasks = [asyncio.create_task(bounded_read(process.stdout)),
             asyncio.create_task(bounded_read(process.stderr)),
             asyncio.create_task(process.wait())]
    try:
        stdout, stderr, _ = await asyncio.gather(*tasks)
        return stdout, stderr
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _terminate_tool(process):
    # Each tool is a session leader. Stop descendants as well as the CLI.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


def _strip_identity_flags(argv: list[str]) -> list[str]:
    """Remove any worker-supplied --user-email/--auth-token pairs."""
    cleaned: list[str] = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg.startswith(("--user-email=", "--auth-token=")):
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
        argv = params.get("argv", [])
        files = params.get("files", {})
        if (not isinstance(argv, list) or len(argv) > _MAX_ARGV_ITEMS
                or not all(isinstance(a, str) and len(a) <= _MAX_ARG_CHARS for a in argv)
                or sum(len(a) for a in argv) > _MAX_ARGV_CHARS):
            raise Denied()
        if (not isinstance(files, dict) or len(files) > _MAX_FILES
                or not all(isinstance(k, str) and 0 < len(k) <= 4096 for k in files)):
            raise Denied()
        decoded = {}
        total = 0
        for name, value in files.items():
            if isinstance(value, str):
                content = value.encode("utf-8")
            elif (isinstance(value, dict) and set(value) == {"encoding", "data"}
                  and value["encoding"] == "base64" and isinstance(value["data"], str)
                  and len(value["data"]) <= _MAX_FILES_CHARS * 4 // 3 + 4):
                try:
                    content = base64.b64decode(value["data"], validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise Denied() from exc
            else:
                raise Denied()
            total += len(content)
            if total > _MAX_FILES_CHARS:
                raise Denied()
            decoded[name] = content
        return argv, decoded

    async def execute(self, db, email, tool_name, params=None):
        if not self.valid_resource(tool_name):
            raise Denied()
        if tool_name in INTEGRATION_TOOLS:
            from integrations.access import require_provider
            await require_provider(db, INTEGRATION_TOOLS[tool_name], email)
        argv, files = self._validate_params(params)
        argv = _strip_identity_flags(argv)

        # Validate before touching disk or invoking privileged backend tools.
        argv = prepare_argv(tool_name, argv, {key: key for key in files})

        script = PROJECT_ROOT / "tools" / f"{tool_name}.py"
        if not script.is_file():
            return {"exit_code": 1, "stdout": "",
                    "stderr": f"Tool {tool_name} is not available on this deployment."}

        tmp_dir = None
        identity_token = None
        try:
            # Only request-owned uploads may become local CLI inputs.
            if files:
                tmp_dir = tempfile.mkdtemp(prefix="loma-broker-files-")
                os.chmod(tmp_dir, 0o700)
                mapping: dict[str, str] = {}
                for index, (worker_path, content) in enumerate(files.items()):
                    suffix = Path(worker_path).suffix
                    if not suffix.isascii() or not suffix[1:].isalnum() or len(suffix) > 12:
                        suffix = ""
                    local = os.path.join(tmp_dir, f"input-{index}{suffix}")
                    with open(local, "wb") as fh:
                        fh.write(content)
                    mapping[worker_path] = local
                argv = prepare_argv(tool_name, argv, mapping)

            if tool_name in AUTH_TOOLS or tool_name in INTEGRATION_TOOLS:
                from tools._auth_token import create_user_auth_token
                identity_token = create_user_auth_token(email)
                # argparse Google tools require the token before the subcommand.
                argv = ["--auth-token", identity_token, *argv, "--user-email", email]

            process = await asyncio.create_subprocess_exec(
                sys.executable, str(script), *argv,
                cwd=str(PROJECT_ROOT), start_new_session=True,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    _collect_tool_output(process), timeout=_TOOL_TIMEOUT_SECONDS,
                )
            except (asyncio.TimeoutError, ToolOutputLimit) as exc:
                await _terminate_tool(process)
                return {"exit_code": 124 if isinstance(exc, asyncio.TimeoutError) else 125,
                        "stdout": "", "stderr": "Tool execution exceeded its time or output limit."}
            except BaseException:
                await _terminate_tool(process)
                raise
        finally:
            if tmp_dir:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)

        from utils.secret_redaction import redact_secrets
        def clean_output(data):
            text = data.decode("utf-8", errors="replace")
            if identity_token:
                text = text.replace(identity_token, "[REDACTED]")
            return redact_secrets(text)
        return {"exit_code": process.returncode,
                "stdout": clean_output(stdout), "stderr": clean_output(stderr)}



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


class SubscriptionRequest:
    """Admission-only operation for the subscription-credential gateway.

    Checked on every proxied request for a pooled subscription account
    (Claude today). Performs no I/O itself: admission gives revocation,
    call-budget, TTL, and active-account semantics per request.
    """

    RESOURCES = frozenset({"claude", "codex", "opencode"})

    def valid_resource(self, value) -> bool:
        return isinstance(value, str) and value in self.RESOURCES

    async def execute(self, db, email, provider):
        if not self.valid_resource(provider):
            raise Denied()
        return {"ok": True, "provider": provider}


class McpRequest:
    """Check current provider ownership/sharing before each gateway request."""

    @staticmethod
    def valid_resource(value):
        import re
        return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value))

    async def execute(self, db, email, server_name):
        from integrations.registry import PROVIDER_CATALOG
        from integrations.access import require_provider
        provider = next((key for key, entry in PROVIDER_CATALOG.items()
                         if entry.get("mcp_server_name") == server_name), server_name)
        if provider in ("grain", "hubspot", "notion"):
            # Personal providers never silently use an organization connection.
            from api.oauth_helpers import get_valid_provider_token
            if not await get_valid_provider_token(email, provider, db=db):
                raise Denied()
        else:
            await require_provider(db, provider, email)
            doc = await db.integrations.find_one({"provider": provider})
            if doc and doc.get("is_custom") and doc.get("auth_mode") == "oauth":
                from api.oauth_helpers import get_valid_custom_mcp_token
                if not await get_valid_custom_mcp_token(email, provider, db=db):
                    raise Denied()
        return {"ok": True}
