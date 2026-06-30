import asyncio
import hashlib
import json as _json
import os
import re
import base64
import tempfile
import logging
from pathlib import Path
from typing import AsyncGenerator

import shutil
import tarfile
import zipfile

import yaml
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)

from agent.pool import get_pool

logger = logging.getLogger(__name__)

# Max times to transparently retry with a different account on rate limit
MAX_ACCOUNT_RETRIES = 3
DEFAULT_AGENT_MODEL = "opencode-go/deepseek-v4-flash"

CONFIG_PATH = Path(os.environ.get("AGENT_CONFIG_PATH", Path(__file__).parent.parent / "config.yaml"))


def _default_agent_model() -> str:
    return os.environ.get("AGENT_DEFAULT_MODEL", DEFAULT_AGENT_MODEL).strip() or DEFAULT_AGENT_MODEL


def _selected_model_is_claude(selected_model: str | None) -> bool:
    if not selected_model:
        return False
    model = selected_model.split("/", 1)[1] if "/" in selected_model else selected_model
    normalized_model = model.lower()
    normalized_full = selected_model.lower()
    return (
        normalized_model.startswith("claude-")
        or "claude" in normalized_model
        or normalized_full.startswith("anthropic/")
    )


def _normalize_claude_model(selected_model: str | None) -> str | None:
    if not selected_model or not _selected_model_is_claude(selected_model):
        return None
    return selected_model.split("/", 1)[1] if "/" in selected_model else selected_model


def _redact_prompt_for_logs(prompt: str) -> str:
    prompt = re.sub(
        r"(\[Personal Tools Auth Token:\s*)[^\]]+(\])",
        r"\1<redacted>\2",
        prompt,
    )
    prompt = re.sub(
        r"(--auth-token\s+)[^\s`]+",
        r"\1<redacted>",
        prompt,
    )
    return prompt


def _resolve_env_vars(value: str) -> str:
    """Replace ${VAR_NAME} patterns with environment variable values."""
    def replacer(match):
        var_name = match.group(1)
        return os.environ.get(var_name, "")
    return re.sub(r"\$\{(\w+)\}", replacer, value)


def _resolve_config_env_vars(obj):
    """Recursively resolve environment variables in config values."""
    if isinstance(obj, str):
        return _resolve_env_vars(obj)
    elif isinstance(obj, dict):
        return {k: _resolve_config_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_resolve_config_env_vars(item) for item in obj]
    return obj


def load_config() -> dict:
    """Load base config. MCP servers come from DB integrations (merged separately).

    If config.yaml exists, it's loaded for any non-MCP settings. Otherwise
    returns a minimal default. max_turns is now read from AGENT_MAX_TURNS env var.
    """
    config = {"mcp_servers": {}}
    if CONFIG_PATH.exists():
        logger.info("Loading config from %s", CONFIG_PATH)
        with open(CONFIG_PATH) as f:
            raw = yaml.safe_load(f) or {}
        config.update(_resolve_config_env_vars(raw))
    config.setdefault("mcp_servers", {})
    logger.info("Config loaded — MCP servers: %s", list(config.get("mcp_servers", {}).keys()))
    return config


async def merge_db_integrations(config: dict) -> dict:
    """Merge DB-stored integrations into an existing config dict.

    Called after startup to overlay DB integration keys onto the base config.
    If a provider exists in both config.yaml and DB, the DB key wins.
    """
    try:
        from observability.db import get_db
        from integrations.registry import PROVIDER_CATALOG
        from api.oauth_helpers import decrypt_token

        db = get_db()
        if db is None:
            return config

        mcp_servers = config.get("mcp_servers", {})
        async for integration in db.integrations.find({"status": "active"}):
            provider = integration["provider"]

            # Custom connectors carry their own inline remote MCP config (no
            # catalog entry). Added by admins from the Integrations page.
            if integration.get("is_custom"):
                try:
                    cfg = {"type": "http", "url": integration["mcp_url"]}
                    if integration.get("api_key_encrypted"):
                        header = integration.get("auth_header") or "Authorization"
                        cfg["headers"] = {
                            header: decrypt_token(integration["api_key_encrypted"]),
                        }
                    mcp_servers[provider] = cfg
                    logger.info("Loaded custom MCP connector '%s' from DB", provider)
                except Exception:
                    logger.exception("Failed to load custom MCP connector %s", provider)
                continue

            catalog_entry = PROVIDER_CATALOG.get(provider)
            if not catalog_entry:
                continue
            try:
                api_key = decrypt_token(integration["api_key_encrypted"])
                # Decrypt extra fields (e.g., GitBook URL)
                extra_fields = {}
                for k, v in (integration.get("extra_fields_encrypted") or {}).items():
                    extra_fields[k] = decrypt_token(v)
                template = catalog_entry["mcp_config_template"]
                mcp_cfg = _resolve_mcp_template(template, api_key, extra_fields)
                server_name = catalog_entry["mcp_server_name"]
                mcp_servers[server_name] = mcp_cfg
                logger.info("Loaded %s MCP config from DB integration", server_name)
            except Exception:
                logger.exception("Failed to load integration %s from DB", provider)

        config["mcp_servers"] = mcp_servers
    except Exception:
        logger.exception("Failed to load integrations from DB — using config.yaml only")

    return config


def _resolve_mcp_template(template: dict, api_key: str, extra_fields: dict | None = None) -> dict:
    """Resolve {{API_KEY}}, {{placeholder}}, and ${ENV_VAR} patterns in an MCP config template."""
    replacements = {"API_KEY": api_key, **(extra_fields or {})}

    def _sub(text: str) -> str:
        # First resolve {{placeholder}} patterns from replacements
        for key, val in replacements.items():
            text = text.replace("{{" + key + "}}", val)
        # Then resolve ${VAR} patterns from environment
        text = _resolve_env_vars(text)
        return text

    result = {}
    for k, v in template.items():
        if k == "url_template":
            result["url"] = _sub(v)
        elif k == "headers_template":
            result["headers"] = {hk: _sub(hv) for hk, hv in v.items()}
        elif k == "env_template":
            result["env"] = {ek: _sub(ev) for ek, ev in v.items()}
        else:
            result[k] = v
    return result


# \u2500\u2500 Artifact Detection \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

# Languages whose fenced code blocks should be treated as artifacts
# when they exceed the minimum size threshold.
_ARTIFACT_LANGUAGES = {
    "html", "svg",
    "javascript", "js", "typescript", "ts", "tsx", "jsx",
    "python", "py", "java", "go", "rust", "ruby", "rb",
    "c", "cpp", "csharp", "cs", "swift", "kotlin",
    "css", "scss", "less", "sql",
    "json", "yaml", "yml", "toml", "xml",
    "bash", "sh", "shell",
    "markdown", "md",
    "mermaid",
    "dart", "lua", "r", "scala",
    "dockerfile", "makefile", "graphql",
    "csv", "text", "txt",
}

# Minimum character threshold \u2014 only promote code blocks to artifacts
# if they are substantial enough to warrant a side panel.
_ARTIFACT_MIN_CHARS = 200
_ARTIFACT_MIN_LINES = 8

# Regex to find fenced code blocks in text
_CODE_BLOCK_RE = re.compile(
    r"```(\w*)\n(.*?)```",
    re.DOTALL,
)


def _detect_artifacts(text: str) -> list[dict]:
    """
    Scan text for fenced code blocks that qualify as artifacts.
    Returns a list of artifact dicts: {language, content, title, start, end}.
    """
    artifacts = []
    for match in _CODE_BLOCK_RE.finditer(text):
        lang = match.group(1).lower() or "text"
        content = match.group(2).rstrip()
        if not content:
            continue

        # Check minimum thresholds
        if len(content) < _ARTIFACT_MIN_CHARS and content.count("\n") < _ARTIFACT_MIN_LINES:
            continue

        # Check if it's a recognized language
        if lang not in _ARTIFACT_LANGUAGES:
            continue

        # Generate a deterministic artifact ID from content hash
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
        artifact_id = f"art_{content_hash}"

        # Infer title from content
        title = _infer_artifact_title(lang, content)

        artifacts.append({
            "artifact_id": artifact_id,
            "language": lang,
            "content": content,
            "title": title,
            "start": match.start(),
            "end": match.end(),
        })

    return artifacts


def _infer_artifact_title(language: str, content: str) -> str:
    """Infer a human-readable title for an artifact from its content."""
    # HTML: look for <title> tag
    if language in ("html", "svg"):
        title_match = re.search(r"<title[^>]*>([^<]+)</title>", content, re.IGNORECASE)
        if title_match:
            return title_match.group(1).strip()
        return "HTML Document" if language == "html" else "SVG Image"

    # Markdown: use first heading
    if language in ("markdown", "md"):
        heading_match = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        if heading_match:
            return heading_match.group(1).strip()
        return "Markdown Document"

    # Mermaid: infer from diagram type
    if language == "mermaid":
        # Extract the diagram type from the first line
        first_line = content.strip().split("\n")[0].strip().lower()
        diagram_types = {
            "graph": "Flowchart",
            "flowchart": "Flowchart",
            "sequencediagram": "Sequence Diagram",
            "sequence": "Sequence Diagram",
            "classDiagram": "Class Diagram",
            "classdiagram": "Class Diagram",
            "statediagram": "State Diagram",
            "erdiagram": "ER Diagram",
            "gantt": "Gantt Chart",
            "pie": "Pie Chart",
            "journey": "User Journey",
            "gitgraph": "Git Graph",
            "mindmap": "Mind Map",
            "timeline": "Timeline",
            "sankey": "Sankey Diagram",
            "quadrantchart": "Quadrant Chart",
            "xychart": "XY Chart",
            "block": "Block Diagram",
        }
        for keyword, label in diagram_types.items():
            if first_line.startswith(keyword.lower()):
                return label
        return "Mermaid Diagram"

    # Python: use module docstring or first class/function name
    if language in ("python", "py"):
        class_match = re.search(r"^class\s+(\w+)", content, re.MULTILINE)
        if class_match:
            return f"{class_match.group(1)}.py"
        func_match = re.search(r"^def\s+(\w+)", content, re.MULTILINE)
        if func_match:
            return f"{func_match.group(1)}.py"
        return "Python Script"

    # JavaScript/TypeScript: look for export/function/class
    if language in ("javascript", "js", "typescript", "ts", "tsx", "jsx"):
        comp_match = re.search(r"(?:export\s+(?:default\s+)?)?(?:function|class|const)\s+(\w+)", content)
        if comp_match:
            ext = {"javascript": "js", "js": "js", "typescript": "ts", "ts": "ts", "tsx": "tsx", "jsx": "jsx"}.get(language, "js")
            return f"{comp_match.group(1)}.{ext}"
        return f"Code.{language}"

    # JSON: look for common top-level keys
    if language == "json":
        try:
            parsed = _json.loads(content)
            if isinstance(parsed, dict):
                if "name" in parsed:
                    return f"{parsed['name']}.json"
                if "title" in parsed:
                    return f"{parsed['title']}.json"
        except Exception:
            pass
        return "Data.json"

    # CSS/SCSS
    if language in ("css", "scss", "less"):
        return f"Styles.{language}"

    # SQL
    if language == "sql":
        return "Query.sql"

    # Shell
    if language in ("bash", "sh", "shell"):
        return "Script.sh"

    # Fallback
    lang_labels = {
        "go": "Go", "rust": "Rust", "ruby": "Ruby", "java": "Java",
        "swift": "Swift", "kotlin": "Kotlin", "dart": "Dart",
        "yaml": "YAML", "yml": "YAML", "toml": "TOML", "xml": "XML",
        "csv": "CSV", "dockerfile": "Dockerfile", "makefile": "Makefile",
        "graphql": "GraphQL",
    }
    label = lang_labels.get(language, language.upper())
    return f"{label} Code"


# Track artifact versions per conversation (keyed by title)
_artifact_version_counters: dict[str, dict[str, int]] = {}


def _get_artifact_version(conversation_id: str | None, title: str) -> int:
    """Get and increment the version number for an artifact title within a conversation."""
    conv_key = conversation_id or "__default__"
    if conv_key not in _artifact_version_counters:
        _artifact_version_counters[conv_key] = {}
    counters = _artifact_version_counters[conv_key]
    version = counters.get(title, 0) + 1
    counters[title] = version
    return version


# \u2500\u2500 File Artifact Detection \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

# Binary file extensions that should be emitted as file artifacts
_FILE_ARTIFACT_EXTENSIONS = {
    ".pdf": ("pdf", "pdf"),
    ".docx": ("docx", "docx"),
    ".pptx": ("pptx", "pptx"),
    ".xlsx": ("xlsx", "xlsx"),
    ".xls": ("xlsx", "xlsx"),
    ".doc": ("docx", "docx"),
    ".ppt": ("pptx", "pptx"),
}

# Regex patterns to detect file writes in Bash output
_FILE_WRITE_PATTERNS = [
    # Common Python file write patterns
    re.compile(r"(?:wrote|saved|created|generated|exported)\s+(?:to\s+)?['\"]?(/tmp/[^'\"\s]+\.(?:pdf|docx|pptx|xlsx|xls|doc|ppt))", re.IGNORECASE),
    # Direct file path mentions after write operations
    re.compile(r"(?:output|file|document|report|spreadsheet|presentation)\s*(?::|=|at|in)\s*['\"]?(/tmp/[^'\"\s]+\.(?:pdf|docx|pptx|xlsx|xls|doc|ppt))", re.IGNORECASE),
    # Python-style print of file path
    re.compile(r"(/tmp/[^\s'\"]+\.(?:pdf|docx|pptx|xlsx|xls|doc|ppt))\b", re.IGNORECASE),
]


def _encode_file_id(file_path: str) -> str:
    """Encode an absolute file path as a URL-safe base64 file ID."""
    return base64.urlsafe_b64encode(file_path.encode()).decode().rstrip("=")


def _detect_file_artifact(file_path: str) -> dict | None:
    """
    Check if a file path corresponds to a binary file artifact.
    Registers the file for serving via /api/files/ and returns an artifact dict.
    Returns None if the file doesn't exist or isn't a supported type.
    """
    if not os.path.isfile(file_path):
        return None

    ext = Path(file_path).suffix.lower()
    if ext not in _FILE_ARTIFACT_EXTENSIONS:
        return None

    language, file_type = _FILE_ARTIFACT_EXTENSIONS[ext]
    filename = Path(file_path).name
    file_size = os.path.getsize(file_path)
    content_hash = hashlib.sha256(f"{file_path}:{file_size}".encode()).hexdigest()[:12]

    # Register the file for serving — this copies it to the served-files dir
    # and adds it to the in-memory registry so /api/files/{id} can serve it.
    try:
        from api.routes import register_served_file
        file_info = register_served_file(file_path)
        file_url = file_info["url"]
        logger.info("[FILE ARTIFACT] Registered %s -> %s", file_path, file_url)
    except Exception as e:
        logger.warning("[FILE ARTIFACT] Failed to register %s: %s", file_path, e)
        # Fallback: use base64-encoded path (won't serve without registration)
        file_id = _encode_file_id(file_path)
        file_url = f"/api/files/{file_id}"

    return {
        "type": "file_artifact",
        "artifact_id": f"file_{content_hash}",
        "title": filename,
        "language": language,
        "file_url": file_url,
        "file_size": file_size,
        "file_type": file_type,
        "version": 1,
    }


def _extract_file_paths_from_tool(tool_name: str, tool_input, result_text: str) -> list[str]:
    """
    Extract file paths from tool calls and results that may have created binary files.
    """
    paths = []
    name_lower = tool_name.lower()

    # Write tool: file_path is in the input
    if name_lower == "write":
        if isinstance(tool_input, dict):
            fp = tool_input.get("file_path", "")
            if fp and Path(fp).suffix.lower() in _FILE_ARTIFACT_EXTENSIONS:
                paths.append(fp)

    # Bash tool: scan the output for file paths
    if name_lower == "bash" and result_text:
        for pattern in _FILE_WRITE_PATTERNS:
            for match in pattern.finditer(result_text):
                paths.append(match.group(1))

    # Also check Bash command input for file output paths
    if name_lower == "bash" and isinstance(tool_input, dict):
        cmd = tool_input.get("command", "")
        if cmd:
            # Check for output redirection or -o flags
            for pattern in _FILE_WRITE_PATTERNS:
                for match in pattern.finditer(cmd):
                    paths.append(match.group(1))

    return paths


# \u2500\u2500 Archive Extraction \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500# ── File Path Detection (for dashboard file delivery) ─────────────────────

# Regex to find generated output file paths in text. Python tempfile paths can
# live under /tmp on Linux or /var/folders on macOS.
_TEMP_FILE_PATH = "/(?:tmp|var/folders)/[^\\s`\"'<>\\n]+"
_FILE_PATH_RE = re.compile(
    r"(?:saved (?:to|at|in)|(?:available|download|file) at|uploaded to|written to|created at"
    r"|output(?:ted)? to|generated at|exported to|the file is at|file[:\s]+)[\s`]*"
    rf"({_TEMP_FILE_PATH}\.(?:pdf|png|jpg|jpeg|gif|svg|csv|xlsx|xls|docx|pptx|zip|tar|gz|json|html|txt|md|mp4|mp3|wav))"
    rf"|({_TEMP_FILE_PATH}\.(?:pdf|png|jpg|jpeg|gif|svg|csv|xlsx|xls|docx|pptx|zip|tar|gz))",
    re.IGNORECASE,
)

# File extensions that should always be offered as downloads
_DOWNLOADABLE_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".csv", ".xlsx", ".xls", ".docx", ".pptx",
    ".zip", ".tar", ".gz", ".tgz",
    ".json", ".html", ".txt", ".md",
    ".mp4", ".mp3", ".wav",
}


def _detect_file_paths(text: str) -> list[str]:
    """Scan text for generated output file paths that the agent generated.

    Returns deduplicated list of valid file paths.
    """
    paths: list[str] = []
    seen: set[str] = set()
    for match in _FILE_PATH_RE.finditer(text):
        # Get whichever group matched
        path = match.group(1) or match.group(2)
        if path and path not in seen:
            # Strip trailing punctuation
            path = path.rstrip(".,;:!?)")
            p = Path(path)
            if p.suffix.lower() in _DOWNLOADABLE_EXTENSIONS and p.exists() and p.is_file():
                paths.append(path)
                seen.add(path)
    return paths


# ── Archive Extraction ────────────────────────────────────────────────────


ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"}
MAX_UNCOMPRESSED_SIZE = 100 * 1024 * 1024   # 100 MB total
MAX_SINGLE_FILE_SIZE = 20 * 1024 * 1024     # 20 MB per file
MAX_FILE_COUNT = 200                         # max files to extract


def _human_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _extract_archive(archive_path: str, extract_dir: str) -> dict:
    """
    Extract an archive and return metadata about extracted files.

    Returns:
        {
            "success": bool,
            "files": [{"name": str, "path": str, "size": int}],
            "error": str | None
        }
    """
    ext = Path(archive_path).suffix.lower()
    # Handle .tar.gz files that may have been saved as .gz
    name_lower = Path(archive_path).name.lower()
    extracted_files = []

    try:
        if ext == ".zip":
            with zipfile.ZipFile(archive_path, "r") as z:
                # Security: check for zip bomb
                total_size = sum(info.file_size for info in z.infolist())
                if total_size > MAX_UNCOMPRESSED_SIZE:
                    return {
                        "success": False,
                        "files": [],
                        "error": f"Archive too large when uncompressed ({_human_size(total_size)} > {_human_size(MAX_UNCOMPRESSED_SIZE)})",
                    }

                members = [info for info in z.infolist() if not info.is_dir()]
                if len(members) > MAX_FILE_COUNT:
                    return {
                        "success": False,
                        "files": [],
                        "error": f"Too many files in archive ({len(members)} > {MAX_FILE_COUNT})",
                    }

                for info in members:
                    # Security: prevent path traversal
                    if ".." in info.filename or info.filename.startswith("/"):
                        logger.warning("[ARCHIVE] Skipping suspicious path: %s", info.filename)
                        continue

                    if info.file_size > MAX_SINGLE_FILE_SIZE:
                        logger.info("[ARCHIVE] Skipping large file: %s (%d bytes)", info.filename, info.file_size)
                        continue

                    extracted_path = z.extract(info, extract_dir)
                    extracted_files.append({
                        "name": info.filename,
                        "path": extracted_path,
                        "size": info.file_size,
                    })

        elif ext in (".tar", ".gz", ".tgz") or name_lower.endswith(".tar.gz"):
            mode = "r:gz" if ext in (".gz", ".tgz") or name_lower.endswith(".tar.gz") else "r"
            with tarfile.open(archive_path, mode) as t:
                # Security: check members before extraction
                members = [m for m in t.getmembers() if m.isfile()]
                total_size = sum(m.size for m in members)

                if total_size > MAX_UNCOMPRESSED_SIZE:
                    return {
                        "success": False,
                        "files": [],
                        "error": f"Archive too large ({_human_size(total_size)})",
                    }

                if len(members) > MAX_FILE_COUNT:
                    return {
                        "success": False,
                        "files": [],
                        "error": f"Too many files ({len(members)})",
                    }

                for member in members:
                    if ".." in member.name or member.name.startswith("/"):
                        continue
                    if member.size > MAX_SINGLE_FILE_SIZE:
                        continue

                    t.extract(member, extract_dir)
                    extracted_path = os.path.join(extract_dir, member.name)
                    extracted_files.append({
                        "name": member.name,
                        "path": extracted_path,
                        "size": member.size,
                    })

        else:
            return {
                "success": False,
                "files": [],
                "error": f"Unsupported archive format: {ext} (only .zip, .tar, .gz, .tgz supported natively)",
            }

        return {"success": True, "files": extracted_files, "error": None}

    except (zipfile.BadZipFile, tarfile.TarError) as e:
        return {"success": False, "files": [], "error": f"Corrupt archive: {e}"}
    except Exception as e:
        return {"success": False, "files": [], "error": str(e)}


async def stream_agent(
    prompt: str,
    conversation_context: str = "",
    files: list | None = None,
    observer=None,
    include_steps: bool = False,
    source: str = "slack",
    user_email: str | None = None,
    selected_model: str | None = None,
    raise_on_opencode_error: bool = False,
) -> AsyncGenerator[str | dict, None]:
    """
    Run the Claude agent and yield text blocks as they arrive.

    Each yield is a substantial text chunk suitable for posting as a Slack message.

    Args:
        prompt: The user's message
        conversation_context: Previous messages in the thread for context
        files: Optional list of file dicts from download_slack_files()
               Each has: name, mimetype, type ("image"/"text"), data
        observer: Optional ConversationObserver for logging to MongoDB
        include_steps: If True, also yield dicts for tool calls/results
        user_email: Authenticated user's email for personal tool auth
        selected_model: Optional dashboard-selected model in provider/model format. Claude-family
                        models are routed through Claude Agent SDK; other selected models use OpenCode.
        raise_on_opencode_error: If True, propagate OpenCode runtime errors after
                        recording them instead of converting them to assistant text.

    Yields:
        Text strings as the agent produces them.
        When include_steps=True, also yields dicts:
          {"type": "tool_call", "name": str, "tool_use_id": str}
          {"type": "tool_result", "tool_use_id": str, "is_error": bool}
          {"type": "turn", "turn_number": int}
          {"type": "file_artifact", ...}  # binary file artifacts
    """
    # Build text portion of the prompt with source marker for the pooled system prompt
    text_parts = [f"[Source: {source}]"]

    # Inject authenticated user identity and auth token for personal tools
    if user_email:
        from tools._auth_token import create_user_auth_token
        auth_token = create_user_auth_token(user_email)
        text_parts.append(
            f"[Authenticated User: {user_email}]\n"
            f"[Personal Tools Auth Token: {auth_token}]\n"
            f"When using personal tools (gmail, google_drive, google_calendar, "
            f"google_sheets, google_slides, google_docs_personal, slack_user), you MUST pass "
            f"`--user-email {user_email} --auth-token {auth_token}`. "
            f"Never use a different user's email with --user-email."
        )

    if conversation_context:
        text_parts.append(
            f"## Conversation Context (previous messages in this thread)\n"
            f"{conversation_context}"
        )

    text_parts.append(f"## Current Message\n{prompt}")

    # Append file contents inline; save images to temp files for the agent to read
    temp_files = []  # track temp files to clean up later
    image_files: list[dict] = []  # raw image data for OpenCode file parts
    if files:
        for f in files:
            if f["type"] == "text":
                text_parts.append(
                    f"## Attached File: {f['name']}\n```\n{f['data']}\n```"
                )
            elif f["type"] == "image":
                image_files.append(f)
                ext = Path(f["name"]).suffix or ".png"
                tmp = tempfile.NamedTemporaryFile(
                    suffix=ext, prefix="slack_img_", delete=False
                )
                tmp.write(base64.standard_b64decode(f["data"]))
                tmp.close()
                temp_files.append(tmp.name)
                text_parts.append(
                    f"## Attached Image: {f['name']}\n"
                    f"The user attached an image. "
                    f"Read it from this path to view it: {tmp.name}\n"
                    f"IMPORTANT: You MUST read this image file before responding. "
                    f"Use your Read tool on the path above to see the image contents."
                )
                logger.info("[AGENT] Image saved to temp file: %s -> %s", f["name"], tmp.name)
            elif f["type"] == "binary":
                ext = Path(f["name"]).suffix or ".bin"
                tmp = tempfile.NamedTemporaryFile(
                    suffix=ext, prefix="upload_", delete=False
                )
                tmp.write(base64.standard_b64decode(f["data"]))
                tmp.close()
                temp_files.append(tmp.name)

                # Archive handling \u2014 extract and list contents
                if ext.lower() in ARCHIVE_EXTENSIONS:
                    extract_dir = tempfile.mkdtemp(prefix="zip_extract_")
                    temp_files.append(extract_dir)  # track for cleanup

                    extraction_result = _extract_archive(tmp.name, extract_dir)

                    if extraction_result["success"]:
                        file_listing = "\n".join(
                            f"- {ef['name']} ({_human_size(ef['size'])}) \u2014 {ef['path']}"
                            for ef in extraction_result["files"]
                        )
                        text_parts.append(
                            f"## Attached Archive: {f['name']}\n"
                            f"Extracted {len(extraction_result['files'])} file(s) to `{extract_dir}/`:\n"
                            f"{file_listing}\n\n"
                            f"Use your Read tool to examine text/image files, or Bash for binary files. "
                            f"All extracted files are available at the paths listed above."
                        )
                        for ef in extraction_result["files"]:
                            temp_files.append(ef["path"])
                    else:
                        text_parts.append(
                            f"## Attached Archive: {f['name']}\n"
                            f"Failed to extract: {extraction_result['error']}\n"
                            f"The raw archive is saved at: {tmp.name}\n"
                            f"You can try extracting it manually via Bash."
                        )
                    logger.info("[AGENT] Archive processed: %s -> %s", f["name"], extract_dir)
                else:
                    # Non-archive binary file (e.g., XLSX, PPTX)
                    text_parts.append(
                        f"## Attached File: {f['name']}\n"
                        f"The user attached a binary file saved at: {tmp.name}\n"
                        f"Use Bash to inspect or parse this file (e.g., "
                        f"`python3 -c \"import openpyxl; ...\"` for Excel files)."
                    )
                logger.info("[AGENT] Binary file saved to temp: %s -> %s", f["name"], tmp.name)

    full_prompt = "\n\n".join(text_parts)

    max_turns = int(os.environ.get("AGENT_MAX_TURNS", "500"))

    logger.info("=" * 60)
    logger.info("AGENT RUN START")
    safe_prompt_for_logs = _redact_prompt_for_logs(full_prompt)
    logger.info("Prompt: %.200s%s", safe_prompt_for_logs, "..." if len(safe_prompt_for_logs) > 200 else "")
    logger.info("=" * 60)

    turn_count = 0
    yielded_any = False
    last_text = ""
    skills_used: set[str] = set()
    emitted_artifact_ids: set[str] = set()  # track already-emitted artifacts to avoid duplicates
    emitted_file_paths: set[str] = set()  # track already-emitted file paths to avoid duplicates

    last_usage: dict | None = None
    last_total_cost_usd: float | None = None
    # Track pending tool calls for file artifact detection
    pending_tool_inputs: dict[str, tuple[str, object]] = {}  # tool_use_id -> (tool_name, tool_input)

    if observer:
        observer.turn_count = 0

    selected_model = selected_model or _default_agent_model()
    selected_claude_model = _normalize_claude_model(selected_model)

    # OpenCode is the default runtime for OSS installs. Claude family selections
    # stay on the Claude Agent SDK runtime when explicitly selected/configured.
    if selected_model and not selected_claude_model:
        try:
            from agent.opencode_runtime import run_opencode_agent

            async for event in run_opencode_agent(
                full_prompt=full_prompt,
                selected_model=selected_model,
                observer=observer,
                include_steps=include_steps,
                source=source,
                image_files=image_files or None,
            ):
                yield event
        except Exception as e:
            logger.exception("OpenCode agent error")
            if observer:
                await observer.record_error(str(e))
            if raise_on_opencode_error:
                raise
            yield f"Sorry, I encountered an OpenCode error: {e}"
        finally:
            for tmp_path in temp_files:
                try:
                    if os.path.isdir(tmp_path):
                        shutil.rmtree(tmp_path)
                    else:
                        os.unlink(tmp_path)
                    logger.info("[AGENT] Cleaned up temp: %s", tmp_path)
                except OSError:
                    pass
        return

    # Acquire a client from the round-robin pool
    pool = get_pool()
    client = None
    account_email: str | None = None

    try:
        client = await pool.acquire(model=selected_claude_model)
        account = getattr(client, '_pool_account', {})
        account_email = account.get('email')
        active_claude_model = getattr(client, "_pool_model", None) or selected_claude_model
        logger.info("Client acquired (account=%s, model=%s), sending query...", account_email, active_claude_model)
    except Exception as e:
        logger.error("pool.acquire() failed: %s", e)
        if observer:
            await observer.record_error(f"Client initialization failed: {e}")
        status = pool.status()
        if not status["accounts"]:
            yield "No Claude accounts are connected. Ask a team member to log in via Integrations."
        else:
            yield (
                "I'm temporarily unable to start a new session \u2014 all agent slots are in use "
                f"({status['in_use']}/{status['pool_size']} busy, {status['queue_depth']} queued). "
                "Please try again in a minute or two."
            )
        return

    # Record which account is processing this conversation
    if observer and account_email:
        await observer.record_account(account_email)

    # Emit account info so the dashboard can show which account is active
    if include_steps:
        pool_status = pool.status()
        yield {
            "type": "account_info",
            "runtime": "claude",
            "provider": "anthropic",
            "model": getattr(client, "_pool_model", None) or selected_claude_model or selected_model or os.environ.get("CLAUDE_MODEL", ""),
            "account_type": "round_robin",
            "account_email": account_email,
            "pool_available": pool_status["available"],
            "pool_size": pool_status["pool_size"],
        }

    # --- Main conversation loop with transparent retry on rate limit ---
    hit_rate_limit = False

    for attempt in range(MAX_ACCOUNT_RETRIES):
        if attempt > 0:
            # Re-acquire a new client (previous was released after rate limit)
            try:
                client = await pool.acquire()
                account = getattr(client, '_pool_account', {})
                account_email = account.get('email')
                logger.info("Retry %d: acquired new client (account=%s)", attempt, account_email)
                if observer and account_email:
                    await observer.record_account(account_email)
                if include_steps:
                    pool_status = pool.status()
                    yield {
                        "type": "account_info",
                        "account_type": "round_robin",
                        "account_email": account_email,
                        "pool_available": pool_status["available"],
                        "pool_size": pool_status["pool_size"],
                    }
            except Exception as e:
                logger.error("Retry acquire failed: %s", e)
                yield "All accounts are currently rate-limited. Please try again later."
                return

        hit_rate_limit = False

        try:
            await client.query(full_prompt)

            streamed_in_turn = False
            streamed_first_chunk = False

            async for message in client.receive_response():
                if isinstance(message, StreamEvent) and include_steps:
                    evt = message.event
                    if evt.get("type") == "content_block_delta":
                        delta = evt.get("delta", {})
                        if delta.get("type") == "text_delta":
                            chunk = delta.get("text", "")
                            if chunk:
                                if not streamed_first_chunk:
                                    yield {"type": "text", "text": chunk}
                                    streamed_first_chunk = True
                                else:
                                    yield {"type": "text", "text": chunk, "append": True}
                                streamed_in_turn = True
                    continue

                if isinstance(message, AssistantMessage):
                    # Check for rate limit / billing errors before processing
                    if message.error in ("rate_limit", "billing_error", "authentication_error"):
                        is_auth = message.error == "authentication_error"
                        pool.mark_account_exhausted(account_email or "unknown", auth_error=is_auth)
                        logger.warning(
                            "Account %s hit %s, retrying with different account (attempt %d/%d)",
                            account_email, message.error, attempt + 1, MAX_ACCOUNT_RETRIES,
                        )
                        hit_rate_limit = True
                        break

                    turn_count += 1
                    if observer:
                        observer.turn_count = turn_count
                    logger.info("--- Turn %d: AssistantMessage (%d blocks) ---", turn_count, len(message.content))

                    # Hard limit \u2014 stop if we exceed max_turns
                    if turn_count > max_turns:
                        logger.warning(
                            "Turn count %d exceeds max_turns %d \u2014 stopping agent",
                            turn_count, max_turns,
                        )
                        if not yielded_any:
                            yield "I hit my turn limit while working on this. Here's what I have so far \u2014 please ask a follow-up if you need more."
                        break
                    if include_steps:
                        yield {"type": "turn", "turn_number": turn_count}

                    text_already_streamed = streamed_in_turn
                    streamed_in_turn = False
                    streamed_first_chunk = False

                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text = block.text.strip()
                            logger.info("[TEXT] %.300s%s", text, "..." if len(text) > 300 else "")
                            if text:
                                if observer:
                                    await observer.record_text(turn_count, text)
                                last_text = text
                                yielded_any = True

                                # Artifact detection: for dashboard source, extract
                                # large code blocks as separate artifact events
                                if include_steps and source == "dashboard":
                                    detected = _detect_artifacts(text)
                                    if detected:
                                        if not text_already_streamed:
                                            yield text
                                        for art in detected:
                                            if art["artifact_id"] in emitted_artifact_ids:
                                                logger.info("[ARTIFACT SKIP] id=%s already emitted", art["artifact_id"])
                                                continue
                                            emitted_artifact_ids.add(art["artifact_id"])
                                            version = _get_artifact_version(None, art["title"])
                                            logger.info("[ARTIFACT] id=%s lang=%s title=%s (%d chars)",
                                                        art["artifact_id"], art["language"], art["title"], len(art["content"]))
                                            artifact_event = {
                                                "type": "artifact",
                                                "artifact_id": art["artifact_id"],
                                                "title": art["title"],
                                                "content": art["content"],
                                                "language": art["language"],
                                                "version": version,
                                            }
                                            if observer:
                                                await observer.record_artifact({
                                                    "artifact_id": art["artifact_id"],
                                                    "title": art["title"],
                                                    "language": art["language"],
                                                    "version": version,
                                                    "artifact_type": "code",
                                                    "content": art["content"],
                                                })
                                            yield artifact_event
                                        continue  # skip the plain yield below

                                # File path detection: for dashboard source, scan
                                # for /tmp/ file paths and emit file download events
                                if include_steps and source == "dashboard":
                                    file_paths = _detect_file_paths(text)
                                    if file_paths:
                                        if not text_already_streamed:
                                            yield text
                                        for fpath in file_paths:
                                            if fpath in emitted_file_paths:
                                                continue
                                            emitted_file_paths.add(fpath)
                                            try:
                                                from api.routes import register_served_file
                                                file_info = register_served_file(fpath)
                                                logger.info(
                                                    "[FILE] Registered %s as %s (%s, %d bytes)",
                                                    fpath, file_info["file_id"],
                                                    file_info["mime_type"], file_info["size"],
                                                )
                                                yield {
                                                    "type": "file",
                                                    "file_id": file_info["file_id"],
                                                    "name": file_info["name"],
                                                    "url": file_info["url"],
                                                    "mime_type": file_info["mime_type"],
                                                    "size": file_info["size"],
                                                }
                                            except Exception as fe:
                                                logger.warning("[FILE] Failed to register %s: %s", fpath, fe)
                                        continue  # skip the plain yield below

                                if not text_already_streamed:
                                    yield text
                        elif isinstance(block, ToolUseBlock):
                            logger.info("[TOOL CALL] %s (id: %s)", block.name, block.id)
                            logger.info("[TOOL INPUT] %s", _truncate_json(block.input))
                            if block.name == "Skill":
                                skill_name = block.input.get("skill", "unknown") if isinstance(block.input, dict) else "unknown"
                                skills_used.add(skill_name)
                                logger.info("[SKILL LOADED] %s", skill_name)
                            # Track tool input for file artifact detection
                            pending_tool_inputs[block.id] = (block.name, block.input)
                            if observer:
                                await observer.record_tool_call(turn_count, block.name, block.id, block.input)
                            if include_steps:
                                yield {
                                    "type": "tool_call",
                                    "name": block.name,
                                    "tool_use_id": block.id,
                                    "input": _summarize_tool_input(block.name, block.input),
                                }
                        else:
                            logger.info("[BLOCK] type=%s", type(block).__name__)

                elif isinstance(message, ResultMessage):
                    logger.info("--- ResultMessage ---")
                    # Check for error on result
                    if getattr(message, "is_error", False):
                        logger.warning("ResultMessage is_error=True")

                    msg_usage = getattr(message, "usage", None)
                    msg_cost = getattr(message, "total_cost_usd", None)
                    if msg_usage is not None:
                        last_usage = msg_usage if isinstance(msg_usage, dict) else vars(msg_usage) if hasattr(msg_usage, "__dict__") else None
                    if msg_cost is not None:
                        last_total_cost_usd = msg_cost
                    logger.info("[USAGE] cost=%.6f usage=%s", msg_cost or 0, msg_usage)
                    content = getattr(message, "content", None)
                    if content and isinstance(content, list):
                        for block in content:
                            if isinstance(block, ToolResultBlock):
                                logger.info("[TOOL RESULT] tool_use_id=%s, is_error=%s",
                                            block.tool_use_id, getattr(block, "is_error", False))
                                result_text = _extract_result_text(block)
                                logger.info("[TOOL OUTPUT] %.500s%s", result_text, "..." if len(result_text) > 500 else "")
                                if observer:
                                    await observer.record_tool_result(
                                        block.tool_use_id,
                                        getattr(block, "is_error", False),
                                        result_text,
                                    )
                                if include_steps:
                                    yield {
                                        "type": "tool_result",
                                        "tool_use_id": block.tool_use_id,
                                        "is_error": getattr(block, "is_error", False),
                                    }

                                # File artifact detection: check if this tool wrote a binary file
                                if include_steps and source == "dashboard" and not getattr(block, "is_error", False):
                                    tool_info = pending_tool_inputs.get(block.tool_use_id)
                                    if tool_info:
                                        tool_name, tool_input = tool_info
                                        file_paths = _extract_file_paths_from_tool(
                                            tool_name, tool_input, result_text
                                        )
                                        for fpath in file_paths:
                                            if fpath in emitted_file_paths:
                                                continue
                                            artifact = _detect_file_artifact(fpath)
                                            if artifact:
                                                emitted_file_paths.add(fpath)
                                                logger.info(
                                                    "[FILE ARTIFACT] %s -> %s (%s)",
                                                    fpath, artifact["title"],
                                                    _human_size(artifact["file_size"]),
                                                )
                                                if observer:
                                                    await observer.record_artifact({
                                                        "artifact_id": artifact["artifact_id"],
                                                        "title": artifact["title"],
                                                        "language": artifact.get("language", ""),
                                                        "version": artifact.get("version", 1),
                                                        "artifact_type": "file",
                                                        "file_url": artifact.get("file_url", ""),
                                                        "file_size": artifact.get("file_size", 0),
                                                        "file_type": artifact.get("file_type", ""),
                                                    })
                                                yield artifact
                            else:
                                logger.info("[RESULT BLOCK] type=%s", type(block).__name__)
                    else:
                        logger.info("[RESULT] %s", _truncate_json(vars(message) if hasattr(message, '__dict__') else str(message)))
                else:
                    logger.info("[MESSAGE] type=%s", type(message).__name__)

        except Exception as e:
            if isinstance(getattr(e, "__context__", None), GeneratorExit):
                logger.info("Client disconnected, agent generator closed")
                return
            err_str = str(e).lower()
            # Auth errors (401) should failover to a different account, not give up.
            if "authentication_error" in err_str or "401" in err_str or "invalid authentication" in err_str:
                logger.warning(
                    "Account %s hit auth error, retrying with different account (attempt %d/%d): %s",
                    account_email, attempt + 1, MAX_ACCOUNT_RETRIES, str(e)[:200],
                )
                pool.mark_account_exhausted(account_email or "unknown", auth_error=True)
                hit_rate_limit = True
                continue
            logger.exception("Claude agent error")
            if observer:
                await observer.record_error(str(e))
            if "timeout" in err_str or "initialize" in err_str:
                yield (
                    "The session timed out while processing. "
                    "This usually resolves on its own \u2014 please try again shortly."
                )
            else:
                yield f"Sorry, I encountered an error: {e}"
            return
        finally:
            # Always release the client back to pool
            if client is not None:
                await pool.release(client)
                client = None
            # Clean up any temp files (only on final attempt or success)
            if not hit_rate_limit:
                for tmp_path in temp_files:
                    try:
                        if os.path.isdir(tmp_path):
                            shutil.rmtree(tmp_path)
                        else:
                            os.unlink(tmp_path)
                        logger.info("[AGENT] Cleaned up temp: %s", tmp_path)
                    except OSError:
                        pass

        if not hit_rate_limit:
            break  # success \u2014 exit retry loop

    # If all retries exhausted due to rate limits
    if hit_rate_limit:
        logger.error("All %d account retries exhausted due to rate limits", MAX_ACCOUNT_RETRIES)
        if observer:
            await observer.record_error("All accounts rate-limited")
        yield "All accounts are currently rate-limited. Please try again later."
        return

    logger.info("=" * 60)
    logger.info("AGENT RUN COMPLETE \u2014 %d turns", turn_count)
    if skills_used:
        logger.info("SKILLS USED: %s", ", ".join(sorted(skills_used)))
    else:
        logger.info("SKILLS USED: none (answered from core context only)")
    logger.info("=" * 60)

    if observer:
        await observer.record_usage(last_usage, last_total_cost_usd)
        await observer.finish(final_response=last_text)

    if not yielded_any:
        yield "I didn't generate a response. Please try again."



def _summarize_tool_input(tool_name: str, tool_input, max_len: int = 200) -> str:
    """Generate a concise human-readable summary of a tool call's input."""
    if not isinstance(tool_input, dict):
        return str(tool_input)[:max_len]

    name = tool_name.lower()

    # File read tools
    if name in ('read', 'readfile'):
        path = tool_input.get('file_path', '')
        return path

    # Bash / command execution
    if name == 'bash':
        cmd = tool_input.get('command', '')
        desc = tool_input.get('description', '')
        return desc if desc else (cmd[:max_len] if cmd else '')

    # Grep / search
    if name == 'grep':
        pattern = tool_input.get('pattern', '')
        path = tool_input.get('path', '')
        glob = tool_input.get('glob', '')
        parts = [f'"\{pattern}"']
        if path:
            parts.append(f'in {path}')
        if glob:
            parts.append(f'({glob})')
        return ' '.join(parts)

    # Glob / file search
    if name == 'glob':
        pattern = tool_input.get('pattern', '')
        path = tool_input.get('path', '')
        return f'{pattern}' + (f' in {path}' if path else '')

    # Edit tool
    if name == 'edit':
        path = tool_input.get('file_path', '')
        return path

    # Write tool
    if name == 'write':
        path = tool_input.get('file_path', '')
        return path

    # Task / Agent tool
    if name == 'task':
        desc = tool_input.get('description', '')
        prompt = tool_input.get('prompt', '')
        return desc if desc else (prompt[:max_len] if prompt else '')

    # WebFetch
    if name == 'webfetch':
        url = tool_input.get('url', '')
        return url

    # WebSearch
    if name == 'websearch':
        query = tool_input.get('query', '')
        return query

    # Skill
    if name == 'skill':
        skill = tool_input.get('skill', '')
        args = tool_input.get('args', '')
        return f'{skill}' + (f' {args}' if args else '')

    # TodoWrite
    if name == 'todowrite':
        todos = tool_input.get('todos', [])
        if isinstance(todos, list) and todos:
            in_progress = [t for t in todos if isinstance(t, dict) and t.get('status') == 'in_progress']
            if in_progress:
                return in_progress[0].get('activeForm', in_progress[0].get('content', ''))
            return f'{len(todos)} items'
        return ''

    # ToolSearch
    if name == 'toolsearch':
        return tool_input.get('query', '')

    # NotebookEdit
    if name == 'notebookedit':
        path = tool_input.get('notebook_path', '')
        return path

    # MCP tools \u2014 try common field names
    if name.startswith('mcp__'):
        # Try common parameter names
        for key in ('query', 'owner', 'pattern', 'command', 'url', 'message', 'body', 'title', 'name', 'path'):
            val = tool_input.get(key)
            if val and isinstance(val, str):
                return f'{key}: {val[:max_len]}'

    # Generic fallback: show first string value
    for key, val in tool_input.items():
        if isinstance(val, str) and val:
            return f'{key}: {val[:max_len]}'

    return ''


def _truncate_json(obj, max_len: int = 500) -> str:
    """Truncate a JSON-serializable object for logging."""
    import json
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(obj)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _extract_result_text(block) -> str:
    """Extract readable text from a ToolResultBlock."""
    content = getattr(block, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif hasattr(item, "text"):
                parts.append(item.text)
        return "\n".join(parts)
    return str(content)
