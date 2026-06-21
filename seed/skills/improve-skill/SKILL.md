---
name: improve-skill
description: Skill creation and improvement playbook — use when creating a new skill, improving an existing skill, or migrating curl-based skills to tool-based patterns
user-invocable: false
---

# Skill Creation & Improvement Playbook

This skill is the definitive guide for creating new Loma skills and improving existing ones. It codifies the patterns from the best skills and explicitly calls out anti-patterns to avoid.

---

> **Cross-references to related skills:**
> - For MCP server development patterns (Python FastMCP, TypeScript SDK), load the **mcp-builder** skill.

> **CRITICAL: How Loma Skills Are Stored & Edited**
>
> Loma skills live in **MongoDB**, not in a git repo. There is no clone/branch/PR flow. Every skill is a `slug` with a `SKILL.md` (YAML frontmatter plus body) and optional extra files and binary assets. All reads and writes go through the first-party CLI:
>
> ```bash
> python3 tools/loma_skills.py <subcommand> [flags]
> ```
>
> Each write is versioned automatically in the database, so there is no separate deployment step — a successful `create`, `import-dir`, or `update-file` is live.
>
> **CLI subcommands:**
>
> | Subcommand | Purpose |
> |---|---|
> | `list` | List all skills |
> | `search --query "<q>"` | Search skills |
> | `get --slug <slug>` | Fetch a skill (SKILL.md + metadata) |
> | `file --slug <slug> --path <path>` | Fetch one file's content |
> | `asset --slug <slug> --path <path>` | Fetch a binary asset |
> | `create --slug <slug> --skill-md <file> --user-email <email> --auth-token <token>` | Create a new skill |
> | `import-dir --source <dir> --user-email <email> --auth-token <token>` | Import a skill directory (SKILL.md + scripts/assets) |
> | `update-file --slug <slug> --path SKILL.md --content-file <file> --user-email <email> --auth-token <token>` | Update or add a file |
>
> Write subcommands (`create`, `import-dir`, `update-file`) require `--user-email` and `--auth-token`. Read subcommands (`list`, `search`, `get`, `file`, `asset`) do not.

---

## When to Use

Load this skill when:
- Asked to create a brand new skill for Loma
- Asked to improve or refactor an existing skill
- Asked to migrate a curl-based skill to a tool-based pattern
- Asked to add a new external API integration
- Discussing how skills work or best practices for structuring them

---

## 1. Skill Anatomy

### Directory Structure

When authoring a skill locally before importing it, lay it out as a directory:

```
<skill-name>/
  SKILL.md          # Required. The skill definition.
  scripts/          # Optional. Helper scripts.
  assets/           # Optional. Binary assets (images, templates).
```

You import this directory into the database with `import-dir`. A skill can also be created from a single SKILL.md file with `create`, then have additional files added with `update-file`.

### YAML Frontmatter (required at top of SKILL.md)

```yaml
---
name: <skill-name>              # Must match the slug
description: <1-line> — use when <trigger conditions>  # Used for trigger matching
tags: [<optional>, <tags>]      # Optional
user-invocable: false           # true only if users can invoke via a /command
---
```

The `description` field is critical — it determines when the skill loads. Be specific about trigger conditions.

**Good descriptions:**
- `"Pylon support ticket API integration — use when fetching ticket messages, posting internal notes, or handling Pylon webhook-triggered support issues"`
- `"Database debugging workflows for MongoDB and ClickHouse — use when investigating customer data, campaign states, events, SDK issues"`

**Bad descriptions:**
- `"Pylon tool"` — too vague, trigger matching will fail
- `"Handles billing"` — doesn't specify what triggers loading

### Required Sections in SKILL.md

Every skill should have these sections (adapt as needed):

1. **Title** (H1) — concise name
2. **When to Use** — explicit trigger conditions (bulleted list)
3. **Tool Reference** — which tool/MCP server to use, how to invoke it
4. **Commands / API Reference** — one subsection per command with exact bash invocation
5. **Common Workflows** — step-by-step playbooks for typical use cases
6. **Error Handling** — what errors look like and how to handle them
7. **Safety / Guardrails** — what the agent must never do

---

## 2. Decision Tree: Choose Your Tool Type

When creating or improving a skill, use this decision tree:

### Does the skill need to call an external API?

**No** → Create a **pure-workflow skill** (no tool needed).
- The SKILL.md contains step-by-step playbooks and decision trees.
- Examples: `self-improvement`, `bug-triage`, `feature-request-triage`, `campaign-visibility`

**Yes** → Continue below.

### Is there already a configured MCP server for this service?

Check `.mcp.json` for existing servers: MongoDB, ClickHouse, GitHub, Linear, Notion, Athena, GitLab.

**Yes** → Reference the existing MCP tools in your SKILL.md.
- Example: `debugging` uses MongoDB and ClickHouse MCP servers
- Example: `code-review` uses GitHub MCP tools

**No** → **Create a CLI tool in `tools/<service>.py`** (this is the default for all new API integrations).

### Rules

- **ALWAYS prefer CLI tools** for new API integrations. This is the standard pattern.
- **NEVER add new MCP servers** to `.mcp.json`. MCP servers are managed via deployment config and not added through skills.
- **NEVER embed raw curl commands in SKILL.md.** This is the single most important rule. See Section 6 for why.

---

## 3. Creating a CLI Tool

When a skill needs to call an external API, create a Python CLI tool in `tools/`. Follow the established pattern from the best existing tools.

### Reference Implementations

Study these before writing a new tool:
- `tools/pylon.py` — best overall example (manual flag parsing, stdin for HTML, clean structure)
- `tools/gmail.py` — argparse-based alternative (good for tools with many flags per command)
- `tools/dataroom.py` — similar to pylon, many subcommands
- `tools/apollo.py` — argparse with subparsers

### Tool File Structure

Every CLI tool follows this structure:

```python
"""<Service> API client.

Provides CLI commands for the agent:
  1. <tool>.py <command1> <args>     — Description
  2. <tool>.py <command2> <args>     — Description
  3. echo '<body>' | <tool>.py <command3> <args>  — Description (stdin)

Requires <ENV_VAR> environment variable.

Usage (called by the agent via Bash):
  python3 tools/<tool>.py <command1> arg1
  python3 tools/<tool>.py <command2> --flag value
"""

import asyncio
import json
import os
import sys
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://api.service.com"


# --- Configuration helpers ---

def _get_api_key() -> str:
    key = os.environ.get("SERVICE_API_KEY", "")
    if not key:
        raise ValueError(
            "SERVICE_API_KEY environment variable is not set. "
            "Please configure it before using this tool."
        )
    return key


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
        "Accept": "*/*",
    }


# --- Shared HTTP helpers ---

async def _api_get(path: str) -> dict[str, Any]:
    """GET helper. Returns parsed JSON or {"error": "..."}."""
    url = f"{BASE_URL}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 401:
                    return {"error": "API key is invalid or expired."}
                if resp.status == 404:
                    return {"error": f"Not found: {path}"}
                if resp.status == 429:
                    return {"error": "Rate limit reached. Try again shortly."}
                if resp.status != 200:
                    text = await resp.text()
                    return {"error": f"API error (HTTP {resp.status}): {text[:500]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to API: {e}"}


async def _api_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST helper. Returns parsed JSON or {"error": "..."}."""
    url = f"{BASE_URL}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=_headers(), json=body,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    return {"error": f"API error (HTTP {resp.status}): {text[:500]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to API: {e}"}


# --- Public async functions (one per API operation) ---

async def search_items(query: str) -> dict[str, Any]:
    return await _api_get(f"/items?search={query}")


async def get_item(item_id: str) -> dict[str, Any]:
    return await _api_get(f"/items/{item_id}")


# --- CLI entry point ---

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    args = sys.argv[1:]
    if not args:
        print(json.dumps({"error": "Usage: python3 tools/<tool>.py <command> [args]"}))
        sys.exit(1)

    command = args[0]

    if command == "search":
        query = args[1] if len(args) > 1 else ""
        result = asyncio.run(search_items(query))
    elif command == "get":
        item_id = args[1] if len(args) > 1 else ""
        result = asyncio.run(get_item(item_id))
    else:
        result = {"error": f"Unknown command: {command}"}

    print(json.dumps(result, indent=2))
```

### Local Development & Testing Workflow

**NEVER ship a CLI tool without testing it first.** Follow this workflow:

1. **Research the official API documentation.** Use `WebSearch` and `WebFetch` to find and read the **official API documentation** from the service's website. Look for: auth method, base URL, endpoint paths, request/response formats, pagination, rate limits, error formats. Do NOT look at existing SKILL.md files yet — they may have wrong API patterns.

2. **STOP — Ask yourself: "Do I have usable API docs?"** This is a mandatory checkpoint. You MUST send a message to the user before continuing. There are only two valid outcomes for this step:

   **If you GOT usable docs** (you can see endpoint paths, auth format, response shapes from an official source): Tell the user what you found and proceed to step 3.

   **If you DID NOT get usable docs** (for ANY reason — login wall, empty page, search found nothing, WebFetch returned junk, docs lack detail): You MUST send this message to the user and WAIT for their response:

   *"I couldn't get usable API documentation from the web. Could you either share the exact docs URL or paste the relevant API reference here? I don't want to proceed by guessing."*

   STOP. WAIT. Do not continue until the user replies. The user will either: (a) give you a URL, (b) paste docs, or (c) say to proceed without docs.

   **This step is ONLY complete when you have sent a message AND received a user response.** You cannot complete this step by reasoning about it internally. The following are NOT substitutes for official docs and do NOT count as "having usable docs":
   - The existing SKILL.md (even if it says "validated" or "tested" — it's the thing you're fixing)
   - Trial-and-error against the live API
   - Guessing based on similar APIs
   - Any source other than official vendor documentation or user-provided documentation

3. **Write the tool file** in a local working directory, e.g. `tools/<service>.py`.

4. **Source environment variables** from the project's `.env`:
   ```bash
   source /path/to/project/.env
   ```
   Check the existing `.env` for required variables first. Only ask the user for variables that are **not already present** in `.env`. If new env vars are needed, ask the user for the values and export them for the test session.

5. **Syntax check:**
   ```bash
   python3 -m py_compile tools/<service>.py
   ```

6. **Test each subcommand** with real data:
   ```bash
   python3 tools/<service>.py <command1> <test-args>
   python3 tools/<service>.py <command2> <test-args>
   ```
   Verify:
   - JSON output on success (valid JSON, expected fields)
   - `{"error": "..."}` output on expected failures (invalid ID, missing auth)
   - No crashes or unhandled exceptions

7. **Iterate and fix** — if any command fails, fix the tool and re-test. Repeat until all commands work.

8. **Only after all tests pass** — package the tool with its SKILL.md and import the skill into the database (see Section 9).

### Key Patterns

1. **JSON output always.** Every command prints JSON to stdout. Success returns the API response; failure returns `{"error": "description"}`. Never print unstructured text.

2. **Stdin for long content.** When a command accepts HTML or multi-line text, read from stdin. This avoids shell escaping nightmares. Example from `pylon.py`:
   ```bash
   cat <<'EOF' | python3 tools/pylon.py note ISSUE_ID
   <p>This is an internal note with <b>HTML</b> formatting.</p>
   EOF
   ```

3. **Environment variables for secrets.** All API keys, tokens, and base URLs come from env vars. Never hardcode them in the tool or the SKILL.md.

4. **Async internals, sync CLI.** Functions are `async def` internally (using `aiohttp`), called via `asyncio.run()` from the CLI entry point.

5. **Dual-purpose design.** Public async functions can be imported by other Python modules (e.g., webhook handlers in the server code), not just called from CLI.

6. **Structured error handling.** Every HTTP helper handles status codes explicitly (401, 404, 429, etc.) and catches `aiohttp.ClientError`. Never let exceptions crash the tool — return `{"error": "..."}`.

### Auth Patterns

Choose the pattern that matches the API:

- **API key in header:** `_get_api_key()` + `_headers()` (like `pylon.py`)
- **Custom header name:** e.g., `x-api-key: TOKEN` instead of `Authorization: Bearer`
- **OAuth with refresh:** Create a helper that handles token refresh internally. See `tools/_google_auth.py` for the pattern — tokens stored in MongoDB, auto-refreshed when expired. The caller never deals with token management.
- **User-scoped auth:** Add `--auth-token` and `--user-email` flags, verify via `tools/_auth_token.py` (like `gmail.py`, `google_calendar.py`)

---

## 4. Writing the SKILL.md

### Template: Tool-Based Skill

```markdown
---
name: <name>
description: <description> — use when <trigger conditions>
user-invocable: false
---

# <Title>

<1-2 sentence overview.>

**Tool**: `tools/<name>.py` — Python CLI called via Bash
**Auth**: `<ENV_VAR>` environment variable (loaded automatically by the tool)

---

## When to Use

- <trigger condition 1>
- <trigger condition 2>

---

## Commands

### <Command Name>

\`\`\`bash
python3 tools/<name>.py <command> [FLAGS]
\`\`\`

<Description of what this does. Document the response shape.>

### <Another Command>

\`\`\`bash
cat <<'EOF' | python3 tools/<name>.py <command> ARGS
<body content here>
EOF
\`\`\`

---

## Common Workflows

### <Workflow Name>

1. <Step 1 — which command to run>
2. <Step 2 — what to do with the result>
3. <Step 3 — next action>

---

## Error Handling

- **401**: API key invalid or expired — check `<ENV_VAR>`
- **404**: Resource not found
- **429**: Rate limit — wait and retry

---

## Safety / Guardrails

- <Rule 1: e.g., "Read-only — never create, update, or delete resources">
- <Rule 2: e.g., "Always confirm with user before sending external communications">
```

### Template: Workflow-Only Skill

```markdown
---
name: <name>
description: <description> — use when <trigger conditions>
user-invocable: false
---

# <Title> Playbook

<1-2 sentence overview.>

---

## When to Use

- <trigger condition 1>
- <trigger condition 2>

---

## Step 1: <First Step>

<Instructions, decision points, what tools/MCP servers to query.>

## Step 2: <Second Step>

<Instructions...>

---

## Edge Cases

- <Edge case 1>
- <Edge case 2>
```

---

## 5. Using Existing MCP Servers

Skills may reference MCP servers that are already configured in `.mcp.json`:

| Server | Use For |
|--------|---------|
| MongoDB | Customer data, product configs, campaign state |
| ClickHouse | Analytics, events, campaign metrics |
| GitHub | Code search, PR operations, file reads/writes |
| Linear | Issue tracking, project management |
| Notion | Internal runbooks, operational docs |
| Athena | API request/response logs |
| Docs search | Product/help documentation lookups |

**NEVER add new MCP servers to `.mcp.json`.** MCP servers are managed via deployment configuration. If you need a new API integration, create a CLI tool instead.

When referencing MCP tools in a SKILL.md, use the tool name directly with the relevant arguments, for example a GitHub file read with `owner`, `repo`, and `path`.

---

## 6. Anti-Patterns — What NOT to Do

### Anti-Pattern 1: Raw curl commands in SKILL.md

This is the **most common and most damaging** mistake. The `monetize-now` and `zoho-books` skills both embed raw curl commands, causing frequent agent errors.

**Bad — from `monetize-now/SKILL.md`:**
```bash
# Agent must construct the full curl each time, getting headers wrong frequently
curl -s -H "x-api-key: $MONETIZE_NOW_API_KEY" \
  "$MONETIZE_NOW_BASE_URL/accounts?search=CUSTOMER_NAME"
```

Problems observed:
- Agent uses `Authorization: Bearer` instead of `x-api-key` (returns 401)
- Agent doubles the URL path: `$MONETIZE_NOW_BASE_URL/api/accounts` → `/api/api/accounts` (returns 404)
- Every curl block repeats the same headers, multiplying error surface

**Bad — from `zoho-books/SKILL.md`:**
```bash
# Two-step OAuth dance that must be manually performed every time
# Step 1: Refresh token
curl -s -X POST "https://accounts.zoho.in/oauth/v2/token" \
  -d "refresh_token=$ZOHO_REFRESH_TOKEN_IN" \
  -d "client_id=$ZOHO_CLIENT_ID_IN" \
  -d "client_secret=$ZOHO_CLIENT_SECRET_IN" \
  -d "grant_type=refresh_token"

# Step 2: Use the token (agent must extract and paste it)
curl -s -H "Authorization: Zoho-oauthtoken ACCESS_TOKEN" \
  "https://books.zoho.in/api/v3/invoices?organization_id=$ZOHO_ORGANIZATION_ID_IN"
```

Problems observed:
- Agent forgets to refresh the token
- Agent uses the wrong region's credentials (India vs US)
- Token expires mid-session and agent doesn't notice
- Massive SKILL.md (300+ lines of curl templates)

**Correct approach:** Create `tools/monetize_now.py` and `tools/zoho_books.py` that handle auth, URLs, and errors internally. The SKILL.md then just documents clean CLI commands:
```bash
python3 tools/monetize_now.py search-accounts "CustomerName"
python3 tools/zoho_books.py list-invoices --region in --customer-id ZOHO_ID
```

### Anti-Pattern 2: Duplicating auth logic

Never repeat authentication setup in every command block. Put it in the tool's `_headers()` function once.

### Anti-Pattern 3: Hardcoding API URLs

Never hardcode URLs like `https://api.monetizeplatform.com/api`. Use env vars in the tool code, and reference the tool from SKILL.md.

### Anti-Pattern 4: Vague skill descriptions

The `description` in frontmatter drives trigger matching. Vague descriptions = skill never loads or loads incorrectly.

### Anti-Pattern 5: Missing error handling

Every skill that calls an external service must document error scenarios. Without this, the agent silently fails or hallucinates recovery steps.

### Anti-Pattern 6: Embedding secrets in SKILL.md

Never put actual API keys, tokens, or credentials in SKILL.md. Reference environment variable names only (e.g., `$SERVICE_API_KEY`).

### Anti-Pattern 7: Shipping untested CLI tools

Never import a CLI tool into the skills database without testing it first. This results in broken tools that fail at runtime. Always follow the local dev/test workflow (Section 3): write the tool, source env vars, test every subcommand, iterate until it works, THEN import.

---

## 7. Improving an Existing Skill — Migration Playbook

Use this step-by-step workflow to migrate a curl-based skill to a tool-based pattern.

### Step 1: Fetch Official API Documentation

**Before looking at the existing SKILL.md**, use `WebSearch` and `WebFetch` to find and read the **official API documentation** from the service's website. The official docs are your primary source of truth for: auth method/headers, base URL, endpoint paths, request/response formats, pagination, error codes.

### Step 2: STOP — Confirm You Have API Docs

This is a mandatory checkpoint. You MUST send a message to the user before continuing. There are only two valid outcomes for this step:

**If you GOT usable docs** (you can see endpoint paths, auth format, response shapes from an official source): Tell the user what you found and proceed to Step 3.

**If you DID NOT get usable docs** (for ANY reason — login wall, empty page, search found nothing, WebFetch returned junk, docs lack detail): You MUST send this message to the user and WAIT for their response:

*"I couldn't get usable API documentation from the web. Could you either share the exact docs URL or paste the relevant API reference here? I don't want to proceed by guessing."*

STOP. WAIT. Do not continue until the user replies. The user will either: (a) give you a URL, (b) paste docs, or (c) say to proceed without docs.

**This step is ONLY complete when you have sent a message AND received a user response.** You cannot complete this step by reasoning about it internally. The following are NOT substitutes for official docs and do NOT count as "having usable docs":
- The existing SKILL.md (even if it says "validated" or "tested" — it's the thing you're fixing)
- Trial-and-error against the live API
- Guessing based on similar APIs
- Any source other than official vendor documentation or user-provided documentation

### Step 3: Audit the Existing Skill

Fetch the current SKILL.md from the database:
```bash
python3 tools/loma_skills.py get --slug <skill-name>
```

To inspect a specific extra file in the skill:
```bash
python3 tools/loma_skills.py file --slug <skill-name> --path <path>
```

Catalog (cross-reference against the official API docs from Steps 1-2):
- Every curl command pattern (endpoints, HTTP methods) — note any that differ from official docs
- The auth mechanism — check if the SKILL.md has it right vs what the official docs say
- Any multi-step workflows (e.g., Zoho's token refresh → API call)
- Pagination patterns
- Business logic and workflows that must be preserved (these are the valuable parts)

### Step 4: Design the CLI Tool

Map each curl command to a CLI subcommand:

| Curl Pattern | CLI Subcommand |
|---|---|
| `GET /accounts?search=NAME` | `search-accounts NAME` |
| `GET /accounts/{id}` | `get-account ID` |
| `GET /contracts/{id}` | `get-contract ID` |
| `POST /items` + JSON body | `echo JSON \| create-item` |

Decide on:
- Argument parsing approach: manual `sys.argv` parsing (simpler, good for few flags) or `argparse` (better for many flags)
- Which env vars the tool needs
- Whether OAuth refresh should be automatic (yes — always handle it in the tool)

### Step 5: Create and Test the CLI Tool Locally

**Do NOT import untested code.** Follow the local development workflow from Section 3:

1. Write the tool following the template from Section 3, using the **official API docs from Steps 1-2** (not the existing SKILL.md) for auth headers, URL paths, and endpoint syntax.
2. Key design points:
   - Handle auth internally — the SKILL.md should never mention auth headers
   - Handle OAuth token refresh automatically if applicable
   - Handle region/environment switching via flags (e.g., `--region in|us` for Zoho)
   - Return structured JSON always

3. Source env vars from the project's existing `.env`. Check what's already available:
   ```bash
   grep -i "<SERVICE_NAME>" /path/to/project/.env
   ```
   Only ask the user for variables that are **not already present**. If new env vars are needed, note them — you'll tell the user to add them at the end.

4. Test every subcommand with real data:
   ```bash
   python3 tools/<service>.py <command1> <test-args>
   python3 tools/<service>.py <command2> <test-args>
   ```

5. Iterate and fix until all commands work correctly. Do not proceed to Step 6 until the tool is fully tested.

### Step 6: Rewrite the SKILL.md

- **Replace** all curl command blocks with `python3 tools/<service>.py` invocations
- **Remove** auth header documentation (handled by tool)
- **Remove** base URL documentation (handled by tool)
- **Remove** the "Standard Curl Template" section (no longer needed)
- **Keep** workflow/playbook sections and business logic (e.g. a billing-query workflow, a contact-lookup workflow, edge-case handling)
- **Keep** data model documentation and status value references
- **Keep** safety/guardrail rules
- **Add** an Error Handling section referencing tool output format

### Step 7: Update Settings if Needed

- `Bash(python3:*)` is already allowed in `settings.local.json`, so new `python3 tools/*.py` calls are covered
- If the tool introduces new pip dependencies, add them to `requirements.txt`

### Step 8: Import the Updated Skill

Once the tool is tested and working locally, update the skill in the database via the CLI (see Section 9).

If the tool requires new environment variables that weren't in `.env`, tell the user:
> "The new `tools/<service>.py` requires these environment variables that aren't in your `.env` yet: `SERVICE_API_KEY`, `SERVICE_BASE_URL`. Please add them before using the skill."

### Worked Example: Migrating `monetize-now`

Current curl patterns → proposed `tools/monetize_now.py` subcommands:

| Current Curl | New CLI Command |
|---|---|
| `GET /accounts?search=NAME` | `python3 tools/monetize_now.py search-accounts "NAME"` |
| `GET /accounts/{id}` | `python3 tools/monetize_now.py get-account ACCOUNT_ID` |
| `GET /contracts/{id}` | `python3 tools/monetize_now.py get-contract CONTRACT_ID` |
| `GET /contracts?status=ACTIVE` | `python3 tools/monetize_now.py list-contracts --status ACTIVE` |
| `GET /accounts/{id}/billGroups` | `python3 tools/monetize_now.py list-bill-groups ACCOUNT_ID` |
| `GET /.../billGroups/{bgId}/invoices` | `python3 tools/monetize_now.py list-invoices ACCOUNT_ID BILL_GROUP_ID` |
| `GET /accounts/{id}/subscriptions` | `python3 tools/monetize_now.py list-subscriptions ACCOUNT_ID` |
| `GET /accounts/{id}/payments` | `python3 tools/monetize_now.py list-payments ACCOUNT_ID` |
| `GET /accounts/{id}/credits` | `python3 tools/monetize_now.py list-credits ACCOUNT_ID` |
| `GET /accounts/{id}/creditNotes` | `python3 tools/monetize_now.py list-credit-notes ACCOUNT_ID` |

Auth: `x-api-key: $MONETIZE_NOW_API_KEY` handled internally in `_headers()`.
Base URL: `$MONETIZE_NOW_BASE_URL` read from env var in the tool.

### Worked Example: Migrating `zoho-books`

Key complexity: dual-region OAuth token refresh.

The tool should:
1. Accept `--region in|us` on every command
2. Internally call `_refresh_token(region)` before each API call
3. Cache the refreshed token for the duration of the CLI invocation
4. Map region → correct Zoho domain (`zoho.in` vs `zoho.com`), org ID env var, and OAuth credentials

Proposed `tools/zoho_books.py` subcommands:

| Current Curl | New CLI Command |
|---|---|
| Token refresh + `GET /contacts/{id}` | `python3 tools/zoho_books.py get-contact --region in CONTACT_ID` |
| Token refresh + `GET /contacts?contact_name=NAME` | `python3 tools/zoho_books.py search-contacts --region in --name "NAME"` |
| Token refresh + `GET /invoices?customer_id=ID` | `python3 tools/zoho_books.py list-invoices --region in --customer-id ID` |
| Token refresh + `GET /estimates?customer_id=ID` | `python3 tools/zoho_books.py list-estimates --region in --customer-id ID` |
| Token refresh + `POST /invoices/{id}/email` | `echo JSON \| python3 tools/zoho_books.py send-invoice --region in INVOICE_ID` |
| Token refresh + `GET /customerpayments?customer_id=ID` | `python3 tools/zoho_books.py list-payments --region in --customer-id ID` |
| Token refresh + `GET /creditnotes?customer_id=ID` | `python3 tools/zoho_books.py list-credit-notes --region in --customer-id ID` |

The agent never sees OAuth tokens, refresh flows, or region-specific URLs.

---

## 8. Registration & Permissions

### 8.1 Settings Permissions (`.claude/settings.local.json`)

- New `python3 tools/*.py` calls: already covered by existing `Bash(python3:*)` permission — no change needed
- New pip dependencies: add to `requirements.txt`

### 8.2 Trigger Matching

Skills are loaded on-demand based on the `description` in their frontmatter — there is no separate prompt-registration step. Make the `description` specific about trigger conditions (see Section 1) so the skill loads at the right times.

---

## 9. Saving the Skill to the Database

> **Prerequisites:** If the skill includes a CLI tool, it MUST be fully tested locally before this step (see Section 3 or Section 7 Step 5). Never import untested tools.

All writes go through `tools/loma_skills.py` and require `--user-email` and `--auth-token`. Each write is versioned automatically in the database; a successful command makes the change live.

### Creating a New Skill

**Option A — single SKILL.md file:**

```bash
python3 tools/loma_skills.py create \
  --slug <skill-name> \
  --skill-md /path/to/SKILL.md \
  --user-email <email> \
  --auth-token <token>
```

Then add any extra files (scripts, additional docs) one at a time:

```bash
python3 tools/loma_skills.py update-file \
  --slug <skill-name> \
  --path tools/<name>.py \
  --content-file /path/to/local/<name>.py \
  --user-email <email> \
  --auth-token <token>
```

**Option B — import a full skill directory** (SKILL.md + scripts/assets in one shot):

```bash
python3 tools/loma_skills.py import-dir \
  --source /path/to/<skill-name>/ \
  --user-email <email> \
  --auth-token <token>
```

### Updating an Existing Skill

Editing is per-file. Update the SKILL.md (or any other file) by pointing `--path` at the file within the skill and `--content-file` at your local edited copy:

```bash
python3 tools/loma_skills.py update-file \
  --slug <skill-name> \
  --path SKILL.md \
  --content-file /path/to/edited/SKILL.md \
  --user-email <email> \
  --auth-token <token>
```

Use the same command with a different `--path` to add or replace additional files.

### Verify the Change

Fetch the skill back to confirm the new content landed:

```bash
python3 tools/loma_skills.py get --slug <skill-name>
```

### Notify About New Environment Variables

If the new tool requires environment variables that are **not already in `.env`**, tell the user:

> "The new `tools/<service>.py` requires these environment variables that aren't in your `.env` yet: `VAR_1`, `VAR_2`. Please add them to the environment before using the skill."

---

## 10. Quick Reference — Existing Skills by Type

### Tool-Based Skills (CLI tool in `tools/`) — GOOD PATTERN

| Skill | Tool | Notes |
|-------|------|-------|
| `pylon-support` | `tools/pylon.py` | Best reference — manual flag parsing, stdin for HTML |
| `apollo` | `tools/apollo.py` | Argparse, many subcommands |
| `grain` | `tools/grain.py` | Simple subcommands |
| `slack-reader` | `tools/slack_reader.py` | Argparse with subparsers |
| `dataroom` | `tools/dataroom.py` | Manual flag parsing |
| `phantombuster` | `tools/phantombuster.py` | Simple CLI |
| `gmail` (personal) | `tools/gmail.py` | Argparse, user-scoped OAuth |

### MCP-Based Skills — GOOD PATTERN

| Skill | MCP Server(s) |
|-------|---------------|
| `debugging` | MongoDB, ClickHouse, Athena |
| `code-review` | GitHub |
| `improve-docs` | GitHub, Notion |
| `self-improvement` | GitHub |
| `implement-ticket` | Linear, GitHub |

### Workflow-Only Skills — GOOD PATTERN

| Skill | Purpose |
|-------|---------|
| `bug-triage` | Bug investigation playbook |
| `feature-request-triage` | Feature request analysis |
| `campaign-visibility` | Campaign debugging playbook |
| `integration-check` | SDK integration assessment |
| `database-reference` | Schema documentation |
| `infosec` | Security questionnaire filling |

### Curl-Based Skills — MIGRATE THESE

| Skill | Primary Issue | Migration Priority |
|-------|--------------|-------------------|
| `monetize-now` | Wrong auth headers, URL path doubling | High |
| `zoho-books` | Manual OAuth refresh, massive curl blocks | High |
