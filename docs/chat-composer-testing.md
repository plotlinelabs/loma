# Chat composer attachment regression checks

## Scope and reproduction

The previous `/api/chat` rejects a genuinely empty image-only prompt with HTTP 400 before inspecting files. The new regression suite fails on that baseline and passes with this change. Previously, queued files were staged back into React state but an override-message closure did not read them; images on the server were temporary files deleted after a turn.

The reported "chat box won't scroll" failure was reproduced on the Tasks board quick-add composer ("What do you need done?"): it used `overflow: hidden` with a 120px cap, so a long prompt was clipped and mouse-wheel scrolling moved it 0px; only arrow keys reached the top. It now uses `overflow-y: auto`. The main chat composers already scrolled in local Chromium on `main`; This change makes composer sizing deterministic (`field-sizing: fixed`, 160px cap), adds overflow containment, and checks actual mouse-wheel movement with long drafts on desktop and mobile-sized viewports. It is not a claim of iOS Safari/device validation.

## Automated tests

Backend:

```bash
.venv/bin/python -m pytest tests/test_chat_composer.py -q
```

Dashboard:

```bash
cd dashboard
node node_modules/typescript/bin/tsc --noEmit --incremental false
node node_modules/eslint/bin/eslint.js src/components/ChatPanel.tsx src/lib/chatDrafts.ts src/lib/api.ts
```

Browser tests require an isolated local Loma backend/dashboard and a Playwright storage-state file from a real local login. Do not point the suite at production. Install Playwright separately if not available; it is not added to production dependencies.

```bash
CHAT_AUTH_STATE=/tmp/chat-auth.json \
CHAT_BASE_URL=http://localhost:13001 \
PLAYWRIGHT_MODULE=/path/to/playwright \
CHROMIUM_PATH=/path/to/chromium \
node dashboard/tests/chat-composer.e2e.cjs
```

The suite boots a real Chromium browser using the logged-in local dashboard, but intercepts chat SSE and conversation lookups for deterministic tests. No live AI response is asserted. It checks image-only payload bytes, queued delivery to the same conversation, preservation of unrelated drafts, reload recovery, HTTP-rejection recovery, and desktop/mobile wheel scrolling. Screenshots are written to `/tmp/chat-*.png`.

A separate manual integration check used only a throwaway `loma_local_chat_*` Mongo database: upload an image, close the DB client, open a new client and reuse it, then verify another user/conversation cannot retrieve it and expired entries are excluded.

## Storage and lifecycle

- Unsent text/files: IndexedDB `loma-chat-drafts`, scoped by signed-in email and conversation/task. No public asset URL. Drafts older than seven days are pruned when a draft is opened. Browser storage failures show a dismissible warning; sending still works.
- Sent images: new `chat_images` Mongo collection. Each document stores binary bytes, name, MIME type, owner email, conversation ID, content key, creation time and expiration time. `_id` hashes owner/conversation/content for idempotent writes.
- The backend creates an `expires_at` TTL index and an owner/conversation/creation-time lookup index. Images expire seven days after upload. Reads enforce expiration even before Mongo's TTL sweep; deleting a conversation clears its image cache.
- Each upload is bounded to 10 files, 10 MB per file and 40 MB total. Follow-up context rehydrates at most ten recent files within a bounded payload, with current uploads taking precedence. Older images may require reattachment. Rehydrating recent images can increase model input cost.
- Cache writes happen after conversation access checks and before starting an agent. Cache outages return a retryable HTTP error rather than leaving an observer running. Sent image thumbnails are not restored to historical UI messages by this change; model reuse works through the server cache.
- This is a short-lived input cache, not an asset library or cross-conversation sharing feature. Browser-local drafts do not sync between devices; queued in-flight messages are not a durable server outbox.
