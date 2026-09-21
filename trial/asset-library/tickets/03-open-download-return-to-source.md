# 03: Open, download, return to source

**What to build:** From the Library, a user selects an Asset to preview it in the detail
pane, downloads it, and returns to the conversation that produced it. The **primary**
action resumes that conversation in Chat; a **secondary** action opens it read-only. Bytes
are served by the existing, already-owner-checked serve path — no new serving code.

Read first: `trial/asset-library/DESIGN.md` (UX). Reuse the existing artifact viewer for
preview, the existing file-URL helper for download, and the existing resume-in-chat
deep-link (`/chat?continue={conversation_id}`) and read-only conversation route
(`/conversations/{id}`).

**Blocked by:** 02 (Library list endpoint + page).

**Status:** ready-for-agent

- [ ] Selecting an Asset previews it in the detail pane via the reused viewer (documents
      preview inline; images fall through to download — a logged, deliberate gap).
- [ ] Downloading an Asset goes through the existing serve endpoint; no new serve path is
      introduced.
- [ ] The primary action deep-links to resume the source conversation in Chat; a secondary
      action opens the read-only conversation view.
- [ ] A user cannot open or download another user's file even given its identifier — the
      serve path fails closed.

**Testing plan:**

- Backend — a second user requesting the first user's file via the existing serve endpoint
  is refused (reuse the existing serve/containment tests; assert the not-found response).
- Frontend — for a selected Asset, the resume and view-conversation links carry the right
  `conversation_id`; preview routing chooses the viewer by mime type; the download control
  targets the serve URL for the Asset's `file_id`.
