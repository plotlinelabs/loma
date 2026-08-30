# loma-tasks MCP server

A stateless Streamable-HTTP MCP server that exposes each user's Loma **task
board** to external agents (e.g. a personal assistant like Hermes). It runs as
a sidecar next to the Loma backend and proxies to the existing `/api/tasks`
REST routes, so all board logic and owner-scoping stay in one place.

## How auth works

1. A user mints a personal key in the dashboard (**Settings → API Keys**,
   `/settings/api-keys`). The full key (`loma_sk_…`) is shown once; only its
   SHA-256 hash is stored in the `api_keys` Mongo collection.
2. The external agent connects to `https://<loma-host>/mcp/tasks` with
   `Authorization: Bearer loma_sk_…`.
3. This server hashes the key, looks it up in Mongo (revoked keys fail), and
   forwards the request to the backend with a trusted `X-User-Email` header —
   the same identity mechanism nginx uses for dashboard sessions. Each caller
   can therefore only ever see and modify **their own** tasks.

The backend port (3000) is never exposed publicly; only nginx and this sidecar
can reach it on the internal Docker network.

## Tools

| Tool | What it does |
|------|--------------|
| `list_tasks` | The user's board: lanes, tags, counts, tasks with derived columns. Optional `q` search and `column` filter. |
| `get_task` | One task in detail: board state, prompt, final response, last messages. |
| `create_task` | Stage a draft in a lane, or `start=true` to fire the Loma agent immediately. Supports priority/deadline. |
| `update_task` | Move between columns (todo/active/done), retitle, edit draft prompt, set/clear priority and deadline. |

## Connecting a client

Streamable HTTP endpoint (stateless JSON — no session management needed):

```json
{
  "mcpServers": {
    "loma-tasks": {
      "type": "http",
      "url": "https://<loma-host>/mcp/tasks",
      "headers": { "Authorization": "Bearer loma_sk_..." }
    }
  }
}
```

Smoke test with curl:

```bash
curl -s https://<loma-host>/mcp/tasks \
  -H "Authorization: Bearer loma_sk_..." \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Configuration (env)

| Var | Default | Purpose |
|-----|---------|---------|
| `OBSERVABILITY_MONGODB_URI` | — (required) | Same Mongo cluster as the backend (for key lookups). |
| `OBSERVABILITY_DB_NAME` | `loma_observability` | Database name. |
| `BACKEND_URL` | `http://loma-backend:3000` | Loma backend base URL. |
| `MCP_PORT` | `3002` | Listen port. |

## Local run

```bash
OBSERVABILITY_MONGODB_URI=mongodb://... BACKEND_URL=http://localhost:3000 \
  python mcp-servers/loma-tasks/server.py
```
