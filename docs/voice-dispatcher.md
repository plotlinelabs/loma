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
  While a task it is watching still runs, the quiet limit is held off so the
  finish can be announced; the twenty minute limit always applies.
  These are UX safeguards, not server-enforced spending limits.

## Runtime

`QuickAddTask` -> `useVoiceDispatcher` -> authenticated `/api/voice/session` ->
OpenAI `/v1/live/sessions` -> WebRTC media/data channel. The server sends its API
key to OpenAI, never to the browser. GPT-Live delegates to a Responses model with
seven allowlisted task tools. The browser executes those through existing
session-authenticated task routes; existing board and conversation access checks
remain authoritative. Tool output goes back to the delegated model, then voice.

- List, create, inspect, message, stop, move and open tasks on the currently selected board.
- `move_task` marks a task done, reopens a done task, or puts it in a named lane, with
  the same `PATCH /api/tasks/{id}` the board uses for drag and drop. The board's rules
  hold (`components/tasks/transitions.ts`): a draft that never ran cannot be marked
  done, a running task cannot be parked, and Working / Needs input are never
  destinations except reopening a done task. A blocked move returns its reason to be spoken.
- `open_task` opens the task in the desktop chat drawer while voice keeps running. Phones
  have no drawer, so it leaves a tap-to-open link in the panel and says so.
- Finish announcements: while voice is on, the hook checks the board every four seconds.
  A task it saw in Working that stops (run finished, failed, needs input, or marked done)
  is announced with `session.commentary.append`, which the voice model says in its own
  words. It waits until nobody is talking and no request is in flight, groups several
  finishes into one line, and announces each task once. Tasks voice itself stopped or
  moved are not announced. With voice off, the existing push alerts are the only signal.
- An announcement quotes up to 280 characters of the task's reply, stripped of links,
  code and markdown. That text is untrusted: the voice prompt treats it as information
  to pass on, and every tool that changes work still needs a clear request from the user.
- Drafts use the backend's default first staging lane after Needs input.
- Requests run immediately unless explicitly saved for later; running tasks display
  only in Working. The current Model and Tools selections carry into new tasks.
- Ambiguous titles ask for clarification; duplicate call IDs execute once per session.
- Audio interruptions do not cancel already-dispatched tasks. Stop is a separate tool.
- Transcripts/actions are currently session-local, with clickable task links.

## Explicitly not complete

This is the core prototype, not the complete rollout. Persistent dispatcher history
(the pinned Voice chat), card-board entry points,
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

This browser test uses real GPT-Live signaling with a synthetic microphone and typed
requests: it saves a draft, starts a real task, waits for the finish to be announced and
spoken, opens the task and marks it done (`live-flow.json`). A WebRTC double then covers
the deterministic checks, including lane moves, blocked moves and a faked run finish. Task CRUD
still uses the isolated backend. It is not proof of audible quality or barge-in.
The normal `scripts/browser/chat-smoke.cjs` checks chat persistence and follow-ups.

## API references

- https://developers.openai.com/api/docs/guides/live
- https://developers.openai.com/api/docs/guides/live-delegation
