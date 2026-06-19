"""Google Stitch AI mockup generation tool.

Generates mobile/desktop UI mockups from text prompts using Google Stitch
(Google Labs). Produces full HTML + screenshot for each screen.

API: MCP (Model Context Protocol) over HTTP — JSON-RPC 2.0 at https://stitch.googleapis.com/mcp
Auth: API key passed as ?key= query parameter

Commands:
  1. stitch.py list-projects
     List all projects in the account.

  2. stitch.py create-project --title "My App"
     Create a new project.

  3. stitch.py get-project --id PROJECT_ID
     Get project details.

  4. stitch.py list-screens --project-id PROJECT_ID
     List screens in a project.

  5. stitch.py get-screen --project-id PROJECT_ID --screen-id SCREEN_ID
     Get screen details (HTML + image URLs).

  6. stitch.py generate --project-id PROJECT_ID --prompt "..." [--device MOBILE|DESKTOP] [--model GEMINI_3_FLASH|GEMINI_3_1_PRO]
     Generate a screen from a text prompt.

  7. stitch.py edit --project-id PROJECT_ID --screen-ids SID1,SID2 --prompt "..."
     Edit existing screens with a text prompt.

  8. stitch.py variants --project-id PROJECT_ID --screen-ids SID1 --prompt "..." [--count 3] [--range EXPLORE]
     Generate design variants.

  9. stitch.py download --url URL --output /tmp/screen.html
     Download a file (HTML or image) from a Stitch URL.

Usage (called by the agent via Bash):
  python3 tools/stitch.py list-projects
  python3 tools/stitch.py create-project --title "Login Flow"
  python3 tools/stitch.py generate --project-id PID --prompt "A modern login page with email and Google SSO"
  python3 tools/stitch.py get-screen --project-id PID --screen-id SID
  python3 tools/stitch.py download --url "https://..." --output /tmp/screen.png
"""

import asyncio
import json
import os
import sys
import uuid

import aiohttp

STITCH_MCP_URL = "https://stitch.googleapis.com/mcp"
DEFAULT_TIMEOUT = 300  # 5 minutes — generation can take 30-60s


# -- MCP JSON-RPC helpers ------------------------------------------------------

def _get_api_key() -> str:
    """Get the Stitch API key from environment."""
    key = os.environ.get("STITCH_API_KEY", "")
    if not key:
        print(json.dumps({"error": "STITCH_API_KEY environment variable not set."}))
        sys.exit(1)
    return key


async def _mcp_call(method: str, params: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Make an MCP JSON-RPC 2.0 call to the Stitch API."""
    api_key = _get_api_key()
    url = f"{STITCH_MCP_URL}?key={api_key}"

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
        "params": params,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return {"error": f"HTTP {resp.status}: {text[:500]}"}
                data = await resp.json()
                if "error" in data:
                    return {"error": data["error"]}
                return data.get("result", data)
    except aiohttp.ClientError as e:
        return {"error": f"Connection failed: {str(e)}"}
    except asyncio.TimeoutError:
        return {"error": f"Request timed out after {timeout}s. Screen generation can take 30-60s — try again."}


async def _mcp_tool_call(tool_name: str, arguments: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Call an MCP tool via tools/call method."""
    result = await _mcp_call(
        method="tools/call",
        params={"name": tool_name, "arguments": arguments},
        timeout=timeout,
    )

    # Extract the text content from MCP tool response
    if isinstance(result, dict) and "content" in result:
        contents = result["content"]
        if isinstance(contents, list):
            for item in contents:
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        return json.loads(item["text"])
                    except (json.JSONDecodeError, TypeError):
                        return {"text": item["text"]}
            return {"content": contents}
    return result


# -- Commands ------------------------------------------------------------------

async def list_projects() -> dict:
    """List all projects."""
    return await _mcp_tool_call("list_projects", {})


async def create_project(title: str) -> dict:
    """Create a new project."""
    return await _mcp_tool_call("create_project", {"title": title})


async def get_project(project_id: str) -> dict:
    """Get project details."""
    return await _mcp_tool_call("get_project", {"projectId": _strip_prefix(project_id, "projects/")})


async def list_screens(project_id: str) -> dict:
    """List screens in a project."""
    return await _mcp_tool_call("list_screens", {"projectId": _strip_prefix(project_id, "projects/")})


async def get_screen(project_id: str, screen_id: str) -> dict:
    """Get screen details including HTML and image URLs."""
    return await _mcp_tool_call("get_screen", {
        "projectId": _strip_prefix(project_id, "projects/"),
        "screenId": _strip_prefix(screen_id, "screens/"),
    })


def _strip_prefix(resource_id: str, prefix: str) -> str:
    """Strip resource prefix (e.g., 'projects/' or 'screens/') from an ID if present."""
    if resource_id.startswith(prefix):
        return resource_id[len(prefix):]
    return resource_id


async def generate_screen(
    project_id: str,
    prompt: str,
    device: str = "MOBILE",
    model: str = "GEMINI_3_FLASH",
) -> dict:
    """Generate a screen from a text prompt."""
    args = {
        "projectId": _strip_prefix(project_id, "projects/"),
        "prompt": prompt,
        "deviceType": device,
        "modelId": model,
    }
    return await _mcp_tool_call("generate_screen_from_text", args, timeout=DEFAULT_TIMEOUT)


async def edit_screens(
    project_id: str,
    screen_ids: list,
    prompt: str,
) -> dict:
    """Edit existing screens with a text prompt."""
    return await _mcp_tool_call("edit_screens", {
        "projectId": _strip_prefix(project_id, "projects/"),
        "screenIds": [_strip_prefix(sid, "screens/") for sid in screen_ids],
        "prompt": prompt,
    }, timeout=DEFAULT_TIMEOUT)


async def generate_variants(
    project_id: str,
    screen_ids: list,
    prompt: str,
    count: int = 3,
    variation_range: str = "BALANCED",
) -> dict:
    """Generate design variants of existing screens."""
    return await _mcp_tool_call("generate_variants", {
        "projectId": _strip_prefix(project_id, "projects/"),
        "selectedScreenIds": [_strip_prefix(sid, "screens/") for sid in screen_ids],
        "prompt": prompt,
        "variantOptions": {
            "variantCount": count,
            "creativeRange": variation_range,
        },
    }, timeout=DEFAULT_TIMEOUT)


async def download_file(url: str, output_path: str) -> dict:
    """Download a file (HTML or image) from a Stitch URL.

    For screenshot URLs from lh3.googleusercontent.com, appends '=s0' to
    request the original full-resolution image instead of Google's default
    downscaled thumbnail.
    """
    # Google's image CDN (lh3.googleusercontent.com) serves downscaled thumbnails
    # by default. Appending '=s0' requests the original full-resolution image.
    download_url = url
    if "lh3.googleusercontent.com" in url and "=s" not in url:
        download_url = url + "=s0"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                download_url,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    return {"error": f"Download failed — HTTP {resp.status}"}
                data = await resp.read()
                with open(output_path, "wb") as f:
                    f.write(data)
                return {
                    "success": True,
                    "output": output_path,
                    "size_bytes": len(data),
                    "content_type": resp.headers.get("Content-Type", "unknown"),
                }
    except aiohttp.ClientError as e:
        return {"error": f"Download failed: {str(e)}"}


# -- CLI entry point -----------------------------------------------------------

def _parse_flag(args: list, flag: str, default: str = "") -> str:
    """Extract a --flag value from args list."""
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return default


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    args = sys.argv[1:]
    if not args:
        print(json.dumps({
            "error": (
                "Usage: python3 tools/stitch.py <command> [flags]\n"
                "Commands: list-projects, create-project, get-project, list-screens, "
                "get-screen, generate, edit, variants, download"
            )
        }))
        sys.exit(1)

    command = args[0]

    if command == "list-projects":
        result = asyncio.run(list_projects())

    elif command == "create-project":
        title = _parse_flag(args, "--title", "Untitled Project")
        result = asyncio.run(create_project(title))

    elif command == "get-project":
        pid = _parse_flag(args, "--id")
        if not pid:
            result = {"error": "Missing required flag: --id"}
        else:
            result = asyncio.run(get_project(pid))

    elif command == "list-screens":
        pid = _parse_flag(args, "--project-id")
        if not pid:
            result = {"error": "Missing required flag: --project-id"}
        else:
            result = asyncio.run(list_screens(pid))

    elif command == "get-screen":
        pid = _parse_flag(args, "--project-id")
        sid = _parse_flag(args, "--screen-id")
        if not pid or not sid:
            result = {"error": "Missing required flags: --project-id and --screen-id"}
        else:
            result = asyncio.run(get_screen(pid, sid))

    elif command == "generate":
        pid = _parse_flag(args, "--project-id")
        prompt = _parse_flag(args, "--prompt")
        device = _parse_flag(args, "--device", "MOBILE")
        model = _parse_flag(args, "--model", "GEMINI_3_FLASH")
        if not pid:
            result = {"error": "Missing required flag: --project-id"}
        elif not prompt:
            result = {"error": "Missing required flag: --prompt"}
        else:
            result = asyncio.run(generate_screen(pid, prompt, device.upper(), model.upper()))

    elif command == "edit":
        pid = _parse_flag(args, "--project-id")
        sids = _parse_flag(args, "--screen-ids")
        prompt = _parse_flag(args, "--prompt")
        if not pid or not sids or not prompt:
            result = {"error": "Missing required flags: --project-id, --screen-ids, --prompt"}
        else:
            screen_ids = [s.strip() for s in sids.split(",")]
            result = asyncio.run(edit_screens(pid, screen_ids, prompt))

    elif command == "variants":
        pid = _parse_flag(args, "--project-id")
        sids = _parse_flag(args, "--screen-ids")
        prompt = _parse_flag(args, "--prompt")
        count = int(_parse_flag(args, "--count", "3"))
        vrange = _parse_flag(args, "--range", "BALANCED")
        if not pid or not sids or not prompt:
            result = {"error": "Missing required flags: --project-id, --screen-ids, --prompt"}
        else:
            screen_ids = [s.strip() for s in sids.split(",")]
            result = asyncio.run(generate_variants(pid, screen_ids, prompt, count, vrange.upper()))

    elif command == "download":
        url = _parse_flag(args, "--url")
        output = _parse_flag(args, "--output", "/tmp/stitch_download")
        if not url:
            result = {"error": "Missing required flag: --url"}
        else:
            result = asyncio.run(download_file(url, output))

    else:
        result = {
            "error": (
                f"Unknown command: {command}. "
                "Use: list-projects, create-project, get-project, list-screens, "
                "get-screen, generate, edit, variants, download"
            )
        }

    print(json.dumps(result, indent=2))
