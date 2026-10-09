# Tasks-board voice dispatcher (prototype)

The existing text composer and dictation button stay available. The new waveform
button opens a GPT-Live WebRTC conversation above the composer. While voice is
active, typed text goes to the dispatcher instead of creating a task directly.
Attachments require ending voice first; the pending files are retained.

## Configuration

- Set `OPENAI_API_KEY` on the backend. Without it the voice button stays hidden.
- `LOMA_VOICE_MODEL` defaults to `gpt-live-1`.
- `LOMA_VOICE_BACKEND_MODEL` defaults to `gpt-6-luna`.
- Both model families must be available to the API project. Use HTTPS outside localhost.
- Audio is AI-generated and travels directly between the browser and OpenAI.
- Microphone capture begins only after clicking the voice button. Mute disables the
  microphone track. End, leaving the board, or changing boards releases it.
- The client ends sessions after three quiet minutes or twenty minutes total.
  These are UX safeguards, not server-enforced spending limits.

## Runtime

`QuickAddTask` -> `useVoiceDispatcher` -> authenticated `/api/voice/session` ->
OpenAI `/v1/live/sessions` -> WebRTC media/data channel. The server sends its API
key to OpenAI, never to the browser. GPT-Live delegates to a Responses model with
five allowlisted task tools. The browser executes those through existing
session-authenticated task routes; existing board and conversation access checks
remain authoritative. Tool output goes back to the delegated model, then voice.

- List, create, inspect, message and stop tasks on the currently selected board.
- Drafts use the backend's default first staging lane after Needs input.
- Requests run immediately unless explicitly saved for later; running tasks display
  only in Working. The current Model and Tools selections carry into new tasks.
- Ambiguous titles ask for clarification; duplicate call IDs execute once per session.
- Audio interruptions do not cancel already-dispatched tasks. Stop is a separate tool.
- Transcripts/actions are currently session-local, with clickable task links.

## Explicitly not complete

This is the core prototype, not the complete rollout. Persistent dispatcher history
(the pinned Voice chat), completion announcements, card-board entry points,
server-side usage/cost caps and device-level iOS PWA audio QA remain follow-ups.
No raw audio is stored by Loma. Real microphone quality, interruption timing and
emotional delivery require a human/device listening test before rollout.

## Verification

```bash
.venv/bin/python -m pytest tests/test_voice_routes.py tests/test_task_create.py tests/test_task_list.py tests/test_task_boards.py -q
cd dashboard && node --test tests/voice-dispatcher.test.cjs
```

Boot an isolated stack using the local-run procedure. With Playwright installed:

```bash
LOMA_VOICE_E2E=1 LOMA_VOICE_EVIDENCE=/tmp/voice-evidence \
  node scripts/browser/voice-dispatcher.cjs
```

This browser test uses real GPT-Live signaling and delegated draft creation with a
synthetic microphone, then a WebRTC double for deterministic UI checks. Task CRUD
still uses the isolated backend. It is not proof of audible quality or barge-in.
The normal `scripts/browser/chat-smoke.cjs` checks chat persistence and follow-ups.

## API references

- https://developers.openai.com/api/docs/guides/live
- https://developers.openai.com/api/docs/guides/live-delegation
