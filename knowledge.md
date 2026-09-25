# Loma Asset Library — working knowledge

This file is a harness for an LLM (or a human) picking up the Asset Library work trial. Prefer this over chat history. Do not treat it as product spec from Loma; it is local investigation plus the published brief.

**Last verified:** 2026-09-20 against local Docker Compose (`loma-backend` + dashboard on `localhost:3001`), OpenCode model `opencode-go/glm-5.3-flash`, Mongo DB `loma_observability`, user `sharma01ketan@gmail.com`.

---

## How to use this file

1. Read **Hard rules** before proposing code.
2. File-backed means `/api/files/{id}` bytes with an owner check — not a markdown path, data-URI, Drive link, or fenced CSV.
3. The library is a **find + navigate** layer on existing file handling. Not a new filesystem.
4. Commit `DESIGN.md` in a **trial-specific directory** before implementation. Do not overwrite `dashboard/DESIGN.md`.
5. Do not enable `LOMA_REMOTE_WORKERS`. The brief says leave it off; this machine cannot run that stack.
6. Do not patch core file delivery (`opencode_runtime`, seed APIs, broader path detection) to “make Chat work.” Use existing behavior + optional fixture.
7. Do not commit unless asked. Do not invent S3/R2/Drive as library storage.

---

## Hard rules (from the brief + local proof)

| Do | Do not |
|----|--------|
| Index file-backed outputs the current user may access | New storage service |
| Open / download via existing `/api/files/{id}` | Historical migration of old chats |
| Jump back to the source conversation | User uploads into the library |
| Enforce access on the **backend** | Public sharing, folders, new generation tools |
| Document limitations of ephemeral chips | Assume a chip surviving reload means Mongo has the file |
| Fixture script is explicitly allowed | Flip remote workers on for durable `worker-` IDs |
| Depth over breadth | Mini Dropbox |

Access: appearing in a UI list does not make a file safe. Same owner rules as `handle_serve_file`. Shared conversation ≠ shared files.

---

## What the assessment is

**Repo:** `plotlinelabs/loma`  
**Window:** 24 hours  
**Deliverable:** PR against `main` + tests + CI + screenshots + demo instructions. Loom optional.

**Problem:** Agents create reports, spreadsheets, images. Those files live in conversations. Finding one later means remembering which chat.

**Build:** An asset library — list outputs the user may see, open/download them, return to the conversation.

**Process they grade:**

1. `DESIGN.md` committed **before** feature code, trial-specific directory.
2. Working UI + backend, relevant tests, CI green.
3. `NOTES.md` at the end: what failed, what is weakest, what was skipped.
4. Be able to explain every line of the diff.

**Ready-to-build bar (already met locally):** sign in, open a sample file from a conversation. Chat samples or a fixture that calls `register_served_file` and records metadata.

**Their own warning (page 3):** some download registrations are **temporary** even when artifact metadata is saved. Do not replace that infrastructure. Explain it and recreate samples for the demo.

---

## Local environment (this machine)

| Piece | Value |
|-------|--------|
| Stack | Docker Compose via Colima |
| Dashboard | `http://localhost:3001` |
| Backend | `http://127.0.0.1:3000` (proxied as `/api` from the dashboard) |
| Nginx | `http://localhost:8080` |
| Auth | Local; Next.js middleware injects `X-User-Email` on `/api/*` |
| Default runtime | **OpenCode** inside `loma-backend` (same container, shared `/tmp`) |
| Claude | No accounts connected |
| Codex | Not used for these chats |
| Google / R2 | Not configured — Drive/CDN/Gmail uploads fail |
| Remote workers | Off (`LOMA_REMOTE_WORKERS` unset). **Leave off.** |
| Mongo | `OBSERVABILITY_DB_NAME=loma_observability` (brief example was `loma_trial`) |
| Served copies | Container path `/tmp/loma-served-files` |
| Host Downloads | `/Users/ketansharma/Downloads` — **browser saves, not Loma storage** |

OpenCode runs in the backend container, so `/tmp/opencode/…` and `/tmp/loma_*.pdf` are visible to `register_served_file` on the same filesystem. That is why local chips work when the model writes `/tmp` and mentions the path.

---

## Glossary

| Term | Meaning here |
|------|----------------|
| **File-backed output** | Real file copied into served storage and reachable at `/api/files/{id}` after an owner check |
| **Live file chip** | SSE `type: "file"` → `FileAttachmentCard` — download link, no right pane |
| **File artifact pane** | SSE `type: "file_artifact"` → `ArtifactCard` with `file_url` → right-hand viewer (PDF iframe, etc.) |
| **Code artifact** | SSE `type: "artifact"` — inline text/CSV/HTML in Mongo. **Not** file-backed |
| **Register** | `register_served_file(path, owner_email=…)` copies bytes, puts metadata in process RAM, returns `{file_id, url, name, mime_type, size}` |
| **Fixture** | Script that calls existing register + records metadata so the demo survives without a live Chat turn |

---

## Architecture — HLD

The browser never talks to OpenCode or Mongo. Chat posts to the dashboard origin. Next.js attaches identity and rewrites `/api/*` to Python. The backend runs the agent, streams SSE, and writes **conversation text** to Mongo. File **bytes** take a side path: copy + in-memory id. The chip is a second HTTP GET.

```text
Browser  /chat
   │  POST /api/chat  { message, conversation_id, model, files? }
   ▼
Next.js middleware     session → X-User-Email
   │  rewrite /api/* → loma-backend:3000
   ▼
handle_chat            ConversationObserver (Mongo: conversations, turns)
   │
   ├─ OpenCode (default OSS)  ──► write /tmp/foo.pdf
   │                              detect path in assistant TEXT
   │                              register_served_file
   │                              SSE { type: "file", url: "/api/files/{id}" }
   │                              chip NOT written to Mongo
   │
   ├─ Claude SDK (if selected) ─► same text chip
   │                              PLUS Write/Bash scan → SSE { type: "file_artifact" }
   │                              observer.record_artifact(artifact_type: "file")
   │
   ├─ Codex                    ─► text/tools only; NO file detection
   │
   └─ Isolated worker          ─► only if LOMA_REMOTE_WORKERS=on
                                  workspace.publish → durable file_artifact
                                  worker-{id}  (NOT this local stack)

GET /api/files/{id}
   ├─ uuid id     → RAM dict _served_files, owner_email must match
   └─ worker-*    → Mongo isolated_artifact_downloads + artifact dir
```

Two stores on the OpenCode path:

1. **Mongo** — prompt, messages, turns, **code** artifacts. Durable.
2. **Process RAM + `/tmp/loma-served-files`** — file chips. Ephemeral.

`~/Downloads` is the user’s disk after clicking a chip. The library must not treat Downloads as a source of truth.

---

## Architecture — LLD (code map)

### Identity and proxy

- `dashboard/src/middleware.ts` — session required; `X-User-Email` on API.
- `dashboard/next.config.ts` — `/api/*` (except NextAuth) → backend. SSE-safe.
- `dashboard/src/lib/api.ts` — `API_BASE` is same-origin (`basePath`). `getFileUrl(id)` → `/api/files/${id}`.

### Chat entry

- `dashboard/src/app/chat/page.tsx` — `?continue=<conversation_id>` loads history via `GET /api/conversations/:id`.
- `dashboard/src/components/ChatPanel.tsx` — streams events; renders chips and artifact cards.
- `api/routes.py` `handle_chat` — `include_steps=True`, `source="dashboard"`, `stream_agent(...)`.

### Runtime routing (`agent/client.py` `stream_agent`)

1. If `LOMA_REMOTE_WORKERS=on` → `isolation.entrypoint.remote_stream_agent` only. **No local fallback.**
2. Else if Codex model → `run_codex_agent` (no file path scan).
3. Else if non-Claude model (OpenCode default) → `run_opencode_agent`.
4. Else Claude SDK pool.

### Live chip (OpenCode + Claude text)

Detection: `agent/client.py` `_detect_file_paths`.

- Path must be under `/tmp/` or `/var/folders/`.
- File must **exist** and be a regular file at scan time (`Path.exists()` on the **backend** FS).
- Extensions: pdf, png, jpg, jpeg, gif, svg, csv, xlsx, xls, docx, pptx, zip, tar, gz, tgz, json, html, txt, md, mp4, mp3, wav.
- **Bare path** (no “saved to”) only matches: pdf, png, jpg, jpeg, gif, svg, csv, xlsx, xls, docx, pptx, zip, tar, gz.
- json / html / txt / md / mp4 / mp3 / wav need a phrase: `saved to`, `available at`, `written to`, `created at`, `generated at`, `exported to`, `the file is at`, `file at`, `uploaded to`, `file:`.

OpenCode emit: `agent/opencode_runtime.py` `_emit_text`.

**Order matters.** `_detect_artifacts` (fenced code) runs **first**. If it hits, the function **returns** and never scans files. That is why a ` ```csv ` block becomes a code artifact and kills the chip.

Claude text emit: same `_detect_file_paths` in `agent/client.py` around the assistant text branch.

Register: `api/routes.py` `register_served_file`.

- Requires non-empty `owner_email`.
- `uuid4().hex` as `file_id`.
- `shutil.copy2` → `{SERVED_FILES_DIR}/{file_id}{ext}`.
- RAM: `_served_files[file_id] = {path, owner_email, original_name, mime_type, size}`.
- Returned URL is always `/api/files/{file_id}` (relative).

Serve: `GET /api/files/{file_id}` (`api/file_routes.py` → `handle_serve_file`).

- 401 without user email.
- Unknown or other-owner → **404** (no existence leak; no admin bypass).
- `worker-` prefix → durable worker store, not the RAM dict.
- Inline vs attachment from `_INLINE_MIME_TYPES` (pdf, images, html, txt, csv).
- **No list route.** `GET /api/files` and `GET /api/files/` → 405.

UI chip: `FileAttachmentCard` — `<a href={basePath + file.url} download>`. Not a button. Does not open the artifact pane.

History: `rebuildItemsFromConversation` restores **Mongo artifacts** only. It does **not** restore `fileAttachments`. Reopen chat → path in prose, no chip.

OpenCode does **not** call `observer.record_artifact` for chips.

### File artifact pane (Claude tool scan)

- `_FILE_ARTIFACT_EXTENSIONS`: pdf, docx, pptx, xlsx, xls, doc, ppt only.
- Trigger: dashboard + `include_steps` + Write or Bash tool result, file exists under `/tmp/`.
- Yields `type: "file_artifact"`; `observer.record_artifact(artifact_type: "file", file_url, …)`.
- UI: `ArtifactCard` + `onArtifactOpen` → `ArtifactViewer` (PDF iframe, docx, pptx, or download).
- Persisted in Mongo `artifacts`. Restored on reload **as a card**, but `file_url` still hits the same ephemeral `/api/files/{id}` unless it is a `worker-` id.
- OpenCode **never** emits this. Local Claude accounts = none → pane path is off here.
- PDF skill (`seed/skills/pdf/SKILL.md`) describes preview-in-pane; on OpenCode the same `/tmp/*.pdf` only produces a **chip**.

### Code artifacts (not in library scope)

- Fenced blocks over ~200 chars or 8 lines, language in `_ARTIFACT_LANGUAGES` (includes `csv`).
- Mongo `artifact_type: "code"`, inline `content`, no `file_url`.
- UI: button like “CSV Code · 11 lines · 462 chars”.

### Isolated workers (out of local scope)

- Flag `LOMA_REMOTE_WORKERS=on` routes **every** agent run remotely; local CLIs disabled.
- File delivery is **only** `workspace.publish` → `isolation/downloads.py` `DownloadRegistry`.
- No regex, no `/tmp` scan. Comment in code: “Emission is idempotent by artifact_id.”
- Durable Mongo `isolated_artifact_downloads`, TTL ~30 days, ids `worker-{artifact_id}`.
- Needs dedicated gVisor host, pinned images, mTLS, `LOMA_WORKER_ARTIFACT_DIR`. App Dockerfile is not a worker image.
- Brief: *leave remote workers off.* Do not enable for the trial.

### Incoming attachments (not outputs)

Paperclip uploads are base64 on the chat request, dumped to temp for the model, deleted when the turn ends. They do not go through `register_served_file`. Library should not index them unless the brief is reinterpreted (it should not).

### External hosting (not Loma file-backed)

Google Drive, Gmail, R2/`cdn_upload`, markdown `data:` URIs. The model often does this when the user says “download link.” Loma’s path is: write `/tmp`, mention the path, backend registers. The assessment Chat prompt failed for that reason, not missing product code.

---

## Data model (what exists today)

### Mongo `conversations`

Identity, owner (`metadata.user_name`), prompt, messages, title, status, cost. **No `file_id`.**

### Mongo `turns`

Tool calls/results and text blocks. Paths may appear in text. That is not a registry.

### Mongo `artifacts`

Upsert on `(conversation_id, artifact_id)`.

| Field | Code artifact | File artifact (Claude / worker) |
|-------|---------------|----------------------------------|
| `artifact_type` | `code` | `file` |
| `content` | yes | empty |
| `file_url` | absent | `/api/files/…` |
| `file_size` / `file_type` | absent | yes |

Local proof (2026-09-20): **one** artifact row — `0eaec484-…` title `CSV Code`, type `code`, `file_url` null. None of the 1–12 OpenCode chips were inserted.

### RAM `_served_files`

```text
file_id → { path, owner_email, original_name, mime_type, size }
```

Lost on backend restart. Disk copies under `/tmp/loma-served-files` may remain but are **not** served without the dict (no path-based recovery — code comment).

### Worker downloads (not present locally)

`isolated_artifact_downloads`: `_id`, `owner`, `conversation_id`, `metadata`, `expires_at`.

---

## Data flow — happy path (what 1–12 used)

```text
User: “Write /tmp/loma_q3_sales.pdf … Final line: The file is saved to /tmp/loma_q3_sales.pdf”
  → POST /api/chat  source=dashboard  OpenCode GLM
  → model bash/python writes the PDF in the container
  → assistant text contains the phrase + path
  → _detect_artifacts: no qualifying fence
  → _detect_file_paths: path exists, extension allowed
  → register_served_file(owner=user email)
  → SSE file event
  → FileAttachmentCard
  → GET /api/files/{id}  (auth cookie / X-User-Email)
  → browser download → ~/Downloads/loma_q3_sales.pdf

Mongo: conversation text only.
Library today: nothing to query except that text.
```

### Failure modes (observed)

| Symptom | Cause |
|---------|--------|
| Spreadsheet chat = “CSV Code” button, path `/app/….csv` | Code fence wins; `/app` not in regex |
| Assessment “return a download link” = Drive/data-URI | Model seeks a hosted URL; integrations unset |
| Image chat: path in text, no chip after reload | Chip never persisted; registry/session |
| Dual PDF+CSV: files in served-files + Downloads, **no chips on reopen** | History rebuild drops `fileAttachments` |
| Mermaid still showed chip in the same tab | Live React state from that turn, not Mongo |
| Zip registered, `.txt` not | Required final line mentioned the zip; txt phrase/path did not match group 1 |
| `GET /api/files` → 405 | No index API |
| Other user’s GET → 404 | Owner check |

---

## What is possible vs not (local OpenCode)

**Possible (live chip, if path + existence + no stealing fence):**  
pdf, png, jpg, jpeg, gif, svg, csv, xlsx, xls, docx, pptx, zip, tar, gz; and with a phrase: json, html, txt, md, mp4, mp3, wav. Multiple files in one turn if both paths match.

**Not possible here without other runtimes:**

- File artifact pane (needs Claude Write/Bash scan or workers).
- Durable `/api/files/worker-*`.
- Paths under `/app`, workspace root, `/home`.
- Unsupported extensions.
- Codex file chips.
- Slack/flow sources (`source != "dashboard"` skips scanners).
- Listing files via HTTP (no endpoint).
- Surviving backend restart without a fixture/metadata layer **you add**.

**In scope for the library:** whatever you can still open through existing handling **and** can authorize. Practically: register at chip time (or fixture) + store **pointers** (conversation_id, owner, file_id, name, mime, size). Reuse GET `/api/files/{id}`. Do not copy to a new blob store.

---

## Local evidence (prompts 1–12, 2026-09-20)

Prompts that work: write a real file under `/tmp`, mention it with a matching phrase, **no** large fenced block, **no** Drive. Stay on the page to see the chip; history will not restore it.

| Prompt | Conversation id | Served copy (container) | Downloads | Notes |
|--------|-----------------|-------------------------|-----------|--------|
| PDF `/tmp/loma_q3_sales.pdf` | `cfd76980-3420-48e9-b682-5f21971ae904` | `915d287c….pdf` | `loma_q3_sales.pdf` | Chip live; gone on history |
| PNG chart | `0e68b588-6ebc-4629-b6fd-563f9e6505cb` | `cb3122dc….png` | `loma_chart.png` | |
| JPEG | `dba33eaa-422c-4151-ab1e-ed0f22fe20a0` | `4445a73e….jpg` | `loma_photo.jpg` | |
| SVG | `02f8a2cd-c8b1-4d7d-9a31-f6c5f37e307f` | `9dcc147c….svg` | `loma_badge.svg` | |
| CSV 10 conditions | `851524a5-74e0-4c43-bffe-f024cc59df83` | `10a46c79….csv` | `box2_4_capacity_performance.csv` | Contrast old `/app` + fence chat |
| XLSX | `921913b5-790a-42b4-9568-93b8d2e11b9b` | `047892a0….xlsx` | `loma_capacity.xlsx` | |
| DOCX | `b69f07f3-ceef-4384-9b2c-27e72d49952f` (+ retry `ffe30dc6-…`) | two `.docx` | **not** in Downloads | Retry went off-script into a PPTX |
| PPTX | (deck from the off-script Word turn) | `9dd05fa5….pptx` | `loma_deck.pptx` | |
| Zip | `37868c3c-95af-4046-9081-44d4819a027a` | `c0f58798….zip` only | `loma_bundle.zip` | No `.txt` in served-files |
| JSON | `90c27cd7-82ee-44ae-8312-09a86363bff9` | `9d076882….json` | `loma_meta.json` | Phrase required |
| HTML | `0692ed05-ccc8-4cf6-8fa1-41f680bd6bd7` | `d191e57e….html` | `loma_page.html` | Phrase required |
| Dual pdf+csv | `8d96c872-6b3c-483b-a93d-a8dece0e0b46` | both ids | both files | **No chips after reopen** |
| Mermaid PNG | `40760e75-2adb-4cb2-814d-a85799be9b17` | `e54b68c7….png` | `diagram.png` | Chip URL `http://localhost:3001/api/files/e54b68c7d4144a03a9d3562dc9f55e30` while the live tab still held state |

**Older chats (why the assessment prompt “failed”):**

| Title | Id | What it was |
|-------|-----|-------------|
| Create a small PDF report using | `11d17d75-abaf-4835-a08c-bafb2bead3fc` | Model chased Drive/R2; markdown **data-URI** “Download PDF”; a chip also registered (`68d54c64….pdf`) when `/tmp/opencode/pdfreport/quarterly_report.pdf` was mentioned |
| make me a spreadsheet of this | `0eaec484-1974-4c96-960c-0d59a1978bfe` | **Code artifact** `CSV Code`; path `/app/box2_4_….csv` — not file-backed |
| ``` 5 Automatic Zoom… | `60810fad-dd80-4106-9971-1b9d5c6411c7` | PNG at `/tmp/opencode/heat_workers_summary.png`; chip `411ed201….png` was fetchable while the process still had the dict; UI after reload showed prose only |

Fetch pattern (authenticated dashboard session): `http://localhost:3001/api/files/{id}`. Direct backend needs `X-User-Email`. There is no collection URL.

Conversation JSON: `GET /api/conversations/{id}` includes `artifacts` (code/file-artifact rows), **not** live chips.

---

## How to begin the assessment (recommended sequence)

You are past setup. Next is design, then a thin slice, then notes.

### 1. DESIGN.md (commit this first)

Put it in a trial-specific directory, e.g. `trial/asset-library/DESIGN.md` (name is yours; must not replace `dashboard/DESIGN.md`).

State:

- **What we index:** file-backed outputs (chips + Mongo `artifact_type: "file"` if present). Not code artifacts, not Drive URLs, not uploads.
- **Why metadata must be recorded:** `_served_files` is RAM; history does not restore chips. Without a pointer (conversation_id, owner, file_id, name, mime, created_at) the library is empty after refresh — we proved that with dual vs mermaid.
- **How we record:** hook next to `register_served_file` / file SSE (and existing `record_artifact` for file artifacts). Do not replace serving.
- **How we serve:** existing `GET /api/files/{id}` plus owner check on any **list** API. List must filter by the authenticated user on the backend.
- **UX:** one library surface in the dashboard (nav + list + preview/download + link to `/chat?continue={id}`). Reuse `ArtifactViewer` / chip preview patterns where cheap. Polish is secondary.
- **Fixture:** script that registers dummy files via `register_served_file` and writes the same metadata the library reads. Demo instructions: run fixture, then open library. Chat samples optional.
- **Out of scope:** remote workers, new blob store, folders, sharing, migration of historical chips that were never recorded.
- **Limitation to document:** files still die when the process/dict dies unless the fixture is re-run; we are not asked to replace that.

### 2. Implementation sketch (after DESIGN is committed)

- Backend: collection or documents for library items; `GET` list (authz); reuse serve endpoint.
- Frontend: page/route consistent with existing dashboard.
- Tests: owner cannot see another user’s item; missing file → safe 404; list empty vs populated; fixture documented.
- Do not broaden `_detect_file_paths` to `/app` as the product fix; that is out of trial spirit unless DESIGN argues a tiny helper for the fixture only.

### 3. NOTES.md last

Answer their three questions honestly: ephemeral registry, OpenCode vs pane, skipped workers/Drive, fixture as the reproducible demo.

---

## Prompts that produce chips (OpenCode)

Use these if Chat samples are needed. They beat Drive, `/app`, and fenced-code theft.

```
Create a 1-page PDF at /tmp/loma_q3_sales.pdf … Do not upload … Do not wrap in a code fence.
In your final reply write exactly: The file is saved to /tmp/loma_q3_sales.pdf
```

Same pattern for png/jpg/svg/csv/xlsx/docx/pptx/zip. For json/html/txt use the phrase `saved to` / `generated at`, not a bare path.

Do **not** use the brief’s “return a download link” wording unless you also force `/tmp` + the phrase — the model will hunt Drive.

---

## Key files

| Path | Role |
|------|------|
| `api/routes.py` | `register_served_file`, `handle_serve_file`, `handle_chat` |
| `api/file_routes.py` | `GET /api/files/{file_id}` only |
| `agent/client.py` | `_detect_file_paths`, `_detect_file_artifact`, Claude emit, runtime routing |
| `agent/opencode_runtime.py` | `_emit_text` — artifacts first, then files |
| `observability/observer.py` | `record_artifact` |
| `dashboard/src/components/ChatPanel.tsx` | chips, artifact cards, history rebuild |
| `dashboard/src/components/ArtifactCard.tsx` | pane trigger; `file_url` ⇒ file artifact |
| `dashboard/src/components/ArtifactViewer.tsx` | PDF/docx/pptx/file download renderers |
| `isolation/downloads.py` | durable worker `file_artifact` |
| `seed/skills/pdf/SKILL.md` | `/tmp` + mention path (pane language; OpenCode still chips) |
| `docs/security-containment/remote-workers.md` | why workers stay off |

---

## Open product decisions (for DESIGN.md, not for debate in code first)

1. Persist library rows at **register** time vs only when Mongo already has `file` artifacts (the latter is empty on OpenCode).
2. Library row after file expired: show “unavailable, recreate via fixture/Chat” vs hide.
3. One row per file_id vs per conversation; dual pdf+csv is two assets, one conversation.
4. Preview in library vs deep-link to chat only. Brief allows either; reuse existing preview if cheap.
5. Fixture-only demo vs Chat-generated samples in the PR description. Fixture is more reliable for reviewers.

Default recommendation from this investigation: **record at register**, list for owner only, reuse `/api/files/{id}`, fixture for CI/demo, Chat samples as optional screenshots, do not enable workers.
